"""Browse the response format reported in issue #8 through Home Assistant."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")

from custom_components.synology_surveillance_station.const import DOMAIN  # noqa: E402
from custom_components.synology_surveillance_station.media_source import (  # noqa: E402
    SurveillanceMediaSource,
)
from custom_components.synology_surveillance_station.models import (  # noqa: E402
    SurveillanceInfo,
)
from custom_components.synology_surveillance_station.runtime import (  # noqa: E402
    SurveillanceRuntime,
)

from .test_api import FakeResponse, FakeSession, api_module  # noqa: E402


async def test_issue8_media_browse_preserves_names_times_order_and_playback(hass) -> None:
    """The real browser shows local dates and retains playable recording identities."""
    await hass.config.async_set_time_zone("Europe/Prague")
    explicit_start = datetime(2026, 11, 18, 12, 0, tzinfo=UTC).timestamp()
    session = FakeSession([
        FakeResponse({"success": True, "data": {"cameras": [
            {"id": 8, "newName": "Backyard Camera"},
        ]}}),
        FakeResponse({"success": True, "data": {"recordings": [
            {"id": 4},
            {
                "id": 2,
                "filePath": "20261118PM/Backyard_Camera-20261118-145353-1795031633369-7.mp4",
                "dsId": 6, "mountId": 1,
            },
            {
                "id": 1, "startTime": explicit_start, "stopTime": explicit_start + 65,
                "filePath": "Conflicting-20261118-235959-1.mp4",
            },
            {"id": 3},
            {"id": 5, "filePath": "Camera-20261118-145353-1795031633369-7.mp4"},
        ]}}),
    ])
    api = api_module.SynologySurveillanceApi(
        session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True,
        recording_timezone=ZoneInfo(hass.config.time_zone),
    )
    api.apis = {
        name: api_module.ApiDescriptor(name, "entry.cgi", 1, 9)
        for name in ("SYNO.SurveillanceStation.Camera", "SYNO.SurveillanceStation.Recording")
    }
    cameras = await api.async_list_cameras()
    info = SurveillanceInfo("TEST", "9.3.12143", None, 1, 1, 4, True)
    runtime = SurveillanceRuntime(api, info, cameras)
    hass.data[DOMAIN] = {"entry": runtime}
    source = SurveillanceMediaSource(hass)

    root = await source.async_browse_media(SimpleNamespace(identifier=None))
    assert [item.title for item in root.children] == ["Backyard Camera"]

    items = await source.async_browse_media(SimpleNamespace(identifier="RANGE|entry|8|30"))
    assert [item.title for item in items.children] == [
        "2026-11-18 14:53:53",
        "2026-11-18 14:53:53",
        "2026-11-18 13:00:00 · 1:05",
        "Recording 4",
        "Recording 3",
    ]
    assert [item.identifier for item in items.children] == [
        "REC|entry|0:0:5", "REC|entry|6:1:2", "REC|entry|0:0:1",
        "REC|entry|0:0:4", "REC|entry|0:0:3",
    ]
    media = await source.async_resolve_media(items.children[1])
    assert media.mime_type == "video/mp4"
    assert "6:1:2" in media.url
    assert all(item.can_play for item in items.children)
