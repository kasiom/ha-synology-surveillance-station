"""Browse and play Surveillance Station recordings in Home Assistant."""

from __future__ import annotations

from datetime import timedelta
from typing import override

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import SynologyApiError
from .const import DOMAIN, RECORDING_PROXY_URL
from .runtime import SurveillanceRuntime

_RANGES = ((1, "Last 24 hours"), (7, "Last 7 days"), (30, "Last 30 days"))


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the global Surveillance Station media source."""
    return SurveillanceMediaSource(hass)


class SurveillanceMediaSource(MediaSource):
    """Provide recordings from all loaded Surveillance Station entries."""

    name = "Synology Surveillance Station"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    @property
    def runtimes(self) -> dict[str, SurveillanceRuntime]:
        """Return currently loaded config entries."""
        return self.hass.data.get(DOMAIN, {})

    @override
    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a cached recording to the authenticated local proxy."""
        parts = (item.identifier or "").split("|")
        if len(parts) != 3 or parts[0] != "REC":
            raise Unresolvable("Invalid Surveillance Station recording identifier")
        _kind, entry_id, recording_key = parts
        runtime = self.runtimes.get(entry_id)
        if runtime is None or recording_key not in runtime.recordings:
            raise Unresolvable("Recording is no longer available; browse the camera again")
        path = RECORDING_PROXY_URL.format(
            entry_id=entry_id, recording_key=recording_key
        )
        return PlayMedia(path, "video/mp4")

    @override
    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse NAS entries, cameras, time ranges, and individual recordings."""
        if not item.identifier:
            return self._root()
        parts = item.identifier.split("|")
        if len(parts) < 3:
            raise BrowseError("Invalid Surveillance Station media identifier")
        item_type, entry_id, camera_id_text, *rest = parts
        runtime = self.runtimes.get(entry_id)
        if runtime is None:
            raise BrowseError("Surveillance Station entry is not loaded")
        try:
            camera_id = int(camera_id_text)
        except ValueError as err:
            raise BrowseError("Invalid camera ID") from err
        camera = next(
            (camera for camera in runtime.cameras if camera.camera_id == camera_id), None
        )
        if camera is None:
            raise BrowseError("Camera no longer exists")
        if item_type == "CAM" and not rest:
            return self._camera_ranges(entry_id, camera_id, camera.name)
        if item_type == "RANGE" and len(rest) == 1:
            try:
                days = int(rest[0])
            except ValueError as err:
                raise BrowseError("Invalid recording range") from err
            if days not in {value for value, _title in _RANGES}:
                raise BrowseError("Unsupported recording range")
            now = dt_util.utcnow()
            try:
                recordings = await runtime.api.async_list_recordings(
                    camera_id, now - timedelta(days=days), now
                )
            except SynologyApiError as err:
                raise BrowseError("Could not load recordings from the DiskStation") from err
            runtime.remember_recordings(recordings)
            return self._recordings(
                entry_id, camera_id, camera.name, days, recordings
            )
        raise BrowseError("Unsupported Surveillance Station media item")

    def _root(self) -> BrowseMediaSource:
        children: list[BrowseMediaSource] = []
        multiple_entries = len(self.runtimes) > 1
        for entry_id, runtime in self.runtimes.items():
            for camera in runtime.cameras:
                title = (
                    f"{runtime.api.host} — {camera.name}"
                    if multiple_entries
                    else camera.name
                )
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"CAM|{entry_id}|{camera.camera_id}",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.VIDEO,
                        title=title,
                        can_play=False,
                        can_expand=True,
                        children_media_class=MediaClass.DIRECTORY,
                    )
                )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=self.name,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=children,
        )

    @staticmethod
    def _camera_ranges(entry_id: str, camera_id: int, camera_name: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"CAM|{entry_id}|{camera_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=camera_name,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=[
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"RANGE|{entry_id}|{camera_id}|{days}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.VIDEO,
                )
                for days, title in _RANGES
            ],
        )

    @staticmethod
    def _recordings(
        entry_id: str,
        camera_id: int,
        camera_name: str,
        days: int,
        recordings,
    ) -> BrowseMediaSource:
        children: list[BrowseMediaSource] = []
        for recording in sorted(
            recordings,
            key=lambda value: value.start_time or dt_util.utcnow(),
            reverse=True,
        ):
            if recording.start_time:
                title = dt_util.as_local(recording.start_time).strftime("%Y-%m-%d %H:%M:%S")
            else:
                title = f"Recording {recording.recording_id}"
            if recording.duration_seconds is not None:
                minutes, seconds = divmod(recording.duration_seconds, 60)
                title += f" · {minutes}:{seconds:02d}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"REC|{entry_id}|"
                        f"{SurveillanceRuntime.recording_key(recording)}"
                    ),
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"RANGE|{entry_id}|{camera_id}|{days}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{camera_name} — last {days} day{'s' if days != 1 else ''}",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.VIDEO,
            children=children,
        )
