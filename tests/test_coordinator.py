"""Tests for the ESW data update coordinator and statistics backfill."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.essex_suffolk_water.const import DOMAIN, TIMEZONE
from custom_components.essex_suffolk_water.coordinator import EswDataUpdateCoordinator
from eswater import InvalidAuth, ServiceUnavailable
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ACCOUNT_ID, FIXED_NOW, SERIAL


def _build_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock
) -> EswDataUpdateCoordinator:
    entry.add_to_hass(hass)
    coordinator = EswDataUpdateCoordinator(hass, entry)
    coordinator.client = client
    return coordinator


def _call_for(add_stats: MagicMock, suffix: str) -> tuple[dict, list[dict]]:
    """Return the (metadata, statistics) for the call whose id ends in suffix."""
    for call in add_stats.call_args_list:
        _hass, metadata, stats = call.args
        if metadata["statistic_id"].endswith(suffix):
            return metadata, stats
    raise AssertionError(f"no async_add_external_statistics call ending in {suffix}")


async def test_first_run_backfill(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """First run walks history day-by-day and inserts usage + cost stats."""
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    data = await coordinator._async_update_data()

    # 3 days (2026-07-13..15) of hourly calls for the single meter.
    assert mock_client.get_usage.await_count == 3
    assert mock_stats["add_stats"].call_count == 2

    usage_meta, usage_stats = _call_for(mock_stats["add_stats"], "_usage")
    assert usage_meta["statistic_id"] == f"{DOMAIN}:{ACCOUNT_ID}_{SERIAL}_usage".lower()
    assert usage_meta["source"] == DOMAIN
    assert usage_meta["has_sum"] is True
    assert usage_meta["unit_of_measurement"] == UnitOfVolume.LITERS
    assert len(usage_stats) == 72  # 3 days x 24 hours

    # Earliest reading (07-13 01:00 end-of-hour) maps to the 07-13 00:00 start.
    assert usage_stats[0]["start"] == datetime(2026, 7, 13, tzinfo=TIMEZONE)
    assert usage_stats[0]["state"] == 1.0
    assert usage_stats[0]["sum"] == 1.0
    # Each day sums 1+..+24 = 300; three days = 900.
    assert usage_stats[-1]["sum"] == pytest.approx(900.0)

    _cost_meta, cost_stats = _call_for(mock_stats["add_stats"], "_cost")
    assert _cost_meta["unit_of_measurement"] == "GBP"
    assert cost_stats[-1]["sum"] == pytest.approx(9.0)

    # Live sensor snapshot is populated from the latest daily reading.
    assert data[SERIAL].latest is not None
    assert data[SERIAL].latest.consumption_litres == 150.0


async def test_resume_skips_stored_hours(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """With a stored point ahead of all data, nothing is fetched or added."""
    future_epoch = (FIXED_NOW + timedelta(days=5)).timestamp()
    mock_stats["last_stats"].side_effect = lambda _hass, _n, sid, _c, _t: {
        sid: [{"start": future_epoch, "sum": 500.0}]
    }
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    await coordinator._async_update_data()

    assert mock_client.get_usage.await_count == 0
    mock_stats["add_stats"].assert_not_called()


async def test_empty_days_tolerated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Empty hourly responses are skipped without error or statistics."""
    mock_client.get_usage = AsyncMock(return_value=[])
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    await coordinator._async_update_data()

    assert mock_client.get_usage.await_count == 3
    mock_stats["add_stats"].assert_not_called()


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


async def test_resume_after_first_run_is_idempotent(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """A second pass resumes from the last stored hour without double-counting.

    Simulates the recorder having stored the first run: the last usage hour is
    2026-07-15 23:00 (sum 900). The rerun only refetches that day and adds no
    new statistics, so the running sum is unchanged.
    """
    last_start = datetime(2026, 7, 15, 23, tzinfo=TIMEZONE).timestamp()
    mock_stats["last_stats"].side_effect = lambda _hass, _n, sid, _c, _t: {
        sid: [{"start": last_start, "sum": 900.0}]
    }
    coordinator = _build_coordinator(hass, mock_config_entry, mock_client)

    await coordinator._async_update_data()

    # Only the last stored day is refetched, and every hour is already stored.
    assert mock_client.get_usage.await_count == 1
    mock_stats["add_stats"].assert_not_called()
