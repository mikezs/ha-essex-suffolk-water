"""Data update coordinator for Essex & Suffolk Water.

Every poll fetches a small trailing window of *hourly* data (the ESW/NWG API
only serves per-day history through the hourly endpoint, one call per day). That
window refreshes the live sensors and appends new long-term statistics so hourly
water usage and cost show up in the Energy dashboard.

The full historical backfill (from the meter's install date) is potentially
hundreds of daily calls, so it runs **once in a background task** rather than
blocking config-entry setup. Regular polls only append statistics once that
initial backfill has finished, which keeps the cumulative sums ordered.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from eswater import (
    ApiError,
    ESWaterClient,
    ESWaterError,
    Granularity,
    InvalidAuth,
    Meter,
    NotAuthenticated,
    UsageReading,
)
from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import (
    BACKFILL_MAX_DAYS,
    BACKFILL_THROTTLE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    RECENT_DAYS,
    SCAN_INTERVAL,
    TIMEZONE,
)

if TYPE_CHECKING:
    from . import EswConfigEntry

_LOGGER = logging.getLogger(__name__)

COST_UNIT = "GBP"


@dataclass(slots=True)
class MeterData:
    """Live snapshot of a meter, derived from the most recent day of hourly data."""

    meter: Meter
    daily_consumption: float | None
    daily_cost: float | None
    last_reading: datetime | None


class EswDataUpdateCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Poll ESW and feed long-term statistics into the recorder."""

    config_entry: EswConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EswConfigEntry) -> None:
        """Initialise the coordinator with a client bound to the HA session."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = ESWaterClient(
            async_get_clientsession(hass),
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
        self._authenticated = False
        # Serialises statistics writes so cumulative sums stay consistent
        # between the background backfill and regular incremental appends.
        self._stats_lock = asyncio.Lock()
        self._backfill_scheduled = False
        # Regular polls only append statistics once the one-time deep backfill
        # has finished, so the oldest-to-newest sum ordering is preserved.
        self._history_ready = False

    @property
    def history_ready(self) -> bool:
        """Whether the one-time deep backfill has finished (for diagnostics)."""
        return self._history_ready

    @property
    def backfill_scheduled(self) -> bool:
        """Whether the background backfill task has been scheduled."""
        return self._backfill_scheduled

    async def _async_update_data(self) -> dict[str, MeterData]:
        """Refresh live readings (and, once backfilled, append recent statistics)."""
        try:
            if not self._authenticated:
                await self.client.authenticate()
                self._authenticated = True
            accounts = await self.client.get_accounts()
            meters = [meter for account in accounts for meter in account.meters]

            end_day = self._today() - timedelta(days=1)
            recent_start = end_day - timedelta(days=RECENT_DAYS)

            data: dict[str, MeterData] = {}
            for meter in meters:
                readings = await self._fetch_hourly(meter, recent_start, end_day)
                data[meter.serial] = self._summarise(meter, readings)
                if self._history_ready:
                    async with self._stats_lock:
                        await self._insert_statistics(meter, readings)
        except (InvalidAuth, NotAuthenticated) as err:
            # Force a fresh login next cycle and hand off to the reauth flow.
            self._authenticated = False
            raise ConfigEntryAuthFailed(str(err)) from err
        except ESWaterError as err:
            # ServiceUnavailable, ApiError (incl. malformed responses), etc.
            raise UpdateFailed(str(err)) from err

        if not self._backfill_scheduled:
            self._backfill_scheduled = True
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_backfill_history(meters),
                name=f"{DOMAIN}_history_backfill",
            )

        return data

    # -- live sensor snapshot -------------------------------------------------

    def _summarise(self, meter: Meter, readings: list[UsageReading]) -> MeterData:
        """Aggregate the most recent day of hourly readings for the sensors."""
        if not readings:
            return MeterData(meter, None, None, None)

        litres: dict[date, float] = defaultdict(float)
        cost: dict[date, float] = defaultdict(float)
        has_cost: set[date] = set()
        latest: datetime | None = None
        for reading in readings:
            ts = reading.timestamp.replace(tzinfo=TIMEZONE)
            day = (ts - timedelta(hours=1)).date()  # hour-start day
            litres[day] += reading.consumption_litres
            if reading.cost is not None:
                cost[day] += reading.cost
                has_cost.add(day)
            if latest is None or ts > latest:
                latest = ts

        last_day = max(litres)
        return MeterData(
            meter=meter,
            daily_consumption=litres[last_day],
            daily_cost=cost[last_day] if last_day in has_cost else None,
            last_reading=latest,
        )

    # -- statistics -----------------------------------------------------------

    async def _async_backfill_history(self, meters: list[Meter]) -> None:
        """One-time deep backfill of hourly statistics from each meter's start."""
        try:
            end_day = self._today() - timedelta(days=1)
            for meter in meters:
                usage_id = self._statistic_id(meter, "usage")
                _, usage_last_ts = await self._resume_point(usage_id)
                start_day = self._start_day(usage_last_ts, meter, end_day)
                readings = await self._fetch_hourly(meter, start_day, end_day)
                async with self._stats_lock:
                    await self._insert_statistics(meter, readings)
        except ESWaterError as err:
            _LOGGER.warning(
                "Historical backfill did not complete (%s); recent statistics "
                "will still be collected. Reload the integration to retry.",
                err,
            )
        finally:
            # Let regular polls append recent statistics from here on, even if
            # the deep backfill was partial — resume logic picks up the rest.
            self._history_ready = True

    async def _insert_statistics(
        self, meter: Meter, readings: list[UsageReading]
    ) -> None:
        """Append usage and cost statistics for the given readings (resume-aware)."""
        if not readings:
            return
        usage_id = self._statistic_id(meter, "usage")
        cost_id = self._statistic_id(meter, "cost")

        usage_sum, usage_last_ts = await self._resume_point(usage_id)
        cost_sum, cost_last_ts = await self._resume_point(cost_id)

        usage_stats: list[StatisticData] = []
        cost_stats: list[StatisticData] = []
        for reading, start in self._hour_starts(readings):
            start_epoch = start.timestamp()

            if usage_last_ts is None or start_epoch > usage_last_ts:
                usage_sum += reading.consumption_litres
                usage_stats.append(
                    StatisticData(
                        start=start,
                        state=reading.consumption_litres,
                        sum=usage_sum,
                    )
                )
            if reading.cost is not None and (
                cost_last_ts is None or start_epoch > cost_last_ts
            ):
                cost_sum += reading.cost
                cost_stats.append(
                    StatisticData(start=start, state=reading.cost, sum=cost_sum)
                )

        if usage_stats:
            _LOGGER.debug("Adding %d usage statistics to %s", len(usage_stats), usage_id)
            async_add_external_statistics(
                self.hass, self._metadata(meter, usage_id, "usage"), usage_stats
            )
        if cost_stats:
            _LOGGER.debug("Adding %d cost statistics to %s", len(cost_stats), cost_id)
            async_add_external_statistics(
                self.hass, self._metadata(meter, cost_id, "cost"), cost_stats
            )

    @staticmethod
    def _hour_starts(
        readings: list[UsageReading],
    ) -> Iterator[tuple[UsageReading, datetime]]:
        """Yield each reading with its tz-aware hour *start*, DST-fold aware.

        API timestamps are naive Europe/London and mark the *end* of the hour,
        so the hour start is ``timestamp - 1h``. On the autumn fall-back night
        the 01:00-02:00 wall-clock hour occurs twice and both readings carry the
        same naive timestamp; attaching the zone naively would collapse them
        onto one UTC hour (and one would be dropped as a duplicate, losing that
        hour's usage). Disambiguating with ``fold`` maps the first occurrence to
        the earlier UTC hour and the second to the later one, so both are kept.
        """
        seen: dict[datetime, int] = {}
        for reading in readings:
            naive_start = reading.timestamp - timedelta(hours=1)
            fold = seen.get(naive_start, 0)
            seen[naive_start] = fold + 1
            yield reading, naive_start.replace(tzinfo=TIMEZONE, fold=fold)

    async def _resume_point(self, statistic_id: str) -> tuple[float, float | None]:
        """Return ``(running_sum, last_hour_epoch)`` for an existing statistic.

        ``last_hour_epoch`` is ``None`` when nothing is stored yet (first run).
        """
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        rows = last.get(statistic_id) if last else None
        if not rows:
            return 0.0, None
        row = rows[0]
        return float(row.get("sum") or 0.0), float(row["start"])

    @staticmethod
    def _start_day(last_ts: float | None, meter: Meter, end_day: date) -> date:
        """Work out which day to begin fetching from."""
        floor = end_day - timedelta(days=BACKFILL_MAX_DAYS)
        if last_ts is not None:
            resumed = datetime.fromtimestamp(last_ts, tz=UTC).astimezone(TIMEZONE).date()
            return max(resumed, floor)
        if meter.installed_date is not None:
            return max(meter.installed_date.date(), floor)
        return floor

    async def _fetch_hourly(
        self, meter: Meter, start_day: date, end_day: date
    ) -> list[UsageReading]:
        """Fetch hourly readings day-by-day, throttled, tolerating empty days."""
        readings: list[UsageReading] = []
        day = start_day
        while day <= end_day:
            try:
                rows = await self.client.get_usage(
                    meter.account_id,
                    meter.serial,
                    # Naive local calendar day: the library serialises this as a
                    # bare "YYYY-MM-DDT00:00:00" the portal interprets as UK-local.
                    datetime(day.year, day.month, day.day),  # noqa: DTZ001
                    Granularity.HOURLY,
                )
            except ApiError as err:
                # The portal occasionally returns a None/malformed payload for
                # dates with no meter data (e.g. before the meter was active).
                # Skip the day rather than aborting the whole backfill.
                _LOGGER.debug("Skipping %s on %s: %s", meter.serial, day, err)
            else:
                if rows:
                    readings.extend(rows)
                else:
                    _LOGGER.debug("No hourly data for %s on %s", meter.serial, day)
            await asyncio.sleep(BACKFILL_THROTTLE)
            day += timedelta(days=1)
        readings.sort(key=lambda r: r.timestamp)
        return readings

    def _today(self) -> date:
        """Current date in the supplier's (UK) time zone."""
        return dt_util.now(TIMEZONE).date()

    @staticmethod
    def _statistic_id(meter: Meter, kind: str) -> str:
        return f"{DOMAIN}:{meter.account_id}_{meter.serial}_{kind}".lower()

    def _metadata(self, meter: Meter, statistic_id: str, kind: str) -> StatisticMetaData:
        """Build the external-statistics metadata for a usage/cost stream."""
        if kind == "cost":
            name = f"ESW {meter.serial} water cost"
            unit: str | None = COST_UNIT
            unit_class: str | None = None
        else:
            name = f"ESW {meter.serial} water usage"
            unit = UnitOfVolume.LITERS
            unit_class = VolumeConverter.UNIT_CLASS
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=name,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )
