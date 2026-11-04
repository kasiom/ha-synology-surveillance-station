"""Minimal asynchronous client for the Surveillance Station Web API."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import API_AUTH, API_CAMERA, API_INFO, API_RECORDING
from .models import (
    ApiDescriptor,
    Camera,
    CameraLiveViewPaths,
    CameraStream,
    Recording,
    SurveillanceInfo,
)

_SESSION_ERROR_CODES = {106, 107}
_AUTH_ERROR_CODES = {400, 401, 402, 403, 404, 406, 407}
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(
    total=None, connect=15, sock_connect=15, sock_read=None
)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_WEB_API_TIMEOUT = aiohttp.ClientTimeout(total=5)
_THIRD_PARTY_PATH = "/webapi/SurveillanceStation/ThirdParty"


@dataclass
class _WebStreamSession:
    auth: dict[str, str]
    camera_ids: set[int]
    users: int = 0


class SynologyApiError(Exception):
    """Base exception returned by the Surveillance Station API."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class SynologyAuthError(SynologyApiError):
    """Credentials or authentication requirements are invalid."""


class SynologyPermissionError(SynologyApiError):
    """The user cannot access a requested Surveillance Station resource."""


class SynologyConnectionError(SynologyApiError):
    """The DiskStation could not be reached or returned invalid data."""


class SynologyUnsupportedError(SynologyApiError):
    """A required Surveillance Station API is unavailable."""


def normalize_host(value: str) -> str:
    """Return a bare host name or IP address and reject paths/userinfo."""
    raw = value.strip()
    if not raw:
        raise ValueError("Host is required")
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be included in the host")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Host must not include a path, query, or fragment")
    if parsed.hostname is None:
        raise ValueError("Invalid host")
    return parsed.hostname


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_datetime(value: Any) -> datetime | None:
    timestamp = _as_int(value)
    if timestamp is None or timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _first(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


class SynologySurveillanceApi:
    """Talk to a single Surveillance Station installation."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_ssl: bool,
        verify_ssl: bool,
    ) -> None:
        self._session = session
        self.host = normalize_host(host)
        self.port = int(port)
        self.username = username
        self._password = password
        self.use_ssl = use_ssl
        self.verify_ssl = verify_ssl
        bracketed = f"[{self.host}]" if ":" in self.host else self.host
        self.base_url = f"{'https' if use_ssl else 'http'}://{bracketed}:{self.port}"
        self.apis: dict[str, ApiDescriptor] = {}
        self.sid: str | None = None
        self.synotoken: str | None = None
        self.info: SurveillanceInfo | None = None
        self.cameras: tuple[Camera, ...] = ()
        self._web_session: _WebStreamSession | None = None
        self._web_session_lock = asyncio.Lock()

    @property
    def _ssl_argument(self) -> bool | None:
        return None if self.verify_ssl else False

    async def async_initialize(self) -> tuple[SurveillanceInfo, tuple[Camera, ...]]:
        """Discover the API, authenticate, and load the camera inventory."""
        await self.async_discover()
        await self.async_login()
        self.info = await self.async_get_info()
        self.cameras = await self.async_list_cameras()
        return self.info, self.cameras

    async def async_discover(self) -> dict[str, ApiDescriptor]:
        """Discover paths and supported versions instead of hard-coding CGI paths."""
        payload = await self._post_json(
            "/webapi/query.cgi",
            {
                "api": "SYNO.API.Info",
                "version": 1,
                "method": "query",
                "query": ",".join(
                    (
                        API_AUTH,
                        API_INFO,
                        API_CAMERA,
                        API_RECORDING,
                    )
                ),
            },
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise SynologyConnectionError("API discovery response has no data object")
        self.apis = {
            name: ApiDescriptor(
                name=name,
                path=str(item["path"]),
                min_version=int(item.get("minVersion", 1)),
                max_version=int(item.get("maxVersion", 1)),
            )
            for name, item in data.items()
            if isinstance(item, Mapping) and item.get("path")
        }
        for required in (API_AUTH, API_INFO, API_CAMERA, API_RECORDING):
            if required not in self.apis:
                raise SynologyUnsupportedError(f"Required API is missing: {required}")
        minimum_versions = {API_CAMERA: 9, API_RECORDING: 6}
        for api_name, minimum_version in minimum_versions.items():
            if self.apis[api_name].max_version < minimum_version:
                raise SynologyUnsupportedError(
                    f"{api_name} version {minimum_version} or newer is required"
                )
        return self.apis

    async def async_login(self) -> None:
        """Create a dedicated Surveillance Station API session."""
        descriptor = self._descriptor(API_AUTH)
        self.sid = None
        self.synotoken = None
        payload = await self._post_json(
            f"/webapi/{descriptor.path.lstrip('/')}",
            {
                "api": API_AUTH,
                "version": descriptor.max_version,
                "method": "login",
                "account": self.username,
                "passwd": self._password,
                "session": "SurveillanceStation",
                "format": "sid",
                "enable_syno_token": "yes",
            },
            auth_context=True,
        )
        data = payload.get("data")
        if not isinstance(data, Mapping) or not data.get("sid"):
            raise SynologyAuthError("Login response did not contain a session ID")
        self.sid = str(data["sid"])
        token = _first(data, "synotoken", "SynoToken")
        self.synotoken = str(token) if token else None

    async def async_logout(self) -> None:
        """Close the API session; failure is intentionally non-fatal."""
        if self.sid is None or API_AUTH not in self.apis:
            return
        try:
            await self._request_json(API_AUTH, "logout", {}, retry_auth=False)
        except SynologyApiError:
            pass
        finally:
            self.sid = None
            self.synotoken = None

    async def async_get_info(self) -> SurveillanceInfo:
        """Return server identity, version, and privilege information."""
        data = await self._request_data(API_INFO, "GetInfo")
        version_value = data.get("version", "unknown")
        if isinstance(version_value, Mapping):
            version = ".".join(
                str(version_value.get(part, 0)) for part in ("major", "minor", "build")
            )
        else:
            version = str(version_value)
        return SurveillanceInfo(
            serial=str(data["serial"]) if data.get("serial") else None,
            version=version,
            path=str(data["path"]) if data.get("path") else None,
            camera_number=_as_int(_first(data, "cameraNumber", "camera_number"), 0) or 0,
            license_number=_as_int(_first(data, "licenseNumber", "license_number"), 0) or 0,
            user_privilege=_as_int(_first(data, "userPriv", "userPrivilege")),
            allow_snapshot=(
                bool(data["allowSnapshot"]) if "allowSnapshot" in data else None
            ),
        )

    async def async_list_cameras(self) -> tuple[Camera, ...]:
        """List basic camera metadata used to create stable HA entities."""
        data = await self._request_data(
            API_CAMERA,
            "List",
            {
                "offset": 0,
                "limit": -1,
                "basic": "true",
                "streamInfo": "true",
                "blPrivilege": "true",
                "privCamType": 3,
            },
        )
        raw_cameras = data.get("cameras", [])
        if not isinstance(raw_cameras, list):
            raise SynologyConnectionError("Camera list has an invalid shape")
        cameras: list[Camera] = []
        for raw in raw_cameras:
            if not isinstance(raw, Mapping):
                continue
            camera_id = _as_int(_first(raw, "id", "cameraId"))
            if camera_id is None:
                continue
            streams: list[CameraStream] = []
            for key, value in raw.items():
                match = re.fullmatch(r"stream(\d+)", str(key), re.IGNORECASE)
                if match is None or not isinstance(value, Mapping):
                    continue
                streams.append(
                    CameraStream(
                        number=int(match.group(1)),
                        resolution=(
                            str(value["resolution"])
                            if value.get("resolution")
                            else None
                        ),
                        fps=_as_int(value.get("fps")),
                        bitrate_control=_as_int(value.get("bitrateCtrl")),
                        constant_bitrate=_as_int(value.get("constantBitrate")),
                        quality=_as_int(value.get("quality")),
                    )
                )
            cameras.append(
                Camera(
                    camera_id=camera_id,
                    name=str(raw.get("name") or f"Camera {camera_id}"),
                    vendor=str(raw["vendor"]) if raw.get("vendor") else None,
                    model=str(raw["model"]) if raw.get("model") else None,
                    status=_as_int(raw.get("status")),
                    ds_id=_as_int(_first(raw, "dsId", "serverId")),
                    video_codec=_as_int(
                        _first(raw, "videoCodec", "video_codec", "video_type", "codec")
                    ),
                    ip_address=(
                        str(_first(raw, "ip", "ipAddress"))
                        if _first(raw, "ip", "ipAddress")
                        else None
                    ),
                    streams=tuple(sorted(streams, key=lambda item: item.number)),
                    high_profile_stream_no=_as_int(raw.get("highProfileStreamNo")),
                    medium_profile_stream_no=_as_int(
                        raw.get("mediumProfileStreamNo")
                    ),
                    low_profile_stream_no=_as_int(raw.get("lowProfileStreamNo")),
                )
            )
        return tuple(cameras)

    async def async_get_live_view_paths(
        self, camera_ids: list[int] | tuple[int, ...]
    ) -> dict[int, CameraLiveViewPaths]:
        """Return fresh proxy paths for the requested cameras.

        Surveillance Station exposes one native RTSP live-view path and one
        compatibility MJPEG path per camera. Numbered stream metadata is
        discovered separately by :meth:`async_list_cameras`.
        """
        if not camera_ids:
            return {}
        payload = await self._request_json(
            API_CAMERA,
            "GetLiveViewPath",
            {"idList": ",".join(str(camera_id) for camera_id in camera_ids)},
        )
        raw_paths = payload.get("data")
        if not isinstance(raw_paths, list):
            raise SynologyConnectionError(
                f"{API_CAMERA}.GetLiveViewPath returned no path list"
            )
        result: dict[int, CameraLiveViewPaths] = {}
        for raw in raw_paths:
            if not isinstance(raw, Mapping):
                continue
            camera_id = _as_int(raw.get("id"))
            if camera_id is None:
                continue
            result[camera_id] = CameraLiveViewPaths(
                camera_id=camera_id,
                rtsp=str(raw["rtspPath"]) if raw.get("rtspPath") else None,
                rtsp_over_http=(
                    str(raw["rtspOverHttpPath"])
                    if raw.get("rtspOverHttpPath")
                    else None
                ),
                mjpeg_http=(
                    str(raw["mjpegHttpPath"])
                    if raw.get("mjpegHttpPath")
                    else None
                ),
            )
        return result

    @asynccontextmanager
    async def async_open_web_stream(
        self, camera_id: int
    ) -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
        """Open Synology's documented fragmented MP4 live stream."""
        async with self._web_session_lock:
            if self._web_session is None:
                self._web_session = await self._async_start_web_session()
            session = self._web_session
            session.users += 1
        disconnected = False
        try:
            if camera_id not in session.camera_ids:
                raise SynologyPermissionError("Camera is not available for web streaming")
            async with asyncio.timeout(10):
                stream = await self._session.ws_connect(
                    f"{self.base_url}/ss_webstream_task/",
                    params={**session.auth, "camId": str(camera_id)},
                    ssl=self.verify_ssl,
                    timeout=aiohttp.ClientWSTimeout(ws_close=2),
                    max_msg_size=8 * 1024 * 1024,
                )
            async with stream:
                keepalive = asyncio.create_task(self._async_web_stream_keepalive(stream, session))
                try:
                    yield stream
                finally:
                    keepalive.cancel()
                    with suppress(asyncio.CancelledError):
                        await keepalive
        except (aiohttp.ClientError, SynologyConnectionError, TimeoutError):
            disconnected = True
            raise
        finally:
            async with self._web_session_lock:
                session.users -= 1
                if self._web_session is session and (disconnected or not session.users):
                    self._web_session = None
                if not session.users:
                    await self._async_logout_web_session(session.auth)

    async def _async_start_web_session(self) -> _WebStreamSession:
        login = await self._async_third_party_request(
            "Auth/Login/v1",
            {"account": self.username, "passwd": self._password},
            method="POST",
        )
        sid = login.get("sid") if isinstance(login, Mapping) else None
        if not isinstance(sid, str) or not sid:
            raise SynologyAuthError("Web stream login did not return a session ID")
        auth = {"_sid": sid}
        try:
            listing = await self._async_third_party_request("Camera/List/v1", auth)
            cameras = listing.get("cameras") if isinstance(listing, Mapping) else None
            if not isinstance(cameras, list):
                raise SynologyConnectionError("Web stream API returned an invalid camera list")
            return _WebStreamSession(auth, {
                camera_id for camera in cameras
                if isinstance(camera, Mapping)
                and (camera_id := _as_int(camera.get("camId"))) is not None
            })
        except (SynologyApiError, asyncio.CancelledError):
            await self._async_logout_web_session(auth)
            raise

    async def _async_logout_web_session(self, auth: dict[str, str]) -> None:
        with suppress(SynologyApiError):
            await self._async_third_party_request("Auth/Logout/v1", auth)

    async def _async_web_stream_keepalive(
        self, stream: aiohttp.ClientWebSocketResponse, session: _WebStreamSession
    ) -> None:
        try:
            while not stream.closed:
                for _ in range(6):
                    await asyncio.sleep(10)
                    await stream.send_str("keepAlive")
                await self._async_third_party_request("Recording/Keepalive/v5", session.auth)
        except (aiohttp.ClientError, OSError, SynologyApiError, TimeoutError):
            await stream.close()

    async def _async_third_party_request(
        self, endpoint: str, values: dict[str, str], *, method: str = "GET"
    ) -> Any:
        """Call the published ThirdParty API independently of the CGI session."""
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{_THIRD_PARTY_PATH}/{endpoint}",
                params=values if method == "GET" else None,
                data=values if method == "POST" else None,
                ssl=self._ssl_argument,
                timeout=_WEB_API_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise SynologyConnectionError(f"Web stream API returned HTTP {response.status}")
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise SynologyConnectionError("Web stream API request failed") from None
        if not isinstance(payload, Mapping):
            raise SynologyConnectionError("Web stream API returned invalid data")
        if payload.get("success") is not True:
            self._raise_api_error(
                "Surveillance Station ThirdParty", endpoint, self._error_code(payload)
            )
        return payload.get("data")

    async def async_list_recordings(
        self,
        camera_id: int,
        from_time: datetime,
        to_time: datetime,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[Recording, ...]:
        """List recordings for one camera and UTC time range."""
        data = await self._request_data(
            API_RECORDING,
            "List",
            {
                "cameraIds": str(camera_id),
                "fromTime": int(from_time.timestamp()),
                "toTime": int(to_time.timestamp()),
                "offset": max(0, offset),
                "limit": limit,
            },
        )
        raw_recordings = data.get("recordings", data.get("events", []))
        if not isinstance(raw_recordings, list):
            raise SynologyConnectionError("Recording list has an invalid shape")
        result: list[Recording] = []
        for raw in raw_recordings:
            if not isinstance(raw, Mapping):
                continue
            recording_id = _as_int(_first(raw, "id", "recordingId", "eventId"))
            raw_camera_id = _as_int(_first(raw, "cameraId", "camId"), camera_id)
            if recording_id is None or raw_camera_id is None:
                continue
            result.append(
                Recording(
                    recording_id=recording_id,
                    camera_id=raw_camera_id,
                    camera_name=(
                        str(_first(raw, "cameraName", "camName"))
                        if _first(raw, "cameraName", "camName")
                        else None
                    ),
                    start_time=_as_datetime(_first(raw, "startTime", "start_time")),
                    stop_time=_as_datetime(_first(raw, "stopTime", "endTime", "stop_time")),
                    video_codec=_as_int(
                        _first(raw, "videoCodec", "video_codec", "video_type", "codec")
                    ),
                    ds_id=_as_int(_first(raw, "dsId", "serverId")),
                    mount_id=_as_int(_first(raw, "mountId", "mountID")),
                    size_bytes=_as_int(
                        _first(
                            raw,
                            "sizeByte",
                            "sizeBytes",
                            "event_size_bytes",
                            "size",
                        )
                    ),
                    reason=_first(raw, "reason", "recordingReason"),
                    file_path=(
                        str(_first(raw, "filePath", "path"))
                        if _first(raw, "filePath", "path")
                        else None
                    ),
                    locked=bool(raw.get("locked", False)),
                )
            )
        return tuple(result)

    async def async_count_recordings(
        self,
        camera_id: int,
        from_time: datetime,
        to_time: datetime,
    ) -> int:
        """Return an exact recording count without downloading the full list."""
        data = await self._request_data(
            API_RECORDING,
            "List",
            {
                "cameraIds": str(camera_id),
                "fromTime": int(from_time.timestamp()),
                "toTime": int(to_time.timestamp()),
                "offset": 0,
                "limit": 1,
            },
        )
        total = _as_int(data.get("total"))
        if total is not None:
            return max(0, total)
        raise SynologyConnectionError(
            "Recording count response did not include an exact total"
        )

    async def async_count_detection_recordings(
        self,
        camera_id: int,
        from_time: datetime,
        to_time: datetime,
    ) -> int:
        """Count event-triggered recordings without relying on absent List fields."""
        data = await self._request_data(
            API_RECORDING,
            "CountByCategory",
            {
                "cameraIds": str(camera_id),
                "fromTime": int(from_time.timestamp()),
                "toTime": int(to_time.timestamp()),
                "reason": "2,3,4,6,7,9",
                "limit": 0,
                "locked": 0,
                "evtSrcType": 0,
                "evtSrcId": -1,
                "blIncludeSnapshot": "false",
                "includeAllCam": "false",
                "timezoneOffset": 0,
            },
            version=4,
        )
        total = _as_int(data.get("total"))
        if total is None:
            raise SynologyConnectionError(
                "Detection recording count response has an invalid shape"
            )
        return max(0, total)

    async def async_get_camera_statuses(self) -> dict[int, int]:
        """Return documented version-9 camera states using the permitted Camera API."""
        cameras = await self.async_list_cameras()
        return {
            camera.camera_id: camera.status
            for camera in cameras
            if camera.status is not None
        }

    async def async_get_snapshot(
        self, camera_id: int, profile_type: int = 1
    ) -> tuple[bytes, str]:
        """Fetch a current camera JPEG using the authenticated API session."""
        response = await self._request_binary(
            API_CAMERA,
            "GetSnapshot",
            {"id": camera_id, "profileType": profile_type},
        )
        return response

    async def async_open_recording_stream(
        self,
        recording: Recording,
        range_header: str | None = None,
        *,
        _retry_auth: bool = True,
    ) -> aiohttp.ClientResponse:
        """Open a recording stream for an authenticated HA proxy view."""
        descriptor = self._descriptor(API_RECORDING)
        data: dict[str, Any] = {
            "api": API_RECORDING,
            "version": descriptor.max_version,
            "method": "Stream",
            "recordingId": recording.recording_id,
            "_sid": self.sid,
        }
        if recording.ds_id is not None:
            data["dsId"] = recording.ds_id
        if recording.mount_id is not None:
            data["mountId"] = recording.mount_id
        headers: dict[str, str] = {}
        if range_header:
            headers["Range"] = range_header
        if self.synotoken:
            headers["X-SYNO-TOKEN"] = self.synotoken
        try:
            response = await self._session.get(
                f"{self.base_url}/webapi/{descriptor.path.lstrip('/')}",
                params=data,
                headers=headers or None,
                ssl=self._ssl_argument,
                timeout=_STREAM_TIMEOUT,
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SynologyConnectionError("Could not open the recording stream") from err
        if response.headers.get("Content-Type", "").casefold().startswith(
            "application/json"
        ):
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ClientError, json.JSONDecodeError, UnicodeDecodeError) as err:
                response.release()
                raise SynologyConnectionError(
                    "Recording stream returned invalid JSON"
                ) from err
            response.release()
            code = self._error_code(payload) if isinstance(payload, Mapping) else None
            if code in _SESSION_ERROR_CODES and _retry_auth:
                await self.async_login()
                return await self.async_open_recording_stream(
                    recording, range_header, _retry_auth=False
                )
            self._raise_api_error(API_RECORDING, "Stream", code)
        return response

    async def _request_data(
        self,
        api_name: str,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        version: int | None = None,
    ) -> Mapping[str, Any]:
        payload = await self._request_json(
            api_name, method, params or {}, version=version
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise SynologyConnectionError(f"{api_name}.{method} returned no data object")
        return data

    async def _request_json(
        self,
        api_name: str,
        method: str,
        params: Mapping[str, Any],
        *,
        retry_auth: bool = True,
        version: int | None = None,
    ) -> Mapping[str, Any]:
        descriptor = self._descriptor(api_name)
        request_data: dict[str, Any] = {
            "api": api_name,
            "version": version or descriptor.max_version,
            "method": method,
            **params,
        }
        if self.sid:
            request_data["_sid"] = self.sid
        if self.synotoken:
            request_data["SynoToken"] = self.synotoken
        payload = await self._post_json(
            f"/webapi/{descriptor.path.lstrip('/')}",
            request_data,
            allow_error_response=True,
        )
        if payload.get("success") is True:
            return payload
        code = self._error_code(payload)
        if code in _SESSION_ERROR_CODES and retry_auth:
            await self.async_login()
            return await self._request_json(
                api_name,
                method,
                params,
                retry_auth=False,
                version=version,
            )
        self._raise_api_error(api_name, method, code)

    async def _request_binary(
        self,
        api_name: str,
        method: str,
        params: Mapping[str, Any],
        *,
        retry_auth: bool = True,
    ) -> tuple[bytes, str]:
        descriptor = self._descriptor(api_name)
        request_data: dict[str, Any] = {
            "api": api_name,
            "version": descriptor.max_version,
            "method": method,
            **params,
            "_sid": self.sid,
        }
        if self.synotoken:
            request_data["SynoToken"] = self.synotoken
        try:
            async with self._session.post(
                f"{self.base_url}/webapi/{descriptor.path.lstrip('/')}",
                data=request_data,
                headers=(
                    {"X-SYNO-TOKEN": self.synotoken} if self.synotoken else None
                ),
                ssl=self._ssl_argument,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                content_length = _as_int(response.headers.get("Content-Length"))
                if content_length is not None and content_length > _MAX_SNAPSHOT_BYTES:
                    raise SynologyConnectionError(
                        f"{api_name}.{method} returned an oversized response"
                    )
                chunks: list[bytes] = []
                received_bytes = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    received_bytes += len(chunk)
                    if received_bytes > _MAX_SNAPSHOT_BYTES:
                        raise SynologyConnectionError(
                            f"{api_name}.{method} returned an oversized response"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SynologyConnectionError(f"Could not call {api_name}.{method}") from err
        if response.status >= 400:
            raise SynologyConnectionError(
                f"{api_name}.{method} returned HTTP {response.status}"
            )
        if content_type.casefold().startswith("image/"):
            if content_type.casefold().startswith("image/jpeg") and not (
                body.startswith(b"\xff\xd8") and b"\xff\xd9" in body[-32:]
            ):
                raise SynologyConnectionError(
                    f"{api_name}.{method} returned an incomplete JPEG image"
                )
            return body, content_type.split(";", 1)[0]
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise SynologyConnectionError(
                f"{api_name}.{method} returned an unexpected content type"
            ) from err
        code = self._error_code(payload)
        if code in _SESSION_ERROR_CODES and retry_auth:
            await self.async_login()
            return await self._request_binary(api_name, method, params, retry_auth=False)
        self._raise_api_error(api_name, method, code)

    async def _post_json(
        self,
        path: str,
        data: Mapping[str, Any],
        *,
        auth_context: bool = False,
        allow_error_response: bool = False,
    ) -> Mapping[str, Any]:
        try:
            async with self._session.post(
                f"{self.base_url}{path}",
                data=data,
                headers=(
                    {"X-SYNO-TOKEN": self.synotoken} if self.synotoken else None
                ),
                ssl=self._ssl_argument,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status >= 400:
                    raise SynologyConnectionError(f"DiskStation returned HTTP {response.status}")
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as err:
            raise SynologyConnectionError("DiskStation returned an invalid response") from err
        if not isinstance(payload, Mapping):
            raise SynologyConnectionError("DiskStation returned an invalid JSON object")
        if payload.get("success") is not True and not allow_error_response:
            code = self._error_code(payload)
            if auth_context or code in _AUTH_ERROR_CODES:
                raise SynologyAuthError("Surveillance Station authentication failed", code)
            self._raise_api_error("SYNO.API.Info", "query", code)
        return payload

    def _descriptor(self, api_name: str) -> ApiDescriptor:
        try:
            return self.apis[api_name]
        except KeyError as err:
            raise SynologyUnsupportedError(f"API was not discovered: {api_name}") from err

    @staticmethod
    def _error_code(payload: Mapping[str, Any]) -> int | None:
        error = payload.get("error")
        return _as_int(error.get("code")) if isinstance(error, Mapping) else None

    @staticmethod
    def _raise_api_error(api_name: str, method: str, code: int | None) -> None:
        if code in _AUTH_ERROR_CODES:
            raise SynologyAuthError(f"{api_name}.{method} authentication failed", code)
        if code in {105, 119, 120}:
            raise SynologyPermissionError(f"{api_name}.{method} is not permitted", code)
        raise SynologyApiError(f"{api_name}.{method} failed", code)
