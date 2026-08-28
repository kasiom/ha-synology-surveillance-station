"""Tests for restart-safe latest-event persistence."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from .helpers import load_module

models = load_module("models")
persistence = load_module("persistence")


class PersistenceTests(unittest.TestCase):
    def test_event_and_snapshot_round_trip(self) -> None:
        occurred_at = datetime(2026, 8, 28, 11, 13, tzinfo=UTC)
        event = models.SurveillanceEvent(
            event_id="recording-0:0:42",
            camera_id=2,
            camera_name="Camera 2",
            event_type="event_recording",
            raw_event_name="Detection recording",
            occurred_at=occurred_at,
            received_at=occurred_at,
            snapshot=b"\xff\xd8\xff\xe0snapshot",
            snapshot_content_type="image/jpeg",
            recording_id=42,
            extra={"detection_verified_by": "CountByCategory"},
        )

        restored = persistence.deserialize_last_events(
            persistence.serialize_last_events({2: event}), {1, 2, 3}
        )

        self.assertEqual(restored[2], event)

    def test_verified_legacy_generic_event_is_migrated_to_event_recording(self) -> None:
        raw = {
            "events": [
                {
                    "camera_id": 2,
                    "camera_name": "Camera 2",
                    "event_type": "unknown",
                    "occurred_at": "2026-08-28T11:13:00+00:00",
                    "received_at": "2026-08-28T11:13:01+00:00",
                    "extra": {"detection_verified_by": "CountByCategory"},
                }
            ]
        }

        restored = persistence.deserialize_last_events(raw, {2})

        self.assertEqual(restored[2].event_type, "event_recording")

        raw["events"][0]["event_type"] = "detection"
        restored = persistence.deserialize_last_events(raw, {2})
        self.assertEqual(restored[2].event_type, "event_recording")

    def test_invalid_and_removed_camera_events_are_ignored(self) -> None:
        raw = {
            "events": [
                {"camera_id": 99, "occurred_at": "bad", "received_at": "bad"},
                {
                    "camera_id": 4,
                    "occurred_at": "2026-08-28T11:13:00+00:00",
                    "received_at": "2026-08-28T11:13:01+00:00",
                },
            ]
        }

        self.assertEqual(persistence.deserialize_last_events(raw, {1, 2, 3}), {})

    def test_oversized_snapshot_is_not_written_to_storage(self) -> None:
        occurred_at = datetime(2026, 8, 28, 11, 13, tzinfo=UTC)
        event = models.SurveillanceEvent(
            event_id="recording-0:0:43",
            camera_id=2,
            camera_name="Camera 2",
            event_type="event_recording",
            raw_event_name="Detection recording",
            occurred_at=occurred_at,
            received_at=occurred_at,
            snapshot=b"x" * (persistence.MAX_STORED_SNAPSHOT_BYTES + 1),
        )

        serialized = persistence.serialize_last_events({2: event})

        self.assertIsNone(serialized["events"][0]["snapshot"])
        self.assertEqual(persistence.MAX_STORED_SNAPSHOT_BYTES, 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
