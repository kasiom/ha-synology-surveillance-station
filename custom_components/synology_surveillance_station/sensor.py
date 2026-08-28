"""Read-only camera statistics for Surveillance Station."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SurveillanceConfigEntry
from .const import DOMAIN
from .models import Camera, SurveillanceEvent
from .runtime import SurveillanceRuntime


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SurveillanceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create useful, low-frequency sensors for each camera."""
    entities: list[SensorEntity] = []
    for camera in entry.runtime_data.cameras:
        entities.extend(
            (
                SurveillanceLastDetectionSensor(entry.runtime_data, camera),
                SurveillanceEventsTodaySensor(entry.runtime_data, camera),
                SurveillanceRecordingsTodaySensor(entry.runtime_data, camera),
            )
        )
    async_add_entities(entities)


class SurveillanceCameraSensor(SensorEntity):
    """Base class for a sensor attached to one Surveillance Station camera."""

    _attr_has_entity_name = True

    def __init__(self, runtime: SurveillanceRuntime, camera: Camera, suffix: str) -> None:
        self._runtime = runtime
        self._camera = camera
        assert runtime.hub_device_id is not None
        identity = runtime.info.serial or f"{runtime.api.host}:{runtime.api.port}"
        self._attr_unique_id = f"{identity}_{camera.camera_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_camera_{camera.camera_id}")},
            manufacturer=camera.vendor or "Synology",
            model=camera.model,
            name=camera.name,
            via_device_id=runtime.hub_device_id,
        )


class SurveillanceLastDetectionSensor(SurveillanceCameraSensor):
    """Expose the last detection timestamp for dashboards and conditions."""

    _attr_translation_key = "last_detection"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: SurveillanceRuntime, camera: Camera) -> None:
        super().__init__(runtime, camera, "last_detection")

    @property
    def native_value(self):
        """Return the last event time from memory."""
        event = self._runtime.last_events.get(self._camera.camera_id)
        return event.occurred_at if event else None

    async def async_added_to_hass(self) -> None:
        """Subscribe to event delivery after entity registration."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.add_state_event_listener(
                self._camera.camera_id, self._handle_event
            )
        )

    @callback
    def _handle_event(self, _event: SurveillanceEvent) -> None:
        self.async_write_ha_state()


class SurveillanceRecordingsTodaySensor(SurveillanceCameraSensor):
    """Expose today's exact recording count from the Recording API."""

    _attr_translation_key = "recordings_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: SurveillanceRuntime, camera: Camera) -> None:
        super().__init__(runtime, camera, "recordings_today")

    @property
    def available(self) -> bool:
        """Return whether the first exact count has been received."""
        return self._runtime.recording_count_available.get(
            self._camera.camera_id, False
        )

    @property
    def native_value(self) -> int | None:
        """Return the cached count without network I/O."""
        return self._runtime.recordings_today.get(self._camera.camera_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe to minute-level count refreshes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.add_count_listener(
                self._camera.camera_id, self._handle_count_update
            )
        )

    @callback
    def _handle_count_update(self) -> None:
        self.async_write_ha_state()


class SurveillanceEventsTodaySensor(SurveillanceCameraSensor):
    """Expose today's verified detection count from the Recording API."""

    _attr_translation_key = "events_today"

    def __init__(self, runtime: SurveillanceRuntime, camera: Camera) -> None:
        super().__init__(runtime, camera, "events_today")

    @property
    def available(self) -> bool:
        """Return whether the first detection count has been received."""
        return self._runtime.event_count_available.get(
            self._camera.camera_id, False
        )

    @property
    def native_value(self) -> int | None:
        """Return the cached verified detection count without network I/O."""
        return self._runtime.events_today.get(self._camera.camera_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe to polling-level count changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.add_event_count_listener(
                self._camera.camera_id, self._handle_count_update
            )
        )

    @callback
    def _handle_count_update(self) -> None:
        self.async_write_ha_state()
