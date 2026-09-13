"""Diagnostics for Synology Surveillance Station."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SurveillanceConfigEntry

_TO_REDACT = {"host", "username", "password", "webhook_id"}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: SurveillanceConfigEntry
) -> dict[str, Any]:
    """Return payload-shape diagnostics while excluding values and credentials."""
    runtime = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), _TO_REDACT),
        "selection": {
            "available_camera_count": len(runtime.available_cameras),
            "selected_camera_ids": [
                camera.camera_id for camera in runtime.cameras
            ],
        },
        "surveillance_station": {
            "version": runtime.info.version,
            "camera_number": runtime.info.camera_number,
            "license_number": runtime.info.license_number,
            "user_privilege": runtime.info.user_privilege,
            "allow_snapshot": runtime.info.allow_snapshot,
        },
        "cameras": [
            {
                "camera_id": camera.camera_id,
                "name": "**REDACTED**",
                "vendor": camera.vendor,
                "model": camera.model,
                "status": camera.status,
                "video_codec": camera.video_codec,
                "profile_stream_numbers": {
                    "high": camera.high_profile_stream_no,
                    "medium": camera.medium_profile_stream_no,
                    "low": camera.low_profile_stream_no,
                },
                "streams": [
                    {
                        "number": stream.number,
                        "resolution": stream.resolution,
                        "fps": stream.fps,
                        "bitrate_control": stream.bitrate_control,
                        "constant_bitrate": stream.constant_bitrate,
                        "quality": stream.quality,
                    }
                    for stream in camera.streams
                ],
            }
            for camera in runtime.cameras
        ],
        "polling": runtime.polling_diagnostics,
        "metadata": runtime.metadata_diagnostics,
        "availability": {
            "event_counts": runtime.event_count_available,
            "recording_counts": runtime.recording_count_available,
            "camera_statuses": runtime.camera_status_available,
        },
        "persistence": runtime.persistence_diagnostics,
    }
