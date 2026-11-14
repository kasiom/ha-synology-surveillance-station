"""Expose Synology's WebSocket video to Home Assistant's stream worker."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import suppress

import aiohttp
from aiohttp import web

from .api import SynologyApiError, SynologyConnectionError, SynologySurveillanceApi

_LOGGER = logging.getLogger(__name__)
_VIDEO_TIMEOUT = 15
_MPEG_TS_PACKET_SIZE = 188
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

    def __init__(self, api: SynologySurveillanceApi, ffmpeg_binary: str = "ffmpeg") -> None:
        self._api = api
        self._ffmpeg_binary = ffmpeg_binary
        self._runner: web.AppRunner | None = None
        self._lock = asyncio.Lock()
        self._token = secrets.token_urlsafe(24)
        self._sources: set[tuple[int, int]] = set()
        self._closed = False

    async def async_source(self, camera_id: int, profile: int) -> str | None:
        """Return a local source URL for a camera already selected by HA."""
        async with self._lock:
            if self._closed:
                return None
            if self._runner is None:
                app = web.Application()
                source_path = f"/{self._token}/{{camera_id:[0-9]+}}/{{profile:[02]}}"
                app.router.add_get(f"{source_path}/live.ts", self._async_mpegts, allow_head=False)
                app.router.add_get(f"{source_path}/source.mp4", self._async_video, allow_head=False)
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
            self._sources.add((camera_id, profile))
            return f"{self._source_url(camera_id, profile)}/live.ts"

    def _source_url(self, camera_id: int, profile: int) -> str:
        assert self._runner is not None
        port = self._runner.addresses[0][1]
        return f"http://127.0.0.1:{port}/{self._token}/{camera_id}/{profile}"

    async def async_close(self) -> None:
        """Stop serving sources and close active NAS connections on unload."""
        async with self._lock:
            self._closed = True
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None

    async def _async_mpegts(self, request: web.Request) -> web.StreamResponse:
        """Remux fragmented MP4 so both the HA stream worker and go2rtc can read it."""
        camera_id = int(request.match_info["camera_id"])
        profile = int(request.match_info["profile"])
        if (camera_id, profile) not in self._sources:
            return web.Response(status=404)
        source = f"{self._source_url(camera_id, profile)}/source.mp4"
        response = web.StreamResponse(
            headers={"Content-Type": "video/mp2t", "Cache-Control": "no-store"}
        )
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._ffmpeg_binary,
                "-nostdin", "-loglevel", "error",
                "-f", "mp4", "-i", source,
                "-map", "0:v:0", "-c:v", "copy",
                "-f", "mpegts", "-flush_packets", "1", "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert process.stdout is not None
            async with asyncio.timeout(_VIDEO_TIMEOUT):
                first_packet = await process.stdout.readexactly(_MPEG_TS_PACKET_SIZE)
            await response.prepare(request)
            await response.write(first_packet)
            while True:
                async with asyncio.timeout(_VIDEO_TIMEOUT):
                    chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                await response.write(chunk)
        except (EOFError, OSError, TimeoutError) as err:
            _LOGGER.debug("MPEG-TS stream for camera %s ended (%s)", camera_id, type(err).__name__)
        finally:
            if process is not None:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                # Drain the pipe so a child killed during a slow write can be reaped.
                await process.communicate()
        return response if response.prepared else web.Response(status=502)

    async def _async_video(self, request: web.Request) -> web.StreamResponse:
        camera_id = int(request.match_info["camera_id"])
        profile = int(request.match_info["profile"])
        if (camera_id, profile) not in self._sources:
            return web.Response(status=404)
        response = web.StreamResponse(
            headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"}
        )
        loop = asyncio.get_running_loop()
        try:
            async with self._api.async_open_web_stream(camera_id, profile) as stream:
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
