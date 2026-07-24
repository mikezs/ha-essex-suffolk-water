"""Tests for the ESW data update coordinator and statistics backfill."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.essex_suffolk_water.const import (
    BACKFILL_MAX_DAYS,
    DOMAIN,
    TIMEZONE,
)
from custom_components.essex_suffolk_water.coordinator import EswDataUpdateCoordinator
from eswater import Account, ApiError, InvalidAuth, Meter, ServiceUnavailable, UsageReading
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ACCOUNT_ID, INSTALLED, SERIAL, make_day_readings


def _build_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock
) -> EswDataUpdateCoordinator:
    entry.add_to_hass(hass)
    coordinator = EswDataUpdateCoordinator(hass, entry)
    coordinator.client = client
    # Disable auto-scheduling of the background backfill; tests drive it directly.
    coordinator._backfill_scheduled = True
    return coordinator


def _call_for(add_stats: MagicMock, suffix: str) -> tuple[dict, list[dict]]:
    """Return the (metadata, statistics) for the call whose id ends in suffix."""
    for call in add_stats.call_args_list:
        _hass, metadata, stats = call.args
        if metadata["statistic_id"].endswith(suffix):
            return metadata, stats
    raise AssertionError(f"no async_add_external_statistics call ending in {suffix}")


async def test_update_populates_sensors_without_stats(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """A regular poll derives sensor values but adds no stats before backfill."""
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    data = await coordinator._async_update_data()

    # Recent window is RECENT_DAYS+1 days (2026-07-12..15) of hourly calls.
    assert mock_client.get_usage.await_count == 4
    md = data[SERIAL]
    # Most recent day (07-15) totals 1+..+24 = 300 L and 0.01*(1+..+24) = 3.0.
    assert md.daily_consumption == 300.0
    assert md.daily_cost == pytest.approx(3.0)
    assert md.last_reading is not None
    # History not backfilled yet -> no statistics written.
    mock_stats["add_stats"].assert_not_called()


async def test_backfill_inserts_statistics(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """The one-time backfill walks history and writes usage + cost statistics."""
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    meter = mock_client.get_accounts.return_value[0].meters[0]

    await coordinator._async_backfill_history([meter])

    assert coordinator._history_ready is True
    assert mock_stats["add_stats"].call_count == 2

    usage_meta, usage_stats = _call_for(mock_stats["add_stats"], "_usage")
    assert usage_meta["statistic_id"] == f"{DOMAIN}:{ACCOUNT_ID}_{SERIAL}_usage".lower()
    assert usage_meta["unit_of_measurement"] == UnitOfVolume.LITERS
    assert len(usage_stats) == 72  # installed 07-13 .. 07-15 = 3 days x 24h
    assert usage_stats[0]["start"] == datetime(2026, 7, 13, tzinfo=TIMEZONE)
    assert usage_stats[-1]["sum"] == pytest.approx(900.0)  # 3 x 300

    _cost_meta, cost_stats = _call_for(mock_stats["add_stats"], "_cost")
    assert _cost_meta["unit_of_measurement"] == "GBP"
    assert cost_stats[-1]["sum"] == pytest.approx(9.0)


async def test_incremental_append_when_ready(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Once backfilled, a poll appends only hours after the last stored one."""
    last_start = datetime(2026, 7, 14, 23, tzinfo=TIMEZONE).timestamp()
    mock_stats["last_stats"].side_effect = lambda _hass, _n, sid, _c, _t: {
        sid: [{"start": last_start, "sum": 500.0}]
    }
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    coordinator._history_ready = True

    await coordinator._async_update_data()

    _usage_meta, usage_stats = _call_for(mock_stats["add_stats"], "_usage")
    # Recent window 07-12..07-15; only 07-15's 24 hours are after 07-14 23:00.
    assert len(usage_stats) == 24
    assert usage_stats[0]["start"] == datetime(2026, 7, 15, tzinfo=TIMEZONE)
    assert usage_stats[0]["sum"] == pytest.approx(501.0)  # 500 + 1


async def test_empty_days_tolerated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Empty hourly responses yield no sensor data and no statistics."""
    mock_client.get_usage = AsyncMock(return_value=[])
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    coordinator._history_ready = True

    data = await coordinator._async_update_data()

    assert data[SERIAL].daily_consumption is None
    mock_stats["add_stats"].assert_not_called()


async def test_backfill_scheduled_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """The first successful poll schedules the background backfill exactly once."""
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    coordinator._backfill_scheduled = False

    with patch.object(
        mock_config_entry,
        "async_create_background_task",
        side_effect=lambda _hass, coro, name=None: coro.close(),
    ) as create_task:
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    create_task.assert_called_once()
    assert coordinator._backfill_scheduled is True


async def test_service_unavailable_raises_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """A ServiceUnavailable from the client becomes UpdateFailed."""
    mock_client.get_accounts = AsyncMock(side_effect=ServiceUnavailable("boom"))
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_invalid_auth_raises_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """InvalidAuth triggers the reauth flow and clears the auth flag."""
    mock_client.authenticate = AsyncMock(side_effect=InvalidAuth("bad creds"))
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert coordinator._authenticated is False


async def test_dst_fall_back_hour_not_collapsed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """The autumn fall-back repeated 01:00-02:00 hour yields two distinct stats.

    On 2025-10-26 the UK clock goes 02:00 BST -> 01:00 GMT, so the 01:00-02:00
    wall hour occurs twice and both readings carry the same naive end-of-hour
    timestamp (02:00). Without fold handling they collapse onto one UTC hour and
    one is dropped, losing that hour's usage; with it, both hours are kept.
    """
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    meter = mock_client.get_accounts.return_value[0].meters[0]
    readings = [
        # 00:00-01:00 BST
        UsageReading(datetime(2025, 10, 26, 1, 0), 10.0, 0.1, 1),
        # 01:00-02:00 BST (first pass, fold 0)
        UsageReading(datetime(2025, 10, 26, 2, 0), 20.0, 0.2, 1),
        # 01:00-02:00 GMT (second pass, fold 1)
        UsageReading(datetime(2025, 10, 26, 2, 0), 30.0, 0.3, 1),
        # 02:00-03:00 GMT
        UsageReading(datetime(2025, 10, 26, 3, 0), 40.0, 0.4, 1),
    ]

    await coordinator._insert_statistics(meter, readings)

    _usage_meta, usage_stats = _call_for(mock_stats["add_stats"], "_usage")
    assert len(usage_stats) == 4
    starts = [s["start"] for s in usage_stats]
    # No two hours share a timestamp -> nothing was dropped as a duplicate.
    assert len({s.timestamp() for s in starts}) == 4
    # The two fold hours land exactly one UTC hour apart, in order.
    assert (starts[2].timestamp() - starts[1].timestamp()) == 3600
    # All four hours contribute to the cumulative sum.
    assert usage_stats[-1]["sum"] == pytest.approx(100.0)


async def test_multi_meter_multi_account(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Meters across multiple accounts each get their own data and statistics."""
    meter_a = Meter(serial="AAA111", account_id="ACC1", installed_date=INSTALLED)
    meter_b = Meter(serial="BBB222", account_id="ACC1", installed_date=INSTALLED)
    meter_c = Meter(serial="CCC333", account_id="ACC2", installed_date=INSTALLED)
    acc1 = Account(account_id="ACC1")
    acc1.meters = [meter_a, meter_b]
    acc2 = Account(account_id="ACC2")
    acc2.meters = [meter_c]

    client = AsyncMock()
    client.authenticate = AsyncMock()
    client.get_accounts = AsyncMock(return_value=[acc1, acc2])
    client.get_usage = AsyncMock(
        side_effect=lambda account_id, serial, start_date, granularity: (
            make_day_readings(start_date)
        )
    )
    coordinator = _build_coordinator(hass, mock_config_entry, client)
    coordinator._history_ready = True

    data = await coordinator._async_update_data()

    assert set(data) == {"AAA111", "BBB222", "CCC333"}
    ids = {
        meta["statistic_id"] for call in mock_stats["add_stats"].call_args_list
        for meta in [call.args[1]]
    }
    # Statistic ids namespace by account *and* serial, so nothing collides.
    assert f"{DOMAIN}:acc1_aaa111_usage" in ids
    assert f"{DOMAIN}:acc1_bbb222_usage" in ids
    assert f"{DOMAIN}:acc2_ccc333_usage" in ids


async def test_backfill_failure_still_readies_history(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed deep backfill logs a warning but still enables incremental stats."""
    mock_client.get_usage = AsyncMock(side_effect=ServiceUnavailable("portal down"))
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    meter = mock_client.get_accounts.return_value[0].meters[0]

    with caplog.at_level(logging.WARNING):
        await coordinator._async_backfill_history([meter])

    # Recovery path: history is marked ready so regular polls resume appending.
    assert coordinator._history_ready is True
    assert "did not complete" in caplog.text
    mock_stats["add_stats"].assert_not_called()


def test_start_day_resume_install_and_cap() -> None:
    """``_start_day`` resumes from stored data, else install date, else the cap."""
    end_day = date(2026, 7, 15)
    floor = end_day - timedelta(days=BACKFILL_MAX_DAYS)
    meter = Meter(serial=SERIAL, account_id=ACCOUNT_ID, installed_date=INSTALLED)

    # Resume point present -> continue from the stored hour's date.
    resume_ts = datetime(2026, 7, 10, 5, tzinfo=TIMEZONE).timestamp()
    assert EswDataUpdateCoordinator._start_day(resume_ts, meter, end_day) == date(
        2026, 7, 10
    )
    # No resume point -> start from the meter's install date.
    assert EswDataUpdateCoordinator._start_day(None, meter, end_day) == INSTALLED.date()
    # Nothing to anchor on, or a very old install -> clamp to the max-days floor.
    bare = Meter(serial=SERIAL, account_id=ACCOUNT_ID, installed_date=None)
    assert EswDataUpdateCoordinator._start_day(None, bare, end_day) == floor
    ancient = Meter(
        serial=SERIAL, account_id=ACCOUNT_ID, installed_date=datetime(2000, 1, 1)
    )
    assert EswDataUpdateCoordinator._start_day(None, ancient, end_day) == floor


async def test_api_error_on_one_day_skipped_backfill_completes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """An ApiError on one day (e.g. None payload) is skipped; backfill still completes."""
    call_count = 0

    def _side_effect(account_id, serial, start_date, granularity):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            msg = "Unexpected usage payload: None"
            raise ApiError(msg)
        return make_day_readings(start_date)

    mock_client.get_usage = AsyncMock(side_effect=_side_effect)
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    meter = mock_client.get_accounts.return_value[0].meters[0]

    await coordinator._async_backfill_history([meter])

    assert coordinator._history_ready is True
    # Days 2 and 3 still produced statistics despite day 1 failing.
    _usage_meta, usage_stats = _call_for(mock_stats["add_stats"], "_usage")
    assert len(usage_stats) == 48  # 2 good days x 24 h


async def test_cost_none_writes_usage_only(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Readings with no cost still produce usage stats but no cost stats."""
    mock_client.get_usage = AsyncMock(
        side_effect=lambda account_id, serial, start_date, granularity: (
            make_day_readings(start_date, with_cost=False)
        )
    )
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)
    meter = mock_client.get_accounts.return_value[0].meters[0]

    data = await coordinator._async_update_data()
    assert data[SERIAL].daily_consumption == 300.0
    assert data[SERIAL].daily_cost is None

    await coordinator._async_backfill_history([meter])
    ids = [
        call.args[1]["statistic_id"] for call in mock_stats["add_stats"].call_args_list
    ]
    assert any(i.endswith("_usage") for i in ids)
    assert not any(i.endswith("_cost") for i in ids)
