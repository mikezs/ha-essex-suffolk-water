"""Shared fixtures for the Essex & Suffolk Water tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.essex_suffolk_water.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    TIMEZONE,
)
from eswater import Account, Meter, UsageReading
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Deliberately fake, non-real identifiers (do not use a real account/serial).
ACCOUNT_ID = "1000000000"
SERIAL = "TESTMETER01"
# A fixed "now" (Europe/London) so backfill spans a small, deterministic range.
FIXED_NOW = datetime(2026, 7, 16, 10, 0, tzinfo=TIMEZONE)
INSTALLED = datetime(2026, 7, 13)  # 3 days before yesterday (2026-07-15)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading of the custom integration in every test."""
    yield


def make_day_readings(day: datetime, *, with_cost: bool = True) -> list[UsageReading]:
    """24 hourly readings for a day; ``Date`` marks the end of each hour."""
    midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
    readings: list[UsageReading] = []
    for hour in range(1, 25):
        readings.append(
            UsageReading(
                timestamp=midnight + timedelta(hours=hour),
                consumption_litres=float(hour),
                cost=round(hour * 0.01, 4) if with_cost else None,
                reading_type=1,
            )
        )
    return readings


@pytest.fixture
def meter() -> Meter:
    """A single smart meter fixture."""
    return Meter(
        serial=SERIAL,
        account_id=ACCOUNT_ID,
        premise_id="P1",
        installed_date=INSTALLED,
        last_read=231.0,
        last_read_date=datetime(2026, 7, 14),
    )


@pytest.fixture
def account(meter: Meter) -> Account:
    """An account owning the meter fixture."""
    acc = Account(
        account_id=ACCOUNT_ID,
        premise_id="P1",
        address="1 Test Street",
        is_smart=True,
    )
    acc.meters = [meter]
    return acc


@pytest.fixture
def mock_client(account: Account) -> AsyncMock:
    """A mocked ESWaterClient returning the account/meter fixtures."""
    client = AsyncMock()
    client.authenticate = AsyncMock()
    client.get_accounts = AsyncMock(return_value=[account])
    client.get_latest_reading = AsyncMock(
        return_value=UsageReading(
            timestamp=datetime(2026, 7, 14),
            consumption_litres=150.0,
            cost=0.9,
            reading_type=1,
        )
    )
    client.get_usage = AsyncMock(
        side_effect=lambda account_id, serial, start_date, granularity: (
            make_day_readings(start_date)
        )
    )
    return client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured entry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="user@example.com",
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
    )


class _FakeRecorder:
    """Minimal recorder stand-in running executor jobs inline."""

    async def async_add_executor_job(self, func, *args):  # type: ignore[no-untyped-def]
        return func(*args)


@pytest.fixture
def mock_stats() -> Generator[dict[str, MagicMock]]:
    """Patch the statistics/recorder plumbing and a fixed clock.

    Yields the patched mocks; by default ``get_last_statistics`` reports no
    prior data (first-run backfill).
    """
    fake_now = MagicMock()
    fake_now.now.return_value = FIXED_NOW

    with (
        patch(
            "custom_components.essex_suffolk_water.coordinator.BACKFILL_THROTTLE", 0
        ),
        patch(
            "custom_components.essex_suffolk_water.coordinator.get_instance",
            return_value=_FakeRecorder(),
        ),
        patch(
            "custom_components.essex_suffolk_water.coordinator.get_last_statistics",
            return_value={},
        ) as last_stats,
        patch(
            "custom_components.essex_suffolk_water.coordinator.async_add_external_statistics"
        ) as add_stats,
        patch(
            "custom_components.essex_suffolk_water.coordinator.dt_util", fake_now
        ),
    ):
        yield {"last_stats": last_stats, "add_stats": add_stats}
