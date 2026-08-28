"""Authenticated HTTP views for recording playback."""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView

from .api import SynologyApiError
from .const import DOMAIN, RECORDING_PROXY_URL
from .runtime import SurveillanceRuntime

_LOGGER = logging.getLogger(__name__)
_PASSTHROUGH_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control",
)


class RecordingProxyView(HomeAssistantView):
    """Stream a Synology recording through HA authentication."""

    url = RECORDING_PROXY_URL
    name = "api:synology_surveillance_station:recording"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, recording_key: str
    ) -> web.StreamResponse:
        """Proxy byte ranges without revealing the NAS session or password."""
        hass = request.app[KEY_HASS]
        runtime: SurveillanceRuntime | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if runtime is None or (recording := runtime.recordings.get(recording_key)) is None:
            raise web.HTTPNotFound
        try:
            upstream = await runtime.api.async_open_recording_stream(
                recording, request.headers.get("Range")
            )
        except SynologyApiError as err:
            raise web.HTTPBadGateway from err
        if upstream.status not in {200, 206}:
            _LOGGER.debug(
                "Recording %s upstream returned HTTP %s",
                recording_key,
                upstream.status,
            )
            upstream.release()
            raise web.HTTPBadGateway
        headers = {
            name: upstream.headers[name]
            for name in _PASSTHROUGH_HEADERS
            if name in upstream.headers
        }
        response = web.StreamResponse(status=upstream.status, headers=headers)
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
        except ConnectionResetError:
            _LOGGER.debug(
                "Client disconnected while reading recording %s", recording_key
            )
        finally:
            upstream.release()
        return response
