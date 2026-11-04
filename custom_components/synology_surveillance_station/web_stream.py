"""Expose Synology's WebSocket video to Home Assistant's stream worker."""

from __future__ import annotations

import asyncio
import logging
import secrets

import aiohttp
from aiohttp import web

from .api import SynologyApiError, SynologyConnectionError, SynologySurveillanceApi

_LOGGER = logging.getLogger(__name__)
_VIDEO_TIMEOUT = 15
_MP4_INIT_BOXES = {b"ftyp", b"moov"}
_MP4_BOXES = _MP4_INIT_BOXES | {b"moof", b"styp", b"mdat", b"free", b"sidx", b"emsg"}


def _video_payload(message: bytes) -> tuple[bool, bytes]:
    """Read the length-prefixed Synology header and extract MP4 video only."""
    header_end = int.from_bytes(message[:4], "big")
    if not 4 < header_end <= len(message):
        return False, b""
    fields = dict(
        field.split(b"=", 1)
        for field in message[4:header_end].split(b"&")
        if b"=" in field
    )
    if fields.get(b"close"):
        return True, b""
    if fields.get(b"mediaType") != b"1":
        return False, b""
    for start in (header_end, header_end + 4):
        if message[start + 4 : start + 8] in _MP4_BOXES:
            return False, message[start:]
    return False, b""


class SurveillanceWebStream:
    """Serve video on a private, on-demand loopback endpoint."""

    def __init__(self, api: SynologySurveillanceApi) -> None:
        self._api = api
        self._runner: web.AppRunner | None = None
        self._lock = asyncio.Lock()
        self._token = secrets.token_urlsafe(24)
        self._cameras: set[int] = set()
        self._closed = False

    async def async_source(self, camera_id: int) -> str | None:
        """Return a local source URL for a camera already selected by HA."""
        async with self._lock:
            if self._closed:
                return None
            if self._runner is None:
                app = web.Application()
                app.router.add_get(
                    f"/{self._token}/{{camera_id:[0-9]+}}", self._async_video, allow_head=False
                )
                runner = web.AppRunner(
                    app, access_log=None, handler_cancellation=True, shutdown_timeout=1
                )
                try:
                    await runner.setup()
                    await web.TCPSite(runner, "127.0.0.1", 0).start()
                except (OSError, asyncio.CancelledError):
                    await runner.cleanup()
                    raise
                self._runner = runner
            self._cameras.add(camera_id)
            port = self._runner.addresses[0][1]
            return f"http://127.0.0.1:{port}/{self._token}/{camera_id}"

    async def async_close(self) -> None:
        """Stop serving sources and close active NAS connections on unload."""
        async with self._lock:
            self._closed = True
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None

    async def _async_video(self, request: web.Request) -> web.StreamResponse:
        camera_id = int(request.match_info["camera_id"])
        if camera_id not in self._cameras:
            return web.Response(status=404)
        response = web.StreamResponse(
            headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"}
        )
        loop = asyncio.get_running_loop()
        try:
            async with self._api.async_open_web_stream(camera_id) as stream:
                async with asyncio.timeout(_VIDEO_TIMEOUT) as video_timeout:
                    async for message in stream:
                        if message.type != aiohttp.WSMsgType.BINARY:
                            continue
                        closed, payload = _video_payload(message.data)
                        if closed:
                            break
                        if not payload:
                            continue
                        if not response.prepared:
                            if payload[4:8] not in _MP4_INIT_BOXES:
                                continue
                            await response.prepare(request)
                        await response.write(payload)
                        video_timeout.reschedule(loop.time() + _VIDEO_TIMEOUT)
                raise SynologyConnectionError("Synology closed the web stream")
        except (aiohttp.ClientError, OSError, SynologyApiError, TimeoutError) as err:
            _LOGGER.debug("Web stream for camera %s ended (%s)", camera_id, type(err).__name__)
        # HA reopens the same source after EOF and gets a new NAS login and MP4 header.
        return response if response.prepared else web.Response(status=502)
