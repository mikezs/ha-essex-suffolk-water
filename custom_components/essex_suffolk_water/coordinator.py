"""Data update coordinator for Essex & Suffolk Water.

Two responsibilities per poll:

1. Fetch each meter's most recent daily reading for the live sensors.
2. Backfill Home Assistant long-term statistics so hourly water usage (and
   cost) shows up in the Energy dashboard. Because the ESW/NWG API only serves
   per-day history through the *hourly* endpoint (one call per day), history is
   walked day-by-day, throttled, and resumed from whatever is already stored.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from eswater import (
    ESWaterClient,
    ESWaterError,
    Granularity,
    InvalidAuth,
    Meter,
    NotAuthenticated,
    UsageReading,
)
from homeassistant.components.recorder import get_instance
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
    SCAN_INTERVAL,
    TIMEZONE,
)

if TYPE_CHECKING:
    from . import EswConfigEntry

_LOGGER = logging.getLogger(__name__)

COST_UNIT = "GBP"


@dataclass(slots=True)
class MeterData:
    """Snapshot of a single meter for the live sensor platform."""

    meter: Meter
    latest: UsageReading | None


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

    async def _async_update_data(self) -> dict[str, MeterData]:
        """Refresh live readings and backfill statistics for every meter."""
        try:
            if not self._authenticated:
                await self.client.authenticate()
                self._authenticated = True
            accounts = await self.client.get_accounts()

            data: dict[str, MeterData] = {}
            for account in accounts:
                for meter in account.meters:
                    await self._insert_statistics(meter)
                    latest = await self.client.get_latest_reading(
                        meter.account_id, meter.serial
                    )
                    data[meter.serial] = MeterData(meter=meter, latest=latest)
        except (InvalidAuth, NotAuthenticated) as err:
            # Force a fresh login next cycle and hand off to the reauth flow.
            self._authenticated = False
            raise ConfigEntryAuthFailed(str(err)) from err
        except ESWaterError as err:
            # ServiceUnavailable, ApiError (incl. malformed responses), etc.
            raise UpdateFailed(str(err)) from err

        return data

    # -- statistics -----------------------------------------------------------

    async def _insert_statistics(self, meter: Meter) -> None:
        """Backfill/append hourly usage and cost statistics for one meter."""
        usage_id = f"{DOMAIN}:{meter.account_id}_{meter.serial}_usage".lower()
        cost_id = f"{DOMAIN}:{meter.account_id}_{meter.serial}_cost".lower()

        usage_sum, usage_last_ts = await self._resume_point(usage_id)
        cost_sum, cost_last_ts = await self._resume_point(cost_id)

        # The fetch window is driven by the usage stream (always present for an
        # active meter). Cost piggybacks on the same readings, which avoids
        # re-walking full history forever when a meter never reports cost.
        end_day = dt_util.now(TIMEZONE).date() - timedelta(days=1)
        start_day = self._start_day(usage_last_ts, meter, end_day)

        readings = await self._fetch_hourly(meter, start_day, end_day)

        usage_stats: list[StatisticData] = []
        cost_stats: list[StatisticData] = []
        for reading in readings:
            # API timestamps are naive Europe/London and mark the *end* of the
            # hour; HA statistics want a tz-aware hour *start*.
            start = reading.timestamp.replace(tzinfo=TIMEZONE) - timedelta(hours=1)
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
            rows = await self.client.get_usage(
                meter.account_id,
                meter.serial,
                datetime(day.year, day.month, day.day),
                Granularity.HOURLY,
            )
            if rows:
                readings.extend(rows)
            else:
                _LOGGER.debug("No hourly data for %s on %s", meter.serial, day)
            await asyncio.sleep(BACKFILL_THROTTLE)
            day += timedelta(days=1)
        readings.sort(key=lambda r: r.timestamp)
        return readings

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
