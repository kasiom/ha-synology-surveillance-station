"""In-memory runtime state for one Surveillance Station config entry."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .api import SynologySurveillanceApi
from .const import METADATA_REFRESH_INTERVAL, RECORDING_COUNT_REFRESH_INTERVAL
from .models import Camera, Recording, SurveillanceEvent, SurveillanceInfo

EventListener = Callable[[SurveillanceEvent], None]
StateListener = Callable[[], None]

_RECORDING_REASON_NAMES = {
    0: "None",
    1: "Continuous recording",
    2: "Motion detection recording",
    3: "Alarm recording",
    4: "Custom recording",
    5: "Manual recording",
    6: "External recording",
    7: "Analytics recording",
    8: "Edge recording",
    9: "Action rule recording",
    10: "Advanced continuous recording",
}
_EVENT_RECORDING_REASONS = {2, 3, 4, 6, 7, 9}
_EVENT_TYPE_BY_RECORDING_REASON = {
    2: "motion",
    3: "alarm",
    4: "custom",
    6: "external",
    7: "analytics",
    9: "action_rule",
}


class SurveillanceRuntime:
    """Own API state, event routing, caches, and privacy-safe diagnostics."""

    def __init__(
        self,
        api: SynologySurveillanceApi,
        info: SurveillanceInfo,
        cameras: tuple[Camera, ...],
        available_cameras: tuple[Camera, ...] | None = None,
    ) -> None:
        self.api = api
        self.info = info
        self.cameras = cameras
        self.available_cameras = available_cameras or cameras
        self.hub_device_id: str | None = None
        self.last_events: dict[int, SurveillanceEvent] = {}
        self.recordings: dict[str, Recording] = {}
        self.camera_status_supported = True
        self.camera_statuses: dict[int, int] = {
            camera.camera_id: camera.status
            for camera in cameras
            if camera.status is not None
        }
        self.recordings_today: dict[int, int] = {}
        self.events_today: dict[int, int] = {}
        self.camera_status_available: dict[int, bool] = {
            camera.camera_id: camera.status is not None for camera in cameras
        }
        self.recording_count_available: dict[int, bool] = {
            camera.camera_id: False for camera in cameras
        }
        self.event_count_available: dict[int, bool] = {
            camera.camera_id: False for camera in cameras
        }
        self._listeners: dict[int, list[EventListener]] = defaultdict(list)
        self._state_event_listeners: dict[int, list[EventListener]] = defaultdict(list)
        self._status_listeners: dict[int, list[StateListener]] = defaultdict(list)
        self._count_listeners: dict[int, list[StateListener]] = defaultdict(list)
        self._event_count_listeners: dict[int, list[StateListener]] = defaultdict(list)
        self._seen_recordings: OrderedDict[str, datetime] = OrderedDict()
        self._detection_count_baselines: dict[int, int] = {}
        self._detection_count_success_at: dict[int, datetime] = {}
        self._metadata_last_attempt: datetime | None = None
        self._recording_count_last_attempt: datetime | None = None
        self.polling_diagnostics: dict[str, Any] = {
            "enabled": False,
            "interval_seconds": None,
            "initialized": False,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_reason_counts": {},
            "last_detection_counts": {},
            "last_detection_count_errors": {},
            "seen_recordings": 0,
            "emitted_events": 0,
            "recording_lists_requested": 0,
            "recording_lists_skipped": 0,
            "ambiguous_recording_matches": 0,
            "generic_events": 0,
            "counter_resets": 0,
        }
        self.metadata_diagnostics: dict[str, Any] = {
            "interval_seconds": METADATA_REFRESH_INTERVAL,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error_types": [],
            "camera_status_source": "SYNO.SurveillanceStation.Camera.List v9",
            "camera_statuses_received": 0,
            "recording_counts_received": 0,
            "recording_count_interval_seconds": RECORDING_COUNT_REFRESH_INTERVAL,
            "recording_count_last_attempt_at": None,
            "recording_count_last_success_at": None,
        }
        self.persistence_diagnostics: dict[str, Any] = {
            "restored_events": 0,
            "restored_snapshots": 0,
            "bootstrap_events": 0,
            "last_save_at": None,
            "last_save_error": None,
            "storage_strategy": "per_camera",
            "migrated_legacy_events": 0,
        }

    def add_listener(self, camera_id: int, listener: EventListener) -> Callable[[], None]:
        """Subscribe a native event entity to new camera detections."""
        self._listeners[camera_id].append(listener)

        def remove() -> None:
            if listener in self._listeners[camera_id]:
                self._listeners[camera_id].remove(listener)

        return remove

    def add_state_event_listener(
        self, camera_id: int, listener: EventListener
    ) -> Callable[[], None]:
        """Subscribe a state entity to new or restored latest-event data."""
        self._state_event_listeners[camera_id].append(listener)

        def remove() -> None:
            if listener in self._state_event_listeners[camera_id]:
                self._state_event_listeners[camera_id].remove(listener)

        return remove

    def add_status_listener(
        self, camera_id: int, listener: StateListener
    ) -> Callable[[], None]:
        """Subscribe an entity to camera connectivity updates."""
        self._status_listeners[camera_id].append(listener)

        def remove() -> None:
            if listener in self._status_listeners[camera_id]:
                self._status_listeners[camera_id].remove(listener)

        return remove

    def add_count_listener(
        self, camera_id: int, listener: StateListener
    ) -> Callable[[], None]:
        """Subscribe an entity to daily recording count updates."""
        self._count_listeners[camera_id].append(listener)

        def remove() -> None:
            if listener in self._count_listeners[camera_id]:
                self._count_listeners[camera_id].remove(listener)

        return remove

    def add_event_count_listener(
        self, camera_id: int, listener: StateListener
    ) -> Callable[[], None]:
        """Subscribe an entity to today's detection count updates."""
        self._event_count_listeners[camera_id].append(listener)

        def remove() -> None:
            if listener in self._event_count_listeners[camera_id]:
                self._event_count_listeners[camera_id].remove(listener)

        return remove

    def remember_recordings(self, recordings: tuple[Recording, ...]) -> None:
        """Cache resolved recordings for the authenticated playback proxy."""
        for recording in recordings:
            self.recordings[self.recording_key(recording)] = recording
        if len(self.recordings) > 1000:
            for recording_key in tuple(self.recordings)[:-1000]:
                self.recordings.pop(recording_key, None)

    def restore_events(self, events: dict[int, SurveillanceEvent]) -> None:
        """Restore persisted last events without replaying native HA events."""
        self.last_events.update(events)
        self.persistence_diagnostics.update(
            {
                "restored_events": len(events),
                "restored_snapshots": sum(
                    event.snapshot is not None for event in events.values()
                ),
            }
        )

    def enable_polling(self, interval_seconds: int) -> None:
        """Expose polling configuration without publishing NAS details."""
        self.polling_diagnostics.update(
            {"enabled": True, "interval_seconds": interval_seconds}
        )

    def accept_detection_count(
        self, camera_id: int, current_count: int, received_at: datetime
    ) -> tuple[int, datetime | None]:
        """Update the authoritative event counter and return its non-negative delta.

        A failed poll never changes the baseline. The next successful poll therefore
        includes events that happened during the outage instead of silently losing them.
        A lower value means the daily Synology counter reset and establishes a new baseline.
        """
        previous_count = self._detection_count_baselines.get(camera_id)
        previous_success = self._detection_count_success_at.get(camera_id)
        self._detection_count_baselines[camera_id] = current_count
        self._detection_count_success_at[camera_id] = received_at

        availability_changed = not self.event_count_available.get(camera_id, False)
        value_changed = self.events_today.get(camera_id) != current_count
        self.event_count_available[camera_id] = True
        self.events_today[camera_id] = current_count
        if availability_changed or value_changed:
            for listener in tuple(self._event_count_listeners[camera_id]):
                listener()

        if previous_count is None:
            return 0, previous_success
        if current_count < previous_count:
            self.polling_diagnostics["counter_resets"] += 1
            return 0, previous_success
        return current_count - previous_count, previous_success

    def mark_detection_count_unavailable(self, camera_id: int) -> None:
        """Mark one event counter unavailable without discarding its baseline."""
        if not self.event_count_available.get(camera_id, False):
            return
        self.event_count_available[camera_id] = False
        for listener in tuple(self._event_count_listeners[camera_id]):
            listener()

    def select_safe_event_recordings(
        self,
        camera_id: int,
        recordings: tuple[Recording, ...],
        event_delta: int,
        received_at: datetime,
        after: datetime | None = None,
    ) -> tuple[Recording, ...]:
        """Return only recordings whose own reason proves that they are events.

        Recording.List commonly omits ``reason``. Those rows are deliberately never
        guessed or attached to a counter event. If more explicit candidates are returned
        than the authoritative delta, the association is ambiguous and no clip is used.
        """
        self.remember_recordings(recordings)
        reason_counts: dict[str, int] = {}
        explicit: list[Recording] = []
        for recording in sorted(
            recordings,
            key=lambda item: (item.start_time or received_at, item.recording_id),
        ):
            reason = self._reason_number(recording.reason)
            label = str(reason) if reason is not None else "missing"
            reason_counts[label] = reason_counts.get(label, 0) + 1
            if (
                recording.camera_id != camera_id
                or reason not in _EVENT_RECORDING_REASONS
                or recording.start_time is None
                or (after is not None and recording.start_time <= after)
            ):
                continue
            key = f"{self.recording_key(recording)}:{reason}"
            if key in self._seen_recordings:
                continue
            self._seen_recordings[key] = received_at
            self._seen_recordings.move_to_end(key)
            explicit.append(recording)

        while len(self._seen_recordings) > 2000:
            self._seen_recordings.popitem(last=False)
        self.polling_diagnostics["last_reason_counts"] = reason_counts
        self.polling_diagnostics["seen_recordings"] = len(self._seen_recordings)
        if len(explicit) > event_delta:
            self.polling_diagnostics["ambiguous_recording_matches"] += 1
            return ()
        return tuple(explicit)

    def finish_poll(
        self,
        received_at: datetime,
        counts: dict[int, int],
        errors: dict[int, str],
    ) -> None:
        """Record privacy-safe poll health after independently processing cameras."""
        self.polling_diagnostics.update(
            {
                "initialized": bool(self._detection_count_baselines),
                "last_attempt_at": received_at.isoformat(),
                "last_success_at": (
                    received_at.isoformat()
                    if counts
                    else self.polling_diagnostics["last_success_at"]
                ),
                "last_error": sorted(set(errors.values())) if errors else None,
                "last_detection_counts": counts,
                "last_detection_count_errors": errors,
            }
        )

    def metadata_refresh_due(self, received_at: datetime, interval_seconds: int) -> bool:
        """Return whether low-frequency counts and status should be refreshed."""
        return self._metadata_last_attempt is None or (
            received_at - self._metadata_last_attempt
        ) >= timedelta(seconds=interval_seconds)

    def begin_metadata_refresh(self, received_at: datetime) -> None:
        """Mark a metadata refresh attempt before network calls begin."""
        self._metadata_last_attempt = received_at
        self.metadata_diagnostics["last_attempt_at"] = received_at.isoformat()

    def recording_count_refresh_due(self, received_at: datetime) -> bool:
        """Return whether the diagnostic total-recording count is due."""
        return self._recording_count_last_attempt is None or (
            received_at - self._recording_count_last_attempt
        ) >= timedelta(seconds=RECORDING_COUNT_REFRESH_INTERVAL)

    def begin_recording_count_refresh(self, received_at: datetime) -> None:
        """Mark a total-recording count refresh before network calls begin."""
        self._recording_count_last_attempt = received_at
        self.metadata_diagnostics["recording_count_last_attempt_at"] = (
            received_at.isoformat()
        )

    def complete_metadata_refresh(
        self,
        received_at: datetime,
        recording_counts: dict[int, int],
        camera_statuses: dict[int, int],
        error_types: set[str],
        *,
        camera_status_refresh_succeeded: bool = True,
    ) -> None:
        """Update cached metadata and notify only entities whose state changed."""
        for camera_id, count in recording_counts.items():
            availability_changed = not self.recording_count_available.get(
                camera_id, False
            )
            value_changed = self.recordings_today.get(camera_id) != count
            self.recording_count_available[camera_id] = True
            self.recordings_today[camera_id] = count
            if availability_changed or value_changed:
                for listener in tuple(self._count_listeners[camera_id]):
                    listener()
        for camera_id, status in camera_statuses.items():
            availability_changed = not self.camera_status_available.get(camera_id, False)
            value_changed = self.camera_statuses.get(camera_id) != status
            self.camera_status_available[camera_id] = True
            self.camera_statuses[camera_id] = status
            if availability_changed or value_changed:
                for listener in tuple(self._status_listeners[camera_id]):
                    listener()
        if camera_status_refresh_succeeded:
            for camera in self.cameras:
                camera_id = camera.camera_id
                if camera_id in camera_statuses:
                    continue
                if self.camera_status_available.get(camera_id, False):
                    self.camera_status_available[camera_id] = False
                    for listener in tuple(self._status_listeners[camera_id]):
                        listener()
        self.metadata_diagnostics.update(
            {
                "last_error_types": sorted(error_types),
                "camera_statuses_received": len(camera_statuses),
                "recording_counts_received": len(recording_counts),
            }
        )
        if not error_types:
            self.metadata_diagnostics["last_success_at"] = received_at.isoformat()
        if recording_counts and not error_types:
            self.metadata_diagnostics["recording_count_last_success_at"] = (
                received_at.isoformat()
            )

    def mark_recording_count_unavailable(self, camera_id: int) -> None:
        """Mark one diagnostic recording counter unavailable."""
        if not self.recording_count_available.get(camera_id, False):
            return
        self.recording_count_available[camera_id] = False
        for listener in tuple(self._count_listeners[camera_id]):
            listener()

    def mark_camera_statuses_unavailable(self) -> None:
        """Mark all selected camera status entities unavailable after a failed refresh."""
        for camera in self.cameras:
            camera_id = camera.camera_id
            if not self.camera_status_available.get(camera_id, False):
                continue
            self.camera_status_available[camera_id] = False
            for listener in tuple(self._status_listeners[camera_id]):
                listener()

    def mark_all_polling_unavailable(self) -> None:
        """Expose an authentication outage on all network-backed entities."""
        for camera in self.cameras:
            self.mark_detection_count_unavailable(camera.camera_id)
            self.mark_recording_count_unavailable(camera.camera_id)
        self.mark_camera_statuses_unavailable()

    def accept_detection(
        self,
        camera_id: int,
        received_at: datetime,
        counter_value: int,
        *,
        recording: Recording | None = None,
        snapshot: bytes | None = None,
        snapshot_content_type: str | None = None,
    ) -> SurveillanceEvent | None:
        """Emit one counter-verified event, optionally enriched by a proven event clip."""
        if recording is not None:
            return self.accept_recording(
                recording,
                received_at,
                snapshot=snapshot,
                snapshot_content_type=snapshot_content_type,
                verified_detection=True,
            )
        camera = next(
            (candidate for candidate in self.cameras if candidate.camera_id == camera_id),
            None,
        )
        if camera is None:
            return None
        event = SurveillanceEvent(
            event_id=(
                f"count-{camera_id}-{int(received_at.timestamp() * 1_000_000)}-"
                f"{counter_value}"
            ),
            camera_id=camera_id,
            camera_name=camera.name,
            event_type="event_recording",
            raw_event_name="Event recording",
            occurred_at=received_at,
            received_at=received_at,
            snapshot=snapshot,
            snapshot_content_type=snapshot_content_type,
            extra={
                "source": "detection_count_poll",
                "detection_verified_by": "CountByCategory",
            },
        )
        self.polling_diagnostics["generic_events"] += 1
        self._publish_event(event)
        return event

    def accept_recording(
        self,
        recording: Recording,
        received_at: datetime,
        *,
        snapshot: bytes | None = None,
        snapshot_content_type: str | None = None,
        verified_detection: bool = False,
    ) -> SurveillanceEvent | None:
        """Turn one newly discovered event recording into a native HA event."""
        event = self._event_from_recording(
            recording,
            received_at,
            snapshot=snapshot,
            snapshot_content_type=snapshot_content_type,
            verified_detection=verified_detection,
        )
        if event is None:
            return None
        self._publish_event(event)
        return event

    def _publish_event(self, event: SurveillanceEvent) -> None:
        """Publish and retain a normalized event through both listener channels."""
        self.last_events[event.camera_id] = event
        self.polling_diagnostics["emitted_events"] += 1
        for listener in tuple(self._listeners[event.camera_id]):
            listener(event)
        for listener in tuple(self._state_event_listeners[event.camera_id]):
            listener(event)

    def restore_recording(
        self,
        recording: Recording,
        received_at: datetime,
        *,
        snapshot: bytes | None = None,
        snapshot_content_type: str | None = None,
    ) -> SurveillanceEvent | None:
        """Bootstrap the newest verified recording without replaying an HA event."""
        event = self._event_from_recording(
            recording,
            received_at,
            snapshot=snapshot,
            snapshot_content_type=snapshot_content_type,
            verified_detection=True,
        )
        if event is None:
            return None
        existing = self.last_events.get(event.camera_id)
        if existing is not None and existing.occurred_at >= event.occurred_at:
            return existing
        self.last_events[event.camera_id] = event
        self.persistence_diagnostics["bootstrap_events"] += 1
        for listener in tuple(self._state_event_listeners[event.camera_id]):
            listener(event)
        return event

    def _event_from_recording(
        self,
        recording: Recording,
        received_at: datetime,
        *,
        snapshot: bytes | None,
        snapshot_content_type: str | None,
        verified_detection: bool,
    ) -> SurveillanceEvent | None:
        """Normalize one recording without deciding whether it should be emitted."""
        camera = next(
            (
                candidate
                for candidate in self.cameras
                if candidate.camera_id == recording.camera_id
            ),
            None,
        )
        reason = self._reason_number(recording.reason)
        if camera is None or (
            reason not in _EVENT_RECORDING_REASONS and not verified_detection
        ):
            return None
        event_type = _EVENT_TYPE_BY_RECORDING_REASON.get(reason, "event_recording")
        raw_event_name = (
            _RECORDING_REASON_NAMES.get(reason, str(recording.reason))
            if reason is not None
            else "Event recording"
        )
        return SurveillanceEvent(
            event_id=f"recording-{self.recording_key(recording)}",
            camera_id=camera.camera_id,
            camera_name=camera.name,
            event_type=event_type,
            raw_event_name=raw_event_name,
            occurred_at=recording.start_time or received_at,
            received_at=received_at,
            snapshot=snapshot,
            snapshot_content_type=snapshot_content_type,
            recording_id=recording.recording_id,
            extra={
                "source": "recording_poll",
                "recording_reason": reason,
                "detection_verified_by": (
                    "CountByCategory" if verified_detection else "recording_reason"
                ),
            },
        )

    @staticmethod
    def recording_key(recording: Recording) -> str:
        """Return the full Synology recording identity used by playback caches."""
        return ":".join(
            (
                str(recording.ds_id or 0),
                str(recording.mount_id or 0),
                str(recording.recording_id),
            )
        )

    @staticmethod
    def _reason_number(reason: int | str | None) -> int | None:
        try:
            return int(reason) if reason is not None else None
        except (TypeError, ValueError):
            text = str(reason).casefold()
            for number, name in _RECORDING_REASON_NAMES.items():
                if name.casefold() in text:
                    return number
        return None
