"""Live camera streams exposed by Synology Surveillance Station."""

from __future__ import annotations

import logging
from typing import Any, override

from aiohttp import web
from homeassistant.components.camera import Camera as HomeAssistantCamera
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_web,
    async_get_clientsession,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SurveillanceConfigEntry
from .api import SynologyApiError
from .const import DOMAIN
from .models import Camera, CameraStream
from .runtime import SurveillanceRuntime

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SurveillanceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one entity for every numbered stream reported by the NAS."""
    async_add_entities(
        SurveillanceLiveCamera(entry.runtime_data, camera, stream)
        for camera in entry.runtime_data.cameras
        for stream in camera.streams
    )


class SurveillanceLiveCamera(HomeAssistantCamera):
    """A dynamically discovered Surveillance Station camera stream."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cctv"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        runtime: SurveillanceRuntime,
        camera: Camera,
        stream: CameraStream,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._camera = camera
        self._stream = stream
        identity = runtime.info.serial or f"{runtime.api.host}:{runtime.api.port}"
        self._attr_unique_id = (
            f"{identity}_{camera.camera_id}_live_stream_{stream.number}"
        )
        self._attr_name = f"Stream {stream.number}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_camera_{camera.camera_id}")},
            manufacturer=camera.vendor or "Synology",
            model=camera.model,
            name=camera.name,
            via_device_id=runtime.hub_device_id,
        )
        self._source_kind = self._resolve_source_kind()
        if self._source_kind == "rtsp":
            self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"
        self._attr_supported_features = (
            CameraEntityFeature.STREAM
            if self._source_kind == "rtsp"
            else CameraEntityFeature(0)
        )

    def _resolve_source_kind(self) -> str | None:
        """Map numbered streams only when Synology publishes a matching path."""
        if self._stream.number == self._camera.high_profile_stream_no:
            return "rtsp"
        if self._stream.number == self._camera.low_profile_stream_no:
            return "mjpeg"
        return None

    @property
    def available(self) -> bool:
        """Report the camera state without claiming unsupported paths work."""
        return self._camera.status == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose discovered profile metadata without credentials or URLs."""
        roles = []
        if self._stream.number == self._camera.high_profile_stream_no:
            roles.append("high")
        if self._stream.number == self._camera.medium_profile_stream_no:
            roles.append("medium")
        if self._stream.number == self._camera.low_profile_stream_no:
            roles.append("low")
        bitrate_modes = {1: "variable", 2: "constant"}
        return {
            "stream_number": self._stream.number,
            "profile_roles": roles,
            "resolution": self._stream.resolution,
            "fps": self._stream.fps,
            "bitrate_control": bitrate_modes.get(
                self._stream.bitrate_control, self._stream.bitrate_control
            ),
            "constant_bitrate_kbps": self._stream.constant_bitrate,
            "quality": self._stream.quality,
            "live_source": self._source_kind or "metadata_only",
            "available_stream_numbers": [
                item.number for item in self._camera.streams
            ],
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the closest Synology snapshot profile for this stream."""
        del width, height
        profile_type = 1
        if self._stream.number == self._camera.high_profile_stream_no:
            profile_type = 0
        elif self._stream.number == self._camera.low_profile_stream_no:
            profile_type = 2
        try:
            image, _content_type = await self._runtime.api.async_get_snapshot(
                self._camera.camera_id, profile_type
            )
        except SynologyApiError:
            _LOGGER.debug(
                "Could not fetch snapshot for camera %s stream %s",
                self._camera.camera_id,
                self._stream.number,
                exc_info=True,
            )
            return None
        return image

    async def stream_source(self) -> str | None:
        """Return a fresh URL so expiring Synology stream keys remain valid."""
        if self._source_kind is None:
            return None
        # Reuse the official integration's authenticated live-view paths when
        # it manages the same NAS. The dedicated account can then stay limited
        # to events and snapshots, and no DSM password needs to be duplicated.
        if source := self._core_synology_source():
            return source
        try:
            paths = await self._runtime.api.async_get_live_view_paths(
                [self._camera.camera_id]
            )
        except SynologyApiError:
            _LOGGER.debug(
                "Could not fetch live-view path for camera %s stream %s",
                self._camera.camera_id,
                self._stream.number,
                exc_info=True,
            )
            return None
        path = paths.get(self._camera.camera_id)
        if path is None:
            return None
        if self._source_kind == "mjpeg":
            return path.mjpeg_http
        return path.rtsp or path.rtsp_over_http

    @override
    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Proxy Synology's low-bandwidth MJPEG without HLS transcoding."""
        if self._source_kind != "mjpeg" or not (source := await self.stream_source()):
            return None
        websession = async_get_clientsession(self.hass, verify_ssl=False)
        return await async_aiohttp_proxy_web(
            self.hass, request, websession.get(source)
        )

    def _core_synology_source(self) -> str | None:
        """Return a cached path from the official integration for the same NAS."""
        expected_host = self._runtime.api.host.strip("[]").casefold()
        for entry in self.hass.config_entries.async_entries("synology_dsm"):
            configured_host = str(entry.data.get(CONF_HOST, "")).strip("[]").casefold()
            if configured_host != expected_host:
                continue
            entry_runtime = getattr(entry, "runtime_data", None)
            api = getattr(entry_runtime, "api", None)
            surveillance = getattr(api, "surveillance_station", None)
            if surveillance is None:
                continue
            live_view = surveillance.get_camera_live_view_path(
                self._camera.camera_id
            )
            if live_view is None:
                continue
            if self._source_kind == "mjpeg":
                return live_view.mjpeg_http
            return live_view.rtsp or live_view.rtsp_http
        return None
