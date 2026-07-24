"""Tests for diagnostics redaction."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.essex_suffolk_water.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ACCOUNT_ID, SERIAL

REDACTED = "**REDACTED**"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.essex_suffolk_water.coordinator.ESWaterClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_diagnostics_redacts_all_pii(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_stats: dict[str, MagicMock],
) -> None:
    """Credentials and account/meter identifiers never appear in diagnostics."""
    await _setup(hass, mock_config_entry, mock_client)

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diag["entry_data"]["email"] == REDACTED
    assert diag["entry_data"]["password"] == REDACTED
    assert diag["coordinator"]["meter_count"] == 1
    assert diag["coordinator"]["last_update_success"] is True
    for meter in diag["meters"]:
        assert meter["meter"]["serial"] == REDACTED
        assert meter["meter"]["account_id"] == REDACTED

    # Strongest guarantee: no sensitive literal survives anywhere in the dump.
    blob = json.dumps(diag, default=str)
    for secret in ("user@example.com", "secret", SERIAL, ACCOUNT_ID):
        assert secret not in blob
