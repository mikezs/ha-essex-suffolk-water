"""Tests for setup, unload, and the sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.essex_suffolk_water.const import DOMAIN
from eswater import InvalidAuth, ServiceUnavailable
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from .conftest import SERIAL

CONSUMPTION = "sensor.esw_meter_testmeter01_daily_consumption"
COST = "sensor.esw_meter_testmeter01_daily_cost"
LAST_READING = "sensor.esw_meter_testmeter01_last_reading"
METER_READ = "sensor.esw_meter_testmeter01_meter_reading"


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock
) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.essex_suffolk_water.coordinator.ESWaterClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_creates_device_and_sensors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Setup registers a meter device with the expected sensors."""
    await _setup(hass, mock_config_entry, mock_client)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), mock_config_entry.entry_id)
    device = next((device for device in devices if (DOMAIN, SERIAL) in device.identifiers), None)
    assert device is not None
    assert device.manufacturer == "Essex & Suffolk Water"

    # Derived from the most recent day of hourly data: 1+..+24 = 300 L.
    assert hass.states.get(CONSUMPTION).state == "300.0"
    assert hass.states.get(COST).state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
    last_reading = hass.states.get(LAST_READING)
    assert last_reading is not None and last_reading.state != STATE_UNAVAILABLE
    assert hass.states.get(METER_READ).state == "231.0"


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """The entry unloads cleanly and tears down its entities."""
    await _setup(hass, mock_config_entry, mock_client)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(CONSUMPTION).state == STATE_UNAVAILABLE


async def test_entry_sessions_are_isolated_and_closed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Cookie jars are private across entries and HA; unload closes only its session."""
    shared = async_get_clientsession(hass)
    second = MockConfigEntry(
        domain=DOMAIN, unique_id="other@example.com", data=mock_config_entry.data
    )
    sessions = []

    def make_client(session, *_args):
        sessions.append(session)
        if len(sessions) == 1:
            return mock_client
        return AsyncMock(authenticate=AsyncMock(), get_accounts=AsyncMock(return_value=[]))

    for entry in (mock_config_entry, second):
        entry.add_to_hass(hass)
        with patch(
            "custom_components.essex_suffolk_water.coordinator.ESWaterClient",
            side_effect=make_client,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    first, other = sessions
    portal = URL("https://www.eswater.co.uk")
    first.cookie_jar.update_cookies({"portal_session": "first"}, portal)
    assert not shared.cookie_jar.filter_cookies(portal)
    assert not other.cookie_jar.filter_cookies(portal)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert first.closed
    assert not other.closed
    assert not shared.closed
    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert other.closed


@pytest.mark.parametrize("error", [InvalidAuth("bad"), ServiceUnavailable("down")])
async def test_failed_setup_closes_private_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    error: Exception,
) -> None:
    """Failed first refresh must release the entry's private cookie session too."""
    mock_config_entry.add_to_hass(hass)
    mock_client.authenticate.side_effect = error
    with patch(
        "custom_components.essex_suffolk_water.coordinator.ESWaterClient",
        return_value=mock_client,
    ) as factory:
        assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    session = factory.call_args.args[0]
    assert session.closed
