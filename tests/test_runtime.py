"""Tests for recording polling, filtering, and event delivery."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from .helpers import load_module

models = load_module("models")
runtime_module = load_module("runtime")


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    def test_authoritative_counter_seeds_then_returns_only_new_events(self) -> None:
        camera = models.Camera(camera_id=1, name="One", status=1)
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        self.assertEqual(runtime.accept_detection_count(1, 4, self.now)[0], 0)
        self.assertEqual(
            runtime.accept_detection_count(
                1, 4, self.now + timedelta(seconds=10)
            )[0],
            0,
        )
        delta, previous_success = runtime.accept_detection_count(
            1, 6, self.now + timedelta(seconds=20)
        )
        self.assertEqual(delta, 2)
        self.assertEqual(previous_success, self.now + timedelta(seconds=10))
        self.assertEqual(runtime.events_today[1], 6)

    def test_failed_count_keeps_baseline_and_recovers_missed_delta(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        runtime.accept_detection_count(1, 4, self.now)
        runtime.mark_detection_count_unavailable(1)
        self.assertFalse(runtime.event_count_available[1])

        delta, previous_success = runtime.accept_detection_count(
            1, 6, self.now + timedelta(minutes=2)
        )
        self.assertEqual(delta, 2)
        self.assertEqual(previous_success, self.now)
        self.assertTrue(runtime.event_count_available[1])

        reset_delta, _previous = runtime.accept_detection_count(
            1, 1, self.now + timedelta(days=1)
        )
        self.assertEqual(reset_delta, 0)
        self.assertEqual(runtime.polling_diagnostics["counter_resets"], 1)

    def test_missing_reason_never_attaches_a_possibly_wrong_recording(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        unknown = models.Recording(
            recording_id=22,
            camera_id=1,
            start_time=self.now,
            reason=None,
        )

        selected = runtime.select_safe_event_recordings(
            1, (unknown,), 1, self.now
        )
        self.assertEqual(selected, ())
        event = runtime.accept_detection(1, self.now, 5, snapshot=b"jpeg")
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "event_recording")
        self.assertIsNone(event.recording_id)
        self.assertEqual(event.snapshot, b"jpeg")
        self.assertEqual(event.extra["detection_verified_by"], "CountByCategory")

    def test_explicit_reason_is_enriched_but_ambiguous_set_is_not(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        motion = models.Recording(
            recording_id=30, camera_id=1, start_time=self.now, reason=2
        )
        continuous = models.Recording(
            recording_id=31, camera_id=1, start_time=self.now, reason=1
        )
        selected = runtime.select_safe_event_recordings(
            1, (motion, continuous), 1, self.now
        )
        self.assertEqual(selected, (motion,))
        event = runtime.accept_detection(1, self.now, 5, recording=motion)
        self.assertEqual(event.event_type, "motion")
        self.assertEqual(event.recording_id, 30)

        two = models.Recording(
            recording_id=32, camera_id=1, start_time=self.now, reason=7
        )
        three = models.Recording(
            recording_id=33, camera_id=1, start_time=self.now, reason=9
        )
        self.assertEqual(
            runtime.select_safe_event_recordings(1, (two, three), 1, self.now),
            (),
        )
        self.assertEqual(runtime.polling_diagnostics["ambiguous_recording_matches"], 1)

    def test_explicit_recording_before_last_success_is_never_attached(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        old = models.Recording(
            recording_id=34,
            camera_id=1,
            start_time=self.now - timedelta(seconds=1),
            reason=2,
        )
        current = models.Recording(
            recording_id=35,
            camera_id=1,
            start_time=self.now + timedelta(seconds=1),
            reason=2,
        )

        self.assertEqual(
            runtime.select_safe_event_recordings(
                1,
                (old, current),
                1,
                self.now + timedelta(seconds=2),
                after=self.now,
            ),
            (current,),
        )

    def test_recording_cache_uses_full_synology_identity(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        first = models.Recording(recording_id=42, camera_id=1, ds_id=0, mount_id=1)
        second = models.Recording(recording_id=42, camera_id=1, ds_id=2, mount_id=3)
        runtime.remember_recordings((first, second))

        self.assertEqual(len(runtime.recordings), 2)
        self.assertEqual(runtime.recordings["0:1:42"], first)
        self.assertEqual(runtime.recordings["2:3:42"], second)

    def test_detection_count_updates_availability_and_notifies(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        updates = []
        runtime.add_event_count_listener(1, lambda: updates.append(True))

        runtime.accept_detection_count(1, 8, self.now)
        runtime.accept_detection_count(1, 8, self.now + timedelta(seconds=10))
        runtime.mark_detection_count_unavailable(1)
        runtime.accept_detection_count(1, 9, self.now + timedelta(seconds=20))

        self.assertEqual(runtime.events_today[1], 9)
        self.assertEqual(len(updates), 3)

    def test_metadata_updates_notify_only_changed_camera_values(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        api = SimpleNamespace(host="nas", port=5001)
        runtime = runtime_module.SurveillanceRuntime(api, info, (camera,))
        self.assertNotIn(1, runtime.recordings_today)
        status_updates = []
        count_updates = []
        runtime.add_status_listener(1, lambda: status_updates.append(True))
        runtime.add_count_listener(1, lambda: count_updates.append(True))

        runtime.begin_metadata_refresh(self.now)
        runtime.complete_metadata_refresh(self.now, {1: 7}, {1: 2}, set())
        runtime.complete_metadata_refresh(
            self.now + timedelta(seconds=60), {1: 7}, {1: 2}, set()
        )

        self.assertTrue(runtime.camera_status_supported)
        self.assertEqual(runtime.recordings_today[1], 7)
        self.assertEqual(runtime.camera_statuses[1], 2)
        self.assertEqual(len(status_updates), 1)
        self.assertEqual(len(count_updates), 1)
        self.assertFalse(runtime.metadata_refresh_due(self.now, 60))
        self.assertTrue(
            runtime.metadata_refresh_due(self.now + timedelta(seconds=60), 60)
        )
        self.assertTrue(runtime.recording_count_refresh_due(self.now))
        runtime.begin_recording_count_refresh(self.now)
        self.assertFalse(
            runtime.recording_count_refresh_due(self.now + timedelta(minutes=4))
        )
        self.assertTrue(
            runtime.recording_count_refresh_due(self.now + timedelta(minutes=5))
        )
        runtime.mark_recording_count_unavailable(1)
        runtime.mark_camera_statuses_unavailable()
        self.assertFalse(runtime.recording_count_available[1])
        self.assertFalse(runtime.camera_status_available[1])
        self.assertEqual(len(status_updates), 2)
        self.assertEqual(len(count_updates), 2)

    def test_restore_events_does_not_replay_entity_events(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        restored_event = models.SurveillanceEvent(
            event_id="recording-0:0:30",
            camera_id=1,
            camera_name="One",
            event_type="motion",
            raw_event_name="Motion detection recording",
            occurred_at=self.now,
            received_at=self.now,
            snapshot=b"jpeg",
            snapshot_content_type="image/jpeg",
            recording_id=30,
        )
        delivered = []
        runtime.add_listener(1, delivered.append)

        runtime.restore_events({1: restored_event})

        self.assertEqual(runtime.last_events[1], restored_event)
        self.assertEqual(delivered, [])
        self.assertEqual(runtime.polling_diagnostics["emitted_events"], 0)
        self.assertEqual(runtime.persistence_diagnostics["restored_events"], 1)
        self.assertEqual(runtime.persistence_diagnostics["restored_snapshots"], 1)

    def test_restore_recording_bootstraps_without_replaying_event(self) -> None:
        camera = models.Camera(camera_id=1, name="One")
        info = models.SurveillanceInfo(None, "9.3.0", None, 1, 2, None, True)
        runtime = runtime_module.SurveillanceRuntime(
            SimpleNamespace(host="nas", port=5001), info, (camera,)
        )
        delivered = []
        state_updates = []
        runtime.add_listener(1, delivered.append)
        runtime.add_state_event_listener(1, state_updates.append)
        recording = models.Recording(
            recording_id=31,
            camera_id=1,
            start_time=self.now,
        )

        restored = runtime.restore_recording(recording, self.now, snapshot=b"jpeg")

        self.assertIsNotNone(restored)
        self.assertEqual(runtime.last_events[1], restored)
        self.assertEqual(delivered, [])
        self.assertEqual(state_updates, [restored])
        self.assertEqual(runtime.polling_diagnostics["emitted_events"], 0)
        self.assertEqual(runtime.persistence_diagnostics["bootstrap_events"], 1)


if __name__ == "__main__":
    unittest.main()
