"""Sensor platform for Essex & Suffolk Water.

These are glanceable "current value" sensors. The Energy-dashboard history is
provided separately by the coordinator via long-term statistics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EswConfigEntry
from .const import TIMEZONE
from .coordinator import COST_UNIT, EswDataUpdateCoordinator, MeterData
from .entity import EswMeterEntity


@dataclass(frozen=True, kw_only=True)
class EswSensorDescription(SensorEntityDescription):
    """Sensor description carrying the value/availability accessors."""

    value_fn: Callable[[MeterData], float | datetime | None]
    available_fn: Callable[[MeterData], bool] = lambda _data: True


def _latest_timestamp(data: MeterData) -> datetime | None:
    """Localize the naive reading timestamp for the timestamp sensor."""
    if data.latest is None:
        return None
    return data.latest.timestamp.replace(tzinfo=TIMEZONE)


SENSORS: tuple[EswSensorDescription, ...] = (
    EswSensorDescription(
        key="daily_consumption",
        translation_key="daily_consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.latest.consumption_litres if d.latest else None,
        available_fn=lambda d: d.latest is not None,
    ),
    EswSensorDescription(
        key="daily_cost",
        translation_key="daily_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=COST_UNIT,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.latest.cost if d.latest else None,
        available_fn=lambda d: d.latest is not None and d.latest.cost is not None,
    ),
    EswSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_latest_timestamp,
        available_fn=lambda d: d.latest is not None,
    ),
    EswSensorDescription(
        key="meter_last_read",
        translation_key="meter_last_read",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.meter.last_read,
        available_fn=lambda d: d.meter.last_read is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EswConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ESW sensors for each discovered meter."""
    coordinator = entry.runtime_data
    async_add_entities(
        EswSensor(coordinator, serial, description)
        for serial in coordinator.data
        for description in SENSORS
    )


class EswSensor(EswMeterEntity, SensorEntity):
    """A single ESW meter sensor."""

    entity_description: EswSensorDescription

    def __init__(
        self,
        coordinator: EswDataUpdateCoordinator,
        serial: str,
        description: EswSensorDescription,
    ) -> None:
        """Attach the sensor description to the meter entity."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | datetime | None:
        """Return the current value from the coordinator snapshot."""
        data = self.meter_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def available(self) -> bool:
        """Extend base availability with the per-sensor predicate."""
        data = self.meter_data
        return (
            super().available
            and data is not None
            and self.entity_description.available_fn(data)
        )
