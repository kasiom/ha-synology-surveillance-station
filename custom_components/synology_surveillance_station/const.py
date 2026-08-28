"""Constants for Synology Surveillance Station."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "synology_surveillance_station"
NAME: Final = "Synology Surveillance Station"

CONF_USE_SSL: Final = "use_ssl"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_POLLING_ENABLED: Final = "polling_enabled"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_CAMERA_IDS: Final = "camera_ids"

DEFAULT_PORT_HTTP: Final = 5000
DEFAULT_PORT_HTTPS: Final = 5001
DEFAULT_POLL_INTERVAL: Final = 10
MIN_POLL_INTERVAL: Final = 5
MAX_POLL_INTERVAL: Final = 300

PLATFORMS: Final = ("binary_sensor", "event", "image", "sensor")

API_AUTH: Final = "SYNO.API.Auth"
API_CAMERA: Final = "SYNO.SurveillanceStation.Camera"
API_INFO: Final = "SYNO.SurveillanceStation.Info"
API_RECORDING: Final = "SYNO.SurveillanceStation.Recording"

METADATA_REFRESH_INTERVAL: Final = 60
RECORDING_COUNT_REFRESH_INTERVAL: Final = 300

EVENT_TYPES: Final = (
    "event_recording",
    "motion",
    "alarm",
    "custom",
    "external",
    "analytics",
    "action_rule",
)

RECORDING_PROXY_URL: Final = (
    "/api/synology_surveillance_station/recording/{entry_id}/{recording_key}"
)
