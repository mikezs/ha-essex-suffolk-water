"""Constants for the Essex & Suffolk Water integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

DOMAIN: Final = "essex_suffolk_water"

# Config entry keys.
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# The upstream data only updates roughly daily and lags 1-2 days, so a modest
# hourly poll keeps things fresh without hammering the private portal API.
SCAN_INTERVAL: Final = timedelta(hours=1)

# The ESW/NWG API returns naive local timestamps. Essex & Suffolk Water is a
# UK-only supplier, so we interpret those wall-clock times as Europe/London
# rather than relying on ``dt_util.as_local`` (which treats naive input as UTC).
TIMEZONE: Final = ZoneInfo("Europe/London")

# First-run history backfill is done one hourly API call per day. Cap how far
# back we reach and pace the calls so a fresh install is polite to the portal.
# The deep backfill runs once in a background task, so this does not block setup.
BACKFILL_MAX_DAYS: Final = 730
BACKFILL_THROTTLE: Final = 0.3  # seconds between per-day hourly calls

# Each regular poll fetches only this trailing window of hourly data: enough to
# refresh the live sensors and append new statistics without a large fetch.
RECENT_DAYS: Final = 3

# Device registry metadata.
MANUFACTURER: Final = "Essex & Suffolk Water"
MODEL: Final = "Smart Water Meter"
