"""Diagnostics support for Essex & Suffolk Water.

Diagnostics dumps are routinely pasted into public bug reports, so every piece
of account-identifying data is redacted: the login email and password, and the
account id / meter serial that make up the statistic ids.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, CONF_PASSWORD

if TYPE_CHECKING:
    from . import EswConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "account_id",
    "serial",
    "serial_number",
    "premise_id",
    "smart_point",
    "address",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EswConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with all PII redacted."""
    coordinator = entry.runtime_data
    # A list (not a serial-keyed dict) so the serial only ever appears as a
    # redactable *value*, never as an un-redactable dict key.
    meters = [
        {
            "meter": asdict(meter_data.meter),
            "daily_consumption": meter_data.daily_consumption,
            "daily_cost": meter_data.daily_cost,
            "last_reading": meter_data.last_reading,
        }
        for meter_data in coordinator.data.values()
    ]
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "history_ready": coordinator.history_ready,
            "backfill_scheduled": coordinator.backfill_scheduled,
            "meter_count": len(coordinator.data),
        },
        "meters": async_redact_data(meters, TO_REDACT),
    }
