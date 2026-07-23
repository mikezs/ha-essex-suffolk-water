"""Tests for the ESW data update coordinator and statistics backfill."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.essex_suffolk_water.const import DOMAIN, TIMEZONE
from custom_components.essex_suffolk_water.coordinator import EswDataUpdateCoordinator
from eswater import InvalidAuth, ServiceUnavailable
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ACCOUNT_ID, SERIAL


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
