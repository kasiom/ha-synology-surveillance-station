"""Camera connectivity entities for Surveillance Station."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SurveillanceConfigEntry
from .const import DOMAIN
from .models import Camera
from .runtime import SurveillanceRuntime

_STATUS_NAMES = {
    0: "unknown",
    1: "normal",
    2: "deleted",
    3: "disconnected",
    4: "unavailable",
    5: "ready",
    6: "inaccessible",
    7: "disabled",
    8: "unrecognized",
    9: "setting",
    10: "server_disconnected",
    11: "migrating",
    12: "other",
    13: "storage_removed",
    14: "stopping",
    15: "connection_history_failed",
    16: "unauthorized",
    17: "rtsp_error",
    18: "no_video",
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SurveillanceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create connectivity entities from the documented Camera List status."""
    async_add_entities(
        SurveillanceCameraOnlineBinarySensor(entry.runtime_data, camera)
        for camera in entry.runtime_data.cameras
    )


class SurveillanceCameraOnlineBinarySensor(BinarySensorEntity):
    """Represent whether Surveillance Station reports a running camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "camera_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime: SurveillanceRuntime, camera: Camera) -> None:
        self._runtime = runtime
        self._camera = camera
        assert runtime.hub_device_id is not None
        identity = runtime.info.serial or f"{runtime.api.host}:{runtime.api.port}"
        self._attr_unique_id = f"{identity}_{camera.camera_id}_camera_online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_camera_{camera.camera_id}")},
            manufacturer=camera.vendor or "Synology",
            model=camera.model,
            name=camera.name,
            via_device_id=runtime.hub_device_id,
        )

    @property
    def available(self) -> bool:
        """Return whether a current status has been received."""
        return self._runtime.camera_status_available.get(
            self._camera.camera_id, False
        )

    @property
    def is_on(self) -> bool | None:
        """Return true only for the documented normal state."""
        status = self._runtime.camera_statuses.get(self._camera.camera_id)
        return status == 1 if status is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the documented state name for troubleshooting."""
        status = self._runtime.camera_statuses.get(self._camera.camera_id)
        return {
            "surveillance_station_status": _STATUS_NAMES.get(status, "unknown")
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to minute-level camera status refreshes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.add_status_listener(
                self._camera.camera_id, self._handle_status_update
            )
        )

    @callback
    def _handle_status_update(self) -> None:
        self.async_write_ha_state()
