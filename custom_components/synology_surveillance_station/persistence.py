"""Serialize the latest events so entity state survives Home Assistant restarts."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .models import SurveillanceEvent

LEGACY_STORAGE_VERSION = 1
STORAGE_VERSION = 2
MAX_STORED_SNAPSHOT_BYTES = 2 * 1024 * 1024


def serialize_last_events(events: Mapping[int, SurveillanceEvent]) -> dict[str, Any]:
    """Return a bounded JSON-safe representation of the newest camera events."""
    serialized = []
    for event in events.values():
        snapshot = event.snapshot
        if snapshot is not None and len(snapshot) > MAX_STORED_SNAPSHOT_BYTES:
            snapshot = None
        try:
            json.dumps(event.extra)
            extra = event.extra
        except (TypeError, ValueError):
            extra = {}
        serialized.append(
            {
                "event_id": event.event_id,
                "camera_id": event.camera_id,
                "camera_name": event.camera_name,
                "event_type": event.event_type,
                "raw_event_name": event.raw_event_name,
                "occurred_at": event.occurred_at.isoformat(),
                "received_at": event.received_at.isoformat(),
                "snapshot": (
                    base64.b64encode(snapshot).decode("ascii")
                    if snapshot is not None
                    else None
                ),
                "snapshot_content_type": event.snapshot_content_type,
                "recording_id": event.recording_id,
                "source_server": event.source_server,
                "extra": extra,
            }
        )
    return {"events": serialized}


def deserialize_last_events(
    value: Mapping[str, Any] | None,
    allowed_camera_ids: set[int],
) -> dict[int, SurveillanceEvent]:
    """Restore valid events for cameras still present in Surveillance Station."""
    if not isinstance(value, Mapping) or not isinstance(value.get("events"), list):
        return {}
    restored: dict[int, SurveillanceEvent] = {}
    for raw in value["events"]:
        if not isinstance(raw, Mapping):
            continue
        try:
            camera_id = int(raw["camera_id"])
            occurred_at = datetime.fromisoformat(str(raw["occurred_at"]))
            received_at = datetime.fromisoformat(str(raw["received_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            camera_id not in allowed_camera_ids
            or occurred_at.tzinfo is None
            or received_at.tzinfo is None
        ):
            continue
        snapshot = _decode_snapshot(raw.get("snapshot"))
        recording_id = raw.get("recording_id")
        try:
            recording_id = int(recording_id) if recording_id is not None else None
        except (TypeError, ValueError):
            recording_id = None
        extra = raw.get("extra")
        extra = dict(extra) if isinstance(extra, Mapping) else {}
        event_type = str(raw.get("event_type") or "event_recording")
        if (
            event_type in {"unknown", "detection"}
            and extra.get("detection_verified_by") == "CountByCategory"
        ):
            event_type = "event_recording"
        restored[camera_id] = SurveillanceEvent(
            event_id=str(raw.get("event_id") or f"restored-{camera_id}"),
            camera_id=camera_id,
            camera_name=str(raw.get("camera_name") or f"Camera {camera_id}"),
            event_type=event_type,
            raw_event_name=str(raw.get("raw_event_name") or "Event recording"),
            occurred_at=occurred_at,
            received_at=received_at,
            snapshot=snapshot,
            snapshot_content_type=(
                str(raw["snapshot_content_type"])
                if snapshot is not None and raw.get("snapshot_content_type")
                else ("image/jpeg" if snapshot is not None else None)
            ),
            recording_id=recording_id,
            source_server=(
                str(raw["source_server"]) if raw.get("source_server") else None
            ),
            extra=extra,
        )
    return restored


def _decode_snapshot(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        snapshot = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None
    return snapshot if len(snapshot) <= MAX_STORED_SNAPSHOT_BYTES else None
