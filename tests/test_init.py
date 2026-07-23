"""Tests for setup, unload, and the sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.essex_suffolk_water.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

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

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL)})
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
