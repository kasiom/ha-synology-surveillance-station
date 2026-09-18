# Synology Surveillance Station v1.0.0 — first stable release

Version 1.0.0 promotes the read-only Home Assistant integration from public beta to stable.
It combines native camera events, snapshots, recordings, connectivity, and dynamically
discovered live streams without modifying Surveillance Station configuration or recordings.

## Highlights

- Discover every numbered `stream1` … `streamN` profile reported by the NAS.
- Use responsive low-bandwidth MJPEG streams on overview dashboards and open high-quality
  RTSP-over-TCP streams on demand.
- Reuse authenticated live-view paths from the official Synology DSM integration when both
  integrations manage the same NAS.
- Poll motion, alarm, analytics, and action-rule recordings without requiring the NAS to
  initiate a connection to Home Assistant.
- Expose authenticated latest-event snapshots and range-aware recording playback without
  publishing credentials, session identifiers, or media files.
- Restore state safely after restarts and guide expired credentials through Home Assistant's
  reauthentication flow.

## Stable-release validation

- Production use with three HIKVISION cameras on Surveillance Station 9.3.12139.
- Home Assistant 2026.9.2, with 2026.8.2 retained as the supported floor.
- Automated HACS and Hassfest validation.
- Python 3.14 lint, compilation, Home Assistant lifecycle, API, persistence, runtime, and
  camera-stream behavior tests.

## Compatibility notes

The integration creates one entity for every `streamN` object reported by Surveillance
Station. Synology publishes distinct live paths for the high and low profiles; an additional
profile may therefore be snapshot-only. The official Synology DSM integration can remain
installed for Home Mode and authenticated live-view path reuse.

New NAS and camera combinations can be reported with the repository's compatibility form.
