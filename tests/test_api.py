"""Deterministic tests for Web API discovery and parsing."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from .helpers import load_module

api_module = load_module("api")


class FakeResponse:
    def __init__(self, payload, *, status=200, headers=None, body=b"") -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.body = body
        self.content = FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.payload

    async def read(self):
        return self.body


class FakeContent:
    def __init__(self, body) -> None:
        self.body = body

    async def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class FakeSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


DISCOVERY = {
    "success": True,
    "data": {
        "SYNO.API.Auth": {"path": "auth.cgi", "minVersion": 1, "maxVersion": 7},
        "SYNO.SurveillanceStation.Info": {
            "path": "entry.cgi",
            "minVersion": 1,
            "maxVersion": 9,
        },
        "SYNO.SurveillanceStation.Camera": {
            "path": "entry.cgi",
            "minVersion": 1,
            "maxVersion": 9,
        },
        "SYNO.SurveillanceStation.Recording": {
            "path": "entry.cgi",
            "minVersion": 1,
            "maxVersion": 6,
        },
    },
}


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_queries_required_apis_without_wildcard(self) -> None:
        session = FakeSession([FakeResponse(DISCOVERY)])
        client = api_module.SynologySurveillanceApi(
            session,
            "nas.local",
            5001,
            "user",
            "pass",
            use_ssl=True,
            verify_ssl=True,
        )

        await client.async_discover()

        request_data = session.calls[0][1]["data"]
        self.assertEqual(
            request_data["query"],
            "SYNO.API.Auth,SYNO.SurveillanceStation.Info,"
            "SYNO.SurveillanceStation.Camera,SYNO.SurveillanceStation.Recording",
        )
        self.assertNotIn("*", request_data["query"])
        self.assertEqual(session.calls[0][1]["timeout"].total, 15)

    async def test_discovery_rejects_recording_api_without_stream_support(self) -> None:
        discovery = {
            "success": True,
            "data": {
                **DISCOVERY["data"],
                "SYNO.SurveillanceStation.Recording": {
                    "path": "entry.cgi",
                    "minVersion": 1,
                    "maxVersion": 5,
                },
            },
        }
        client = api_module.SynologySurveillanceApi(
            FakeSession([FakeResponse(discovery)]),
            "nas.local",
            5001,
            "user",
            "pass",
            use_ssl=True,
            verify_ssl=True,
        )
        with self.assertRaises(api_module.SynologyUnsupportedError):
            await client.async_discover()

    async def test_initialize_discovers_logs_in_and_parses_cameras(self) -> None:
        session = FakeSession(
            [
                FakeResponse(DISCOVERY),
                FakeResponse(
                    {"success": True, "data": {"sid": "secret", "synotoken": "token"}}
                ),
                FakeResponse(
                    {
                        "success": True,
                        "data": {
                            "serial": "NAS123",
                            "version": {"major": 9, "minor": 2, "build": 11979},
                            "cameraNumber": 1,
                            "licenseNumber": 2,
                            "allowSnapshot": True,
                        },
                    }
                ),
                FakeResponse(
                    {
                        "success": True,
                        "data": {
                            "cameras": [
                                {
                                    "id": 8,
                                    "name": "cam3_byt_8",
                                    "vendor": "ONVIF",
                                    "model": "Doorbell",
                                    "videoCodec": 3,
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        client = api_module.SynologySurveillanceApi(
            session,
            "https://192.0.2.10/",
            5001,
            "ha-events",
            "password-value",
            use_ssl=True,
            verify_ssl=False,
        )
        info, cameras = await client.async_initialize()
        self.assertEqual(info.version, "9.2.11979")
        self.assertEqual(cameras[0].camera_id, 8)
        self.assertEqual(cameras[0].name, "cam3_byt_8")
        login_url, login_kwargs = session.calls[1]
        self.assertNotIn("password-value", login_url)
        self.assertEqual(login_kwargs["data"]["passwd"], "password-value")
        self.assertFalse(login_kwargs["ssl"])
        self.assertEqual(session.calls[2][1]["headers"], {"X-SYNO-TOKEN": "token"})

    async def test_session_expiry_is_reauthenticated_once(self) -> None:
        session = FakeSession(
            [
                FakeResponse({"success": False, "error": {"code": 106}}),
                FakeResponse({"success": True, "data": {"sid": "fresh"}}),
                FakeResponse({"success": True, "data": {"value": 1}}),
            ]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            name: api_module.ApiDescriptor(name, "entry.cgi", 1, 9)
            for name in (
                "SYNO.API.Auth",
                "SYNO.SurveillanceStation.Info",
            )
        }
        client.sid = "expired"
        data = await client._request_data("SYNO.SurveillanceStation.Info", "GetInfo")
        self.assertEqual(data["value"], 1)
        self.assertEqual(client.sid, "fresh")
        self.assertEqual(len(session.calls), 3)

    async def test_recording_parser_accepts_documented_aliases(self) -> None:
        start = 1_700_000_000
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "success": True,
                        "data": {
                            "events": [
                                {
                                    "recordingId": 42,
                                    "cameraId": 8,
                                    "startTime": start,
                                    "stopTime": start + 65,
                                    "sizeByte": 1234,
                                    "mountId": 2,
                                    "dsId": 0,
                                }
                            ]
                        },
                    }
                )
            ]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Recording": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Recording", "entry.cgi", 1, 6
            )
        }
        client.sid = "sid"
        recordings = await client.async_list_recordings(
            8,
            datetime.fromtimestamp(start, UTC),
            datetime.fromtimestamp(start + 100, UTC),
            offset=12,
            limit=1,
        )
        self.assertEqual(recordings[0].recording_id, 42)
        self.assertEqual(recordings[0].duration_seconds, 65)
        self.assertEqual(recordings[0].size_bytes, 1234)
        self.assertEqual(session.calls[0][1]["data"]["offset"], 12)
        self.assertEqual(session.calls[0][1]["data"]["limit"], 1)

    async def test_recording_count_uses_total_without_loading_the_full_day(self) -> None:
        session = FakeSession(
            [FakeResponse({"success": True, "data": {"total": 314, "recordings": []}})]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Recording": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Recording", "entry.cgi", 1, 6
            )
        }
        client.sid = "sid"
        count = await client.async_count_recordings(
            8,
            datetime(2026, 8, 28, tzinfo=UTC),
            datetime(2026, 8, 29, tzinfo=UTC),
        )
        self.assertEqual(count, 314)
        self.assertEqual(session.calls[0][1]["data"]["limit"], 1)

    async def test_recording_count_rejects_inexact_single_page_fallback(self) -> None:
        session = FakeSession(
            [FakeResponse({"success": True, "data": {"recordings": [{"id": 1}]}})]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Recording": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Recording", "entry.cgi", 1, 6
            )
        }
        client.sid = "sid"
        with self.assertRaises(api_module.SynologyConnectionError):
            await client.async_count_recordings(
                8,
                datetime(2026, 8, 28, tzinfo=UTC),
                datetime(2026, 8, 29, tzinfo=UTC),
            )

    async def test_snapshot_response_size_is_bounded(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    {},
                    headers={
                        "Content-Type": "image/jpeg",
                        "Content-Length": str(api_module._MAX_SNAPSHOT_BYTES + 1),
                    },
                )
            ]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Camera": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Camera", "entry.cgi", 1, 9
            )
        }
        client.sid = "sid"
        with self.assertRaises(api_module.SynologyConnectionError):
            await client.async_get_snapshot(8)
        self.assertEqual(session.calls[0][1]["timeout"].total, 15)

    async def test_detection_count_uses_documented_reason_filter(self) -> None:
        session = FakeSession(
            [FakeResponse({"success": True, "data": {"total": 12}})]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Recording": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Recording", "entry.cgi", 1, 6
            )
        }
        client.sid = "sid"
        count = await client.async_count_detection_recordings(
            8,
            datetime(2026, 8, 28, tzinfo=UTC),
            datetime(2026, 8, 29, tzinfo=UTC),
        )
        self.assertEqual(count, 12)
        request = session.calls[0][1]["data"]
        self.assertEqual(request["method"], "CountByCategory")
        self.assertEqual(request["version"], 4)
        self.assertEqual(request["reason"], "2,3,4,6,7,9")
        self.assertEqual(request["cameraIds"], "8")

    async def test_camera_statuses_use_documented_camera_list_shape(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "success": True,
                        "data": {
                            "cameras": [
                                {"id": 1, "name": "One", "status": 1},
                                {"id": 2, "name": "Two", "status": 3},
                            ]
                        },
                    }
                )
            ]
        )
        client = api_module.SynologySurveillanceApi(
            session, "nas.local", 5001, "user", "pass", use_ssl=True, verify_ssl=True
        )
        client.apis = {
            "SYNO.SurveillanceStation.Camera": api_module.ApiDescriptor(
                "SYNO.SurveillanceStation.Camera", "entry.cgi", 1, 9
            )
        }
        client.sid = "sid"
        self.assertEqual(await client.async_get_camera_statuses(), {1: 1, 2: 3})

    def test_host_normalization_rejects_paths_and_credentials(self) -> None:
        self.assertEqual(api_module.normalize_host("https://nas.local/"), "nas.local")
        with self.assertRaises(ValueError):
            api_module.normalize_host("https://user:pass@nas.local/")
        with self.assertRaises(ValueError):
            api_module.normalize_host("nas.local/webapi")


if __name__ == "__main__":
    unittest.main()
