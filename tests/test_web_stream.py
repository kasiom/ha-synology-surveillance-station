"""Exercise Synology's WebSocket fallback through an actual local HTTP source."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from .helpers import load_module

api_module = load_module("api")
web_stream_module = load_module("web_stream")

INIT = b"\x00\x00\x00\x08ftyp\x00\x00\x00\x08moov"
MEDIA = b"\x00\x00\x00\x08moof\x00\x00\x00\x08mdat"


def _packet(payload: bytes, fields: bytes = b"mediaType=1") -> bytes:
    return (4 + len(fields)).to_bytes(4, "big") + fields + payload


@pytest.fixture
async def nas() -> AsyncIterator[tuple[int, list[str], list[str]]]:
    """Simulate the documented ThirdParty login, camera list and stream endpoints."""
    logins: list[str] = []
    logouts: list[str] = []

    async def login(request: web.Request) -> web.Response:
        data = await request.post()
        assert data == {"account": "viewer", "passwd": "example"}
        sid = f"session-{len(logins) + 1}"
        logins.append(sid)
        return web.json_response({"success": True, "data": {"sid": sid}})

    async def camera_list(request: web.Request) -> web.Response:
        assert request.query["_sid"] in logins
        return web.json_response({"success": True, "data": {"cameras": [{"camId": "8"}]}})

    async def logout(request: web.Request) -> web.Response:
        logouts.append(request.query["_sid"])
        return web.json_response({"success": True})

    async def video(request: web.Request) -> web.WebSocketResponse:
        assert request.query["_sid"] in logins
        assert request.query["camId"] == "8"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(_packet(MEDIA))
        await ws.send_bytes(_packet(INIT))
        await ws.send_bytes(_packet(MEDIA, b"mediaType=2"))
        await ws.send_bytes(_packet(MEDIA))
        await ws.send_bytes(_packet(b"", b"close=1"))
        await ws.close()
        return ws

    app = web.Application()
    path = "/webapi/SurveillanceStation/ThirdParty"
    app.router.add_post(f"{path}/Auth/Login/v1", login)
    app.router.add_get(f"{path}/Camera/List/v1", camera_list)
    app.router.add_get(f"{path}/Auth/Logout/v1", logout)
    app.router.add_get("/ss_webstream_task/", video)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    try:
        yield runner.addresses[0][1], logins, logouts
    finally:
        await runner.cleanup()


async def test_loopback_stream_reopens_with_new_session_and_mp4_header(nas) -> None:
    port, logins, logouts = nas
    async with aiohttp.ClientSession() as http:
        api = api_module.SynologySurveillanceApi(
            http, "127.0.0.1", port, "viewer", "example", use_ssl=False, verify_ssl=True
        )
        bridge = web_stream_module.SurveillanceWebStream(api)
        try:
            source = await bridge.async_source(8)
            assert source.startswith("http://127.0.0.1:")
            for _ in range(2):
                async with http.get(source) as response:
                    assert response.status == 200
                    assert response.content_type == "video/mp4"
                    assert await response.read() == INIT + MEDIA
            async with http.get(source.removesuffix("/8") + "/9") as response:
                assert response.status == 404
            assert logins == logouts == ["session-1", "session-2"]
        finally:
            await bridge.async_close()
        assert await bridge.async_source(8) is None
