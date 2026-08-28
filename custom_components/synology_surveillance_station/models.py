"""Data models for Synology Surveillance Station."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ApiDescriptor:
    """A Web API discovered on the DiskStation."""

    name: str
    path: str
    min_version: int
    max_version: int


@dataclass(frozen=True, slots=True)
class SurveillanceInfo:
    """Surveillance Station installation information."""

    serial: str | None
    version: str
    path: str | None
    camera_number: int
    license_number: int
    user_privilege: int | None
    allow_snapshot: bool | None


@dataclass(frozen=True, slots=True)
class Camera:
    """A camera managed by Surveillance Station."""

    camera_id: int
    name: str
    vendor: str | None = None
    model: str | None = None
    status: int | None = None
    ds_id: int | None = None
    video_codec: int | None = None
    ip_address: str | None = None


@dataclass(frozen=True, slots=True)
class Recording:
    """A Surveillance Station recording."""

    recording_id: int
    camera_id: int
    camera_name: str | None = None
    start_time: datetime | None = None
    stop_time: datetime | None = None
    video_codec: int | None = None
    ds_id: int | None = None
    mount_id: int | None = None
    size_bytes: int | None = None
    reason: int | str | None = None
    file_path: str | None = None
    locked: bool = False

    @property
    def duration_seconds(self) -> int | None:
        """Return the duration when both timestamps are available."""
        if self.start_time is None or self.stop_time is None:
            return None
        return max(0, int((self.stop_time - self.start_time).total_seconds()))


@dataclass(frozen=True, slots=True)
class SurveillanceEvent:
    """A normalized Surveillance Station event."""

    event_id: str
    camera_id: int
    camera_name: str
    event_type: str
    raw_event_name: str
    occurred_at: datetime
    received_at: datetime
    snapshot: bytes | None = None
    snapshot_content_type: str | None = None
    recording_id: int | None = None
    source_server: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
