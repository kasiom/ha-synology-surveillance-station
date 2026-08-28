"""Event entities for Surveillance Station detections."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.event import EventEntity, EventExtraStoredData
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SurveillanceConfigEntry
from .const import DOMAIN, EVENT_TYPES
from .models import Camera, SurveillanceEvent
from .runtime import SurveillanceRuntime


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SurveillanceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one flexible detection event entity per camera."""
    async_add_entities(
        SurveillanceDetectionEvent(entry, entry.runtime_data, camera)
        for camera in entry.runtime_data.cameras
    )


class SurveillanceDetectionEvent(EventEntity):
    """Emit normalized motion, smart detection, sound, and doorbell events."""

    _attr_has_entity_name = True
    _attr_translation_key = "detection"
    _attr_event_types = list(EVENT_TYPES)
    _unrecorded_attributes = frozenset(
        {
            "event_id",
            "camera_id",
            "camera_name",
            "occurred_at",
            "received_at",
            "raw_event_name",
            "recording_id",
            "source_server",
            "source",
            "recording_reason",
            "detection_verified_by",
            "snapshot_available",
        }
    )

    def __init__(
        self,
        entry: SurveillanceConfigEntry,
        runtime: SurveillanceRuntime,
        camera: Camera,
    ) -> None:
        self._runtime = runtime
        self._camera = camera
        assert runtime.hub_device_id is not None
        identity = runtime.info.serial or f"{runtime.api.host}:{runtime.api.port}"
        self._attr_unique_id = f"{identity}_{camera.camera_id}_detection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_camera_{camera.camera_id}")},
            manufacturer=camera.vendor or "Synology",
            model=camera.model,
            name=camera.name,
            via_device_id=runtime.hub_device_id,
        )

    @property
    def available(self) -> bool:
        """Expose whether the authoritative event poll currently succeeds."""
        return self._runtime.event_count_available.get(
            self._camera.camera_id, False
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after HA has assigned an entity ID."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.add_listener(self._camera.camera_id, self._handle_event)
        )
        self.async_on_remove(
            self._runtime.add_event_count_listener(
                self._camera.camera_id, self._handle_availability
            )
        )

    @callback
    def _handle_availability(self) -> None:
        self.async_write_ha_state()

    @override
    async def async_get_last_event_data(self) -> EventExtraStoredData | None:
        """Migrate a restored legacy unknown type without firing a new event."""
        event_data = await super().async_get_last_event_data()
        latest = self._runtime.last_events.get(self._camera.camera_id)
        if (
            event_data is not None
            and event_data.last_event_type in {"unknown", "detection"}
            and latest is not None
        ):
            return EventExtraStoredData(
                latest.event_type,
                event_data.last_event_attributes,
            )
        return event_data

    @callback
    def _handle_event(self, event: SurveillanceEvent) -> None:
        attributes: dict[str, Any] = {
            "event_id": event.event_id,
            "camera_id": event.camera_id,
            "camera_name": event.camera_name,
            "occurred_at": event.occurred_at.isoformat(),
            "received_at": event.received_at.isoformat(),
            "raw_event_name": event.raw_event_name,
            "snapshot_available": event.snapshot is not None,
        }
        if event.recording_id is not None:
            attributes["recording_id"] = event.recording_id
        if event.source_server:
            attributes["source_server"] = event.source_server
        if event.extra:
            attributes.update(event.extra)
        self._trigger_event(event.event_type, attributes)
        self.async_write_ha_state()
