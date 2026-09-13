# Synology Surveillance Station v0.7.0 — dynamically discovered live streams

This public beta adds native live-camera entities without assuming that every camera has the
same number of profiles. It keeps the integration read-only and remains compatible with the
official Synology DSM integration.

## Highlights

- Discover every numbered `stream1` … `streamN` object reported by Surveillance Station.
- Create exactly one Home Assistant camera entity for each reported stream, including
  non-contiguous stream numbers.
- Expose resolution, frame rate, bitrate mode, quality, and high/medium/low profile roles.
- Play the high-quality profile over RTSP with TCP transport for better reliability.
- Proxy the low-bandwidth MJPEG profile directly for responsive multi-camera dashboards.
- Reuse authenticated live-view paths from the official Synology DSM integration when it
  manages the same NAS, without copying credentials.
- Keep additional profiles available for snapshots and metadata when Synology does not publish
  a distinct live URL for them.

## Stream discovery behavior

The number of entities follows the `streamN` objects in the Camera List API response: one
reported stream creates one entity and three reported streams create three. Surveillance
Station does not provide a separate online flag for each profile, so their availability follows
the connection state of the parent camera. A reported third or later profile may be
snapshot-only when the public live-view API has no matching path.

## Validated environment

- Home Assistant 2026.9.2;
- Surveillance Station 9.3.12139;
- three HIKVISION cameras;
- low-bandwidth MJPEG dashboard playback and on-demand high-quality RTSP playback.

Other Surveillance Station 9.x releases and camera combinations remain beta-test targets.
Follow `docs/BETA_TESTING.md` when reporting compatibility results.
