"""Last-event snapshot image entities."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
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
    """Create one last-event snapshot per camera."""
    async_add_entities(
        SurveillanceLastEventImage(_hass, entry.runtime_data, camera)
        for camera in entry.runtime_data.cameras
    )


class SurveillanceLastEventImage(ImageEntity):
    """Keep the most recent API-enriched event snapshot in memory."""

    _attr_has_entity_name = True
    # Keep a visible fallback in entity lists and cards before the first image.
    # The translated icon in icons.json remains the primary HA definition.
    _attr_icon = "mdi:image-outline"
    _attr_translation_key = "last_event"

    def __init__(
        self, hass: HomeAssistant, runtime: SurveillanceRuntime, camera: Camera
    ) -> None:
        super().__init__(hass)
        self._runtime = runtime
        self._camera = camera
        self._image: bytes | None = None
        assert runtime.hub_device_id is not None
        identity = runtime.info.serial or f"{runtime.api.host}:{runtime.api.port}"
        self._attr_unique_id = f"{identity}_{camera.camera_id}_last_event"
        self._attr_content_type = "image/jpeg"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_camera_{camera.camera_id}")},
            manufacturer=camera.vendor or "Synology",
            model=camera.model,
            name=camera.name,
            via_device_id=runtime.hub_device_id,
        )

    @property
    def available(self) -> bool:
        """Avoid presenting a broken image before the first snapshot arrives."""
        return self._image is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe after HA has assigned an entity ID."""
        await super().async_added_to_hass()
        if event := self._runtime.last_events.get(self._camera.camera_id):
            self._handle_event(event)
        self.async_on_remove(
            self._runtime.add_state_event_listener(
                self._camera.camera_id, self._handle_event
            )
        )

    @callback
    def _handle_event(self, event: SurveillanceEvent) -> None:
        self._image = event.snapshot
        self._attr_content_type = event.snapshot_content_type or "image/jpeg"
        self._attr_image_last_updated = event.occurred_at
        self.async_update_token()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return the in-memory last-event image without exposing NAS credentials."""
        return self._image
