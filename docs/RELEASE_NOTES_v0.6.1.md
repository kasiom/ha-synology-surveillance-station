# Synology Surveillance Station v0.6.1 — image rendering fix

This beta fixes last-event snapshots that contained valid JPEG data but appeared blank in the
Home Assistant frontend.

## Fixed

- Restore Home Assistant's standard authenticated image-proxy URL for every last-event image.
- Keep a static `mdi:image-outline` fallback only before the first snapshot is available.
- Add a Home Assistant lifecycle regression test for the generated image-proxy URL.

## Important behavior

With polling enabled, the integration captures the current camera image when Home Assistant
observes a new event. The delay is at most the configured polling interval under normal
conditions. This is not a historical frame extracted from the recording.

The integration remains read-only, requires no webhook or Action Rule, and keeps the official
Synology DSM integration responsible for live cameras and Home Mode.
