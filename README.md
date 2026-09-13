# Synology Surveillance Station for Home Assistant

An unofficial public-beta integration that complements Home Assistant's official
`synology_dsm` integration with native detection events, last-event snapshots, useful
camera status entities, and a Media Source for Surveillance Station recordings.

> [!IMPORTANT]
> Version `0.7.0` is a public beta. It is stable on the maintainer's three-camera test
> installation, but it still needs reports from different Synology models, camera vendors,
> recording modes, and Surveillance Station 9.x versions. It does not modify recordings or
> camera configuration.

## What it adds

- one `event` entity per Surveillance Station camera;
- localized event types, including `event_recording`, `motion`, `alarm`, `analytics`, and action rules;
- one authenticated post-event `image` entity per camera;
- automatic current-camera snapshot retrieval when each new event is observed;
- API polling of motion and analytics recordings for isolated camera networks;
- one useful verified-events-today counter per camera;
- optional diagnostic last-event timestamp and total-recordings-today sensors;
- one connectivity binary sensor per camera from the documented Camera List status;
- automatic discovery of numbered camera streams (`stream1` … `streamN`);
- one live camera entity per discovered stream, with high-quality RTSP over TCP and
  low-bandwidth MJPEG playback where Surveillance Station publishes those paths;
- recordings grouped by camera and time range in Home Assistant Media;
- an authenticated byte-range proxy, so NAS credentials and session IDs never appear in
  dashboards or media URLs;
- privacy-safe polling and metadata diagnostics;
- native light/dark integration branding and translated entity icons.

The official integration can remain installed for Home Mode. Live camera entities are
provided by this integration so dashboards can choose a stable high-quality or
low-bandwidth source without changing NAS recording settings.

When the official Synology DSM integration manages the same NAS, the live entities reuse
its already-authenticated, cached live-view paths. This lets the dedicated Surveillance
Station account remain least-privileged for events and snapshots without copying DSM
credentials into a second config entry.

## Beta status

Version `0.7.0` is a reliability-focused public beta. The API layer follows Synology's
documented Web API discovery, authentication, camera, snapshot, and recording endpoints.
New event recordings are polled by default, so Home Assistant can consume events when the NAS
is intentionally unable to initiate connections into the automation network.

Validated on Home Assistant `2026.9.2`, Surveillance Station `9.3.12139`, and three HIKVISION
cameras. The supported floor is Home Assistant `2026.8.2`; other Surveillance Station 9.x
installations are beta-test targets rather than claimed compatibility.

## Install the public beta with HACS

Until the beta has broader hardware coverage, install it as a custom HACS repository:

1. In HACS, open the menu in the upper-right corner and select **Custom repositories**.
2. Add `https://github.com/kasiom/ha-synology-surveillance-station` as category
   **Integration**.
3. Find **Synology Surveillance Station**, choose the `v0.7.0` beta release, and download it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and select
   **Synology Surveillance Station**.

The repository will become installable after its first GitHub beta release is published.

## Manual installation for development

1. Copy `custom_components/synology_surveillance_station` to the same path under the Home
   Assistant configuration directory.
2. Restart Home Assistant.
3. Add **Synology Surveillance Station** from Settings → Devices & services.
4. Enter the DSM address, port, and a dedicated account. HTTPS with certificate
   verification disabled is the practical default for a NAS using a self-signed
   certificate.
5. Leave API event polling enabled. The default ten-second interval is appropriate for most
   installations.
6. Open **Configure** to select which cameras belong in Home Assistant or to change the
   polling interval.

## Recommended use

The four primary entities on each camera device have distinct jobs:

- **Camera event** is the automation trigger. Its state is the event timestamp and its
  `event_type` attribute distinguishes motion, alarm, analytics, and the truthful generic
  `event_recording` fallback.
- **Last event snapshot** supplies the camera image captured when Home Assistant observes the
  event for a dashboard or notification.
- **Events today** is a compact dashboard counter of verified event recordings.
- **Connection** reports whether Surveillance Station currently sees the camera online.

The **Last event** timestamp and **Recordings today** total are diagnostic entities and are
disabled by default on new installations. They remain available when a troubleshooting or
special dashboard use case needs them. Existing enabled entities are not silently disabled
during an upgrade.

Each numbered **Stream** camera entity corresponds to metadata discovered from the NAS.
The integration creates entities only for `streamN` objects returned by Surveillance Station:
one reported stream creates one entity, while three reported streams create three. Synology
does not expose a separate health flag for each profile, so availability follows the parent
camera's connection state.

The high-quality profile uses Synology's RTSP path with TCP transport and the low-bandwidth profile
uses its compatibility MJPEG path. This allows both profiles to coexist in Home Assistant;
use the low-bandwidth entity in overview dashboards and open the high-quality entity only
when detail is needed. If a camera reports a third or later stream for which the public
Synology API supplies no distinct live path, the entity remains available for snapshots and
clearly omits the live-stream feature instead of silently playing the wrong profile.

A minimal notification automation looks like this after replacing the entity and notify
service IDs:

```yaml
triggers:
  - trigger: state
    entity_id: event.camera_1_detekce
    not_from:
      - unknown
      - unavailable
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "{{ trigger.to_state.attributes.camera_name }}"
      message: >-
        New camera event: {{ trigger.to_state.attributes.event_type }}
      data:
        image: /api/image_proxy/image.camera_1_snimek_posledni_udalosti
mode: queued
max: 10
```

## Compatibility and limits

- Target: Home Assistant 2026.8 or newer and Surveillance Station 9.x.
- Live View permission is required for live camera entities; snapshot, event, and recording
  permissions remain independently least-privilege.
- Playback is passed through without transcoding. H.264 MP4 is the safest choice; H.265
  support depends on the browser, app, and playback target.
- Event history is Home Assistant's normal event entity history. Recording history remains
  on the NAS and is queried on demand.
- Each selected Media Source range returns the newest 200 recordings to keep NAS queries and
  the Home Assistant browser responsive.
- When Synology supplies a recording reason, the integration emits its exact localized
  type and can safely attach that recording. When Surveillance Station omits the reason,
  the documented detection counter still produces the truthful generic type
  `event_recording`, never `unknown`, but no potentially unrelated clip is attached.
- Surveillance Station 9 may omit the recording reason from `Recording.List`. The
  documented filtered `CountByCategory` counter is therefore the sole authority for new
  events; `Recording.List` is only a best-effort enrichment source. New continuous
  recordings alone do not trigger events.
- Existing recordings are seeded after a restart and are never replayed as new events.
- Because isolated-network polling is used instead of a Synology webhook, the image is a
  current camera snapshot taken when Home Assistant observes the event, up to one configured
  polling interval later. It is not a frame extracted from the historical recording.
- The integration keeps one latest event and a snapshot of at most 2 MiB per camera in a
  separate private storage record, so one damaged camera record cannot affect the others.
  It never writes surveillance images into `/config/www` or the Recorder database.
- The lightweight detection counter is read at the configured interval. The heavier
  recording list is requested only when that counter changes. A failed counter keeps its
  last good baseline, so the next successful poll recovers the missed delta. Connectivity
  refreshes every minute and the diagnostic total-recording counter every five minutes.
- Camera failures are isolated, stale network-backed entities become unavailable, expired
  credentials start Home Assistant's reauthentication flow, and all API requests use
  bounded timeouts. A new event without a snapshot clears the previous image instead of
  displaying a stale picture.
- Detailed event attributes remain available to automations in the current state but are
  excluded from Recorder history; Home Assistant stores the event timestamp and type.

Before reporting a beta problem, download the integration's diagnostic data from Home
Assistant. It removes credentials, IP addresses, recording paths, session identifiers, and
image data. See [Beta testing](docs/BETA_TESTING.md) for a reproducible checklist and
[Troubleshooting](docs/TROUBLESHOOTING.md) for common failures.

## Security

Use a dedicated least-privilege Synology account with access only to the required cameras
and recordings. Accounts created in Surveillance Station are synchronized to DSM. Treat
the service-account password as a secret. The NAS does not need to reach Home Assistant;
only Home Assistant initiates API connections. The service account must be allowed to use
the API without an interactive OTP challenge.

No Surveillance Station webhook or Action Rule is needed.

## Support and responsible reporting

- Functional bugs and compatibility reports: use the GitHub issue forms.
- Security vulnerabilities: follow [SECURITY.md](SECURITY.md) and do not publish credentials,
  diagnostics containing unexpected private data, or camera images in a public issue.
- Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).

This project is not affiliated with or endorsed by Synology Inc. Synology and Surveillance
Station are trademarks of their respective owner.

## Why a separate integration?

This narrow companion design avoids patching or pinning the dependency used by the official
`synology_dsm` integration. It also lets detection and recording support mature in HACS
without destabilizing the official live-camera and Home Mode functionality.
