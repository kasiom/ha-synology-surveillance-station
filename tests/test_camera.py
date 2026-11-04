"""Behavior tests for dynamically discovered camera streams."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.camera import DATA_COMPONENT, CameraEntityFeature  # noqa: E402
from homeassistant.components.stream import CONF_RTSP_TRANSPORT  # noqa: E402
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

from custom_components.synology_surveillance_station.api import (  # noqa: E402
    SynologyApiError,
    SynologyPermissionError,
)
from custom_components.synology_surveillance_station.camera import (  # noqa: E402
    SurveillanceLiveCamera,
    async_setup_entry,
)
from custom_components.synology_surveillance_station.models import (  # noqa: E402
    Camera,
    CameraLiveViewPaths,
    CameraStream,
    SurveillanceInfo,
)
from custom_components.synology_surveillance_station.runtime import (  # noqa: E402
    SurveillanceRuntime,
)

INFO = SurveillanceInfo(
    serial="TESTSERIAL",
    version="9.3.12139",
    path=None,
    camera_number=1,
    license_number=1,
    user_privilege=1,
    allow_snapshot=True,
)
STREAMS = (
    CameraStream(
        number=1,
        resolution="2560x1440",
        fps=20,
        bitrate_control=1,
        quality=5,
    ),
    CameraStream(
        number=2,
        resolution="1280x720",
        fps=15,
        bitrate_control=2,
        constant_bitrate=2048,
        quality=4,
    ),
    CameraStream(
        number=3,
        resolution="640x360",
        fps=10,
        bitrate_control=2,
        constant_bitrate=512,
        quality=3,
    ),
)
CAMERA = Camera(
    camera_id=8,
    name="Entrance",
    vendor="Example",
    model="Model 1",
    status=1,
    streams=STREAMS,
    high_profile_stream_no=1,
    medium_profile_stream_no=2,
    low_profile_stream_no=3,
)


def _runtime() -> SurveillanceRuntime:
    api = SimpleNamespace(
        host="nas.example.test",
        port=5001,
        async_get_snapshot=AsyncMock(return_value=(b"jpeg", "image/jpeg")),
        async_get_live_view_paths=AsyncMock(
            return_value={
                CAMERA.camera_id: CameraLiveViewPaths(
                    camera_id=CAMERA.camera_id,
                    rtsp="rtsp://nas.example.test/high",
                    rtsp_over_http="https://nas.example.test/rtsp.cgi",
                    mjpeg_http="https://nas.example.test/mjpeg.cgi",
                )
            }
        ),
    )
    runtime = SurveillanceRuntime(api, INFO, (CAMERA,))
    runtime.hub_device_id = "hub-device"
    return runtime


async def test_setup_creates_exactly_one_entity_per_reported_stream(hass) -> None:
    """The platform must not assume a fixed number or contiguous stream numbers."""
    sparse_camera = Camera(
        camera_id=9,
        name="Sparse profiles",
        status=1,
        streams=(STREAMS[0], STREAMS[2]),
        high_profile_stream_no=1,
        low_profile_stream_no=3,
    )
    runtime = _runtime()
    runtime.cameras = (CAMERA, sparse_camera)
    added: list[SurveillanceLiveCamera] = []

    await async_setup_entry(
        hass,
        SimpleNamespace(runtime_data=runtime),
        lambda entities: added.extend(entities),
    )

    assert [(entity._camera.camera_id, entity._stream.number) for entity in added] == [
        (8, 1),
        (8, 2),
        (8, 3),
        (9, 1),
        (9, 3),
    ]
    assert len({entity.unique_id for entity in added}) == 5


def test_profiles_expose_truthful_roles_capabilities_and_metadata() -> None:
    """Only profiles with a published live path should claim playback support."""
    runtime = _runtime()
    high, medium, low = (
        SurveillanceLiveCamera(runtime, CAMERA, stream) for stream in STREAMS
    )

    assert high.available
    assert high.supported_features == CameraEntityFeature.STREAM
    assert high.stream_options[CONF_RTSP_TRANSPORT] == "tcp"
    assert high.extra_state_attributes["profile_roles"] == ["high"]
    assert high.extra_state_attributes["live_source"] == "rtsp"

    assert medium.supported_features == CameraEntityFeature(0)
    assert medium.extra_state_attributes == {
        "stream_number": 2,
        "profile_roles": ["medium"],
        "resolution": "1280x720",
        "fps": 15,
        "bitrate_control": "constant",
        "constant_bitrate_kbps": 2048,
        "quality": 4,
        "live_source": "metadata_only",
        "available_stream_numbers": [1, 2, 3],
    }

    assert low.supported_features == CameraEntityFeature(0)
    assert low.extra_state_attributes["profile_roles"] == ["low"]
    assert low.extra_state_attributes["live_source"] == "mjpeg"


async def test_snapshot_profile_mapping_and_api_failure() -> None:
    """High, neutral, and low streams should request the matching snapshot profile."""
    runtime = _runtime()
    entities = [SurveillanceLiveCamera(runtime, CAMERA, stream) for stream in STREAMS]

    assert [await entity.async_camera_image() for entity in entities] == [
        b"jpeg",
        b"jpeg",
        b"jpeg",
    ]
    assert [call.args for call in runtime.api.async_get_snapshot.await_args_list] == [
        (CAMERA.camera_id, 0),
        (CAMERA.camera_id, 1),
        (CAMERA.camera_id, 2),
    ]

    runtime.api.async_get_snapshot.side_effect = SynologyApiError("snapshot failed")
    assert await entities[0].async_camera_image() is None


async def test_live_sources_use_api_paths_and_skip_metadata_only_profile(hass) -> None:
    """Each mapped profile should return its own path without substituting another stream."""
    runtime = _runtime()
    high, medium, low = (
        SurveillanceLiveCamera(runtime, CAMERA, stream) for stream in STREAMS
    )
    for entity in (high, medium, low):
        entity.hass = hass

    assert await high.stream_source() == "rtsp://nas.example.test/high"
    assert await low.stream_source() == "https://nas.example.test/mjpeg.cgi"
    assert await medium.stream_source() is None
    assert runtime.api.async_get_live_view_paths.await_count == 2

    runtime.api.async_get_live_view_paths.side_effect = SynologyApiError(
        "live view failed"
    )
    assert await high.stream_source() is None


async def test_live_sources_use_own_session_with_official_entry_present(hass) -> None:
    """A matching DSM entry must not be used, even when our API request fails."""
    runtime = _runtime()
    high = SurveillanceLiveCamera(runtime, CAMERA, STREAMS[0])
    low = SurveillanceLiveCamera(runtime, CAMERA, STREAMS[2])
    high.hass = hass
    low.hass = hass
    live_view = SimpleNamespace(
        rtsp="rtsp://nas.example.test/official-high",
        rtsp_http="https://nas.example.test/official-rtsp.cgi",
        mjpeg_http="https://nas.example.test/official-mjpeg.cgi",
    )
    surveillance = SimpleNamespace(
        get_camera_live_view_path=Mock(return_value=live_view)
    )
    official_entry = SimpleNamespace(
        data={CONF_HOST: "NAS.EXAMPLE.TEST"},
        runtime_data=SimpleNamespace(
            api=SimpleNamespace(surveillance_station=surveillance)
        ),
    )

    with patch.object(
        hass.config_entries, "async_entries", return_value=[official_entry]
    ) as entries:
        assert await high.stream_source() == "rtsp://nas.example.test/high"
        assert await low.stream_source() == "https://nas.example.test/mjpeg.cgi"
        assert [call.args for call in runtime.api.async_get_live_view_paths.await_args_list] == [
            ([CAMERA.camera_id],),
            ([CAMERA.camera_id],),
        ]
        runtime.api.async_get_live_view_paths.side_effect = SynologyApiError("Live View denied")
        assert await high.stream_source() is None
        assert await low.stream_source() is None

    entries.assert_not_called()
    surveillance.get_camera_live_view_path.assert_not_called()


async def test_main_stream_uses_websocket_when_live_path_is_denied(hass) -> None:
    """Only an unavailable RTSP path switches the main stream to the loopback source."""
    runtime = _runtime()
    runtime.api.use_ssl = True
    runtime.api.async_get_live_view_paths.side_effect = SynologyPermissionError("Denied", 105)
    camera = SurveillanceLiveCamera(runtime, CAMERA, STREAMS[0])
    camera.hass = hass
    with patch.object(
        runtime.web_stream, "async_source", AsyncMock(return_value="http://127.0.0.1:1234/source")
    ) as source:
        assert await camera.stream_source() == "http://127.0.0.1:1234/source"
    source.assert_awaited_once_with(CAMERA.camera_id, 0)
    assert camera.extra_state_attributes["live_source"] == "wss"


async def test_low_stream_advertises_hls_when_mjpeg_is_denied(hass) -> None:
    """A live dashboard card should use HLS and request the actual low profile."""
    runtime = _runtime()
    runtime.api.use_ssl = True
    runtime.api.async_get_live_view_paths.side_effect = SynologyPermissionError("Denied", 105)
    low = SurveillanceLiveCamera(runtime, CAMERA, STREAMS[2])
    assert await async_setup_component(hass, "camera", {})
    await hass.data[DATA_COMPONENT].async_add_entities([low])
    assert low.supported_features == CameraEntityFeature.STREAM
    with patch.object(
        runtime.web_stream, "async_source", AsyncMock(return_value="http://127.0.0.1:1234/low")
    ) as source:
        assert await low.stream_source() == "http://127.0.0.1:1234/low"
    source.assert_awaited_once_with(CAMERA.camera_id, 2)


async def test_live_profiles_follow_camera_status_without_reload(hass) -> None:
    """Publish offline, recovery and stale status changes to Home Assistant."""
    camera = replace(CAMERA, status=3)
    runtime = SurveillanceRuntime(_runtime().api, INFO, (camera,))
    high = SurveillanceLiveCamera(runtime, camera, STREAMS[0])
    low = SurveillanceLiveCamera(runtime, camera, STREAMS[2])
    assert await async_setup_component(hass, "camera", {})
    await hass.data[DATA_COMPONENT].async_add_entities([high, low])
    entity_ids = (high.entity_id, low.entity_id)

    def states() -> tuple[str, ...]:
        return tuple(hass.states.get(entity_id).state for entity_id in entity_ids)

    assert states() == (STATE_UNAVAILABLE, STATE_UNAVAILABLE)
    for reported_statuses, expected in (
        ({camera.camera_id: 1}, "idle"),
        ({camera.camera_id: 3}, STATE_UNAVAILABLE),
        ({camera.camera_id: 1}, "idle"),
        ({}, STATE_UNAVAILABLE),
        ({camera.camera_id: 1}, "idle"),
    ):
        runtime.complete_metadata_refresh(datetime.now(UTC), {}, reported_statuses, set())
        assert states() == (expected, expected)

    runtime.mark_camera_statuses_unavailable()
    assert states() == (STATE_UNAVAILABLE, STATE_UNAVAILABLE)


async def test_low_profile_proxies_mjpeg_directly(hass) -> None:
    """Low-bandwidth playback should proxy MJPEG instead of invoking HLS."""
    runtime = _runtime()
    low = SurveillanceLiveCamera(runtime, CAMERA, STREAMS[2])
    low.hass = hass
    request = Mock()
    upstream_request = Mock()
    response = Mock()
    websession = SimpleNamespace(get=Mock(return_value=upstream_request))

    with (
        patch.object(
            low,
            "stream_source",
            AsyncMock(return_value="https://nas.example.test/mjpeg.cgi"),
        ),
        patch(
            "custom_components.synology_surveillance_station.camera.async_get_clientsession",
            return_value=websession,
        ) as get_session,
        patch(
            "custom_components.synology_surveillance_station.camera.async_aiohttp_proxy_web",
            AsyncMock(return_value=response),
        ) as proxy,
    ):
        assert await low.handle_async_mjpeg_stream(request) is response

    get_session.assert_called_once_with(hass, verify_ssl=False)
    websession.get.assert_called_once_with("https://nas.example.test/mjpeg.cgi")
    proxy.assert_awaited_once_with(hass, request, upstream_request)
