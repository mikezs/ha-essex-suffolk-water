"""Base entity for Essex & Suffolk Water meter devices."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import EswDataUpdateCoordinator, MeterData


class EswMeterEntity(CoordinatorEntity[EswDataUpdateCoordinator]):
    """An entity bound to a single smart meter device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EswDataUpdateCoordinator, serial: str, key: str
    ) -> None:
        """Set up the meter device info and per-sensor identity."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"ESW meter {serial}",
            serial_number=serial,
        )

    @property
    def meter_data(self) -> MeterData | None:
        """Return this meter's latest coordinator snapshot, if present."""
        return self.coordinator.data.get(self._serial)

    @property
    def available(self) -> bool:
        """Available while the coordinator succeeds and the meter is known."""
        return super().available and self.meter_data is not None
