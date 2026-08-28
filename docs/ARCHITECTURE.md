# Architecture

```text
CountByCategory API ◀────────poll────────── Home Assistant
              │                              every 5–300 s
              │ authoritative count delta
              ├──────────────┬──────────────────────┐
              │              │                      │
              ▼              ▼                      ▼
       Recording.List     snapshot              generic event
       explicit reason   best effort        if no safe recording
              └──────────────┬──────────────────────┘
                             │ normalize + deduplicate
       ┌──────┬─────────┬────────────────┐
       ▼      ▼         ▼                ▼
     event  image    events today   diagnostics
 automation snapshot   sensor          sensors

Camera List API ◀──60 s── Home Assistant ──> connectivity sensor

Home Assistant Media ──API List──> recording metadata on NAS
        │
        └──authenticated HA proxy──API Stream──> MP4 bytes on NAS
```

The live stream is intentionally outside this integration and stays in `synology_dsm`.

## Boundaries

- `api.py` is a Home Assistant-independent client for the documented Synology endpoints.
- `runtime.py` owns per-entry matching, deduplication, last images, recording metadata,
  daily counts, and camera connectivity state.
- `persistence.py` stores only the newest normalized event and an up-to-2-MiB snapshot for
  each camera in a separate Home Assistant private-storage record. Version 0.5 migrates
  the old shared record automatically.
- `Recording.List` does not reliably include a recording reason on Surveillance Station 9.
  A filtered `CountByCategory` delta is the sole event authority. A listed recording is
  attached only when its own explicit reason and timestamp make the match safe; otherwise
  the integration emits a generic counter-verified event without a clip.
- Entities subscribe to runtime callbacks; one shared scheduler polls lightweight event
  counters at the configured interval. It lists recordings only for changed counters,
  refreshes status every minute, and refreshes the diagnostic
  total-recording count every five minutes. Entities never poll independently.
- Media Source queries recordings only when a user browses a time range.
- `views.py` streams a selected recording under Home Assistant authentication.

No NAS password, API session, or snapshot is written to logs or diagnostics. One latest
snapshot per camera is kept in separate private Home Assistant storage and replaced by the next
event; it is never written to `/config/www` or the Recorder database.

## Deliberate first-release choices

One flexible detection entity per camera is used until real recordings show which event
families are reliably distinguishable across camera vendors. This avoids creating many
entities that never fire. A single exact daily counter and timestamp provide useful
dashboard context without duplicating Home Assistant history. Actions that mutate
surveillance state remain deferred until read-only behavior is proven on hardware.
