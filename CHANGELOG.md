# Changelog

## 0.6.1 — functional Home Assistant image rendering

- Restore the standard authenticated `ImageEntity` proxy URL so the last-event snapshot opens
  and renders in Home Assistant cards, entity dialogs, and notifications.
- Keep `mdi:image-outline` as the fallback icon before a snapshot is available; once an image
  exists, Home Assistant may correctly show its thumbnail instead of the static icon.
- Document that polling captures the current camera image when Home Assistant observes the
  event rather than extracting a historical frame from its recording.

## 0.6.0 — reliability and truthful event correlation

- Prepare the first public beta with HACS metadata, repository ownership and support links,
  installation and beta-testing guides, issue forms, security policy, and release notes.
- Add automated HACS, Hassfest, Python 3.14, lint, compilation, coverage, config-flow, setup,
  unload, and authentication-failure checks.
- Make the documented filtered event counter the sole event authority and retain its last
  successful baseline across transient failures, recovering the full missed delta.
- Attach a recording only when its explicit Synology reason proves it is an event and its
  timestamp belongs to the current counter window; ambiguous rows are emitted safely as a
  generic event without a potentially wrong clip.
- Isolate count, recording-list, snapshot, and metadata failures per camera.
- Mark stale event counters, recording counters, and connectivity entities unavailable,
  and start Home Assistant reauthentication once when credentials expire.
- Add bounded API timeouts and an 8 MiB snapshot response limit.
- Clear the old image when a newer event has no snapshot.
- Use the complete server, mount, and recording tuple for media playback identity.
- Remove per-camera private storage and stale devices when cameras or the integration are
  removed; add a manual stale-device cleanup path.
- Refresh the camera inventory when opening integration options and expand regression tests.

## 0.5.0 — focused entities and efficient polling

- Add per-entry camera selection to the options flow.
- Add a primary Events today sensor based on the documented filtered detection counter.
- Make the redundant Last event timestamp and total Recordings today sensors diagnostic
  and disabled by default for new entities without changing existing unique IDs.
- Read recording lists only after a detection-counter change; keep the safe recording-list
  fallback when the counter endpoint fails.
- Refresh connectivity every minute and the diagnostic total-recording count every five
  minutes.
- Persist the newest event and up-to-2-MiB snapshot in a separate private store per camera,
  with automatic migration from the 0.4 shared store.
- Exclude verbose event attributes from Recorder while retaining them for live automations.
- Add an end-to-end notification automation example and expand privacy-safe diagnostics.

## 0.4.5 — valid static snapshot icon

- Replace the nonexistent `mdi:image-clock` identifier with the verified
  `mdi:image-outline` icon so image entities render a static icon in Home Assistant's
  device entity card.
- Keep the actual snapshot available through the image entity's more-info dialog and
  authenticated image proxy.

## 0.4.4 — event naming model

- Name the entity `Camera event`, the fallback type `Event recording`, and the timestamp
  sensor `Last event`, avoiding repetitive labels in Home Assistant's more-info dialog.
- Preserve all entity IDs and unique IDs so existing automations and dashboards continue
  to work.
- Migrate the current generic type from `detection` to `event_recording` without firing a
  false event; Recorder history remains untouched.

## 0.4.3 — meaningful detection types

- Replace `unknown` with the localized generic `detection` type when the documented
  counter verifies an event but Surveillance Station omits its recording reason.
- Map documented recording reasons to motion, alarm, custom, external, analytics, and
  action-rule event types whenever the NAS provides them.
- Add native Home Assistant translations for every emitted event type.
- Migrate the current restored `unknown` label without creating a false new event; existing
  historical logbook rows remain unchanged.

## 0.4.2 — restart-safe latest events

- Persist one latest event and snapshot per camera in Home Assistant's private storage.
- Restore last-detection timestamps and images before entities are created after a restart.
- Bootstrap an empty store only when the daily recording and documented detection counters
  prove that the newest recording is a detection.
- Give image entities a standard static MDI icon in device lists while their image endpoint
  continues to serve the actual last-event snapshot.

## 0.4.1 — reliable detection correlation

- Correlate new recordings with the documented `CountByCategory` event counter because
  Surveillance Station 9 omits `reason` from `Recording.List` responses.
- Keep continuous recording segments from producing false detection events.
- Mark last-event image entities unavailable until a real snapshot exists, avoiding a
  broken-image placeholder in the Home Assistant UI.
- Keep last-event snapshots out of entity badges so lists use a consistent static icon.
- Use the square light/dark brand mark as Home Assistant's logo fallback; this avoids an
  unreadable wordmark on compact device pages.

## 0.4.0 — native branding and camera context

- Add a coordinated light/dark integration icon using Home Assistant's local
  custom-integration brand directory.
- Add translated entity icons for event, image, timestamp, counter, and connectivity
  entities.
- Add a last-detection timestamp and exact recordings-today sensor for every camera.
- Add a connectivity binary sensor based on the documented Camera List v9 status already
  available to the least-privilege account.
- Refresh counts and connectivity once per minute without adding per-entity polling.
- Extend privacy-safe diagnostics with metadata refresh health.

## 0.3.0 — polling-only network architecture

- Remove the incoming webhook and its Surveillance Station action-rule setup.
- Keep all event detection, snapshots, and recordings on the HA-to-NAS API path.
- Automatically remove the obsolete webhook identifier from upgraded config entries.
- Mark the integration as `local_polling` and simplify its options and diagnostics.

## 0.2.0 — isolated-network event polling

- Detect new motion and analytics recordings by polling the documented Recording API.
- Add API polling as an alternative to webhook delivery.
- Seed existing recordings on startup so a restart never replays historical events.
- Add configurable 5–300 second polling intervals and privacy-safe polling diagnostics.

## 0.1.2 — webhook module compatibility

- Prevent the integration's webhook parser module from shadowing Home Assistant's webhook helper.

## 0.1.1 — API discovery compatibility

- Query the four required Web APIs explicitly instead of relying on an undocumented wildcard.
- Clarify that accounts created in Surveillance Station are synchronized to DSM.

## 0.1.0 — hardware-validation release

- Discover and authenticate against the documented Surveillance Station Web API.
- Create a native detection event and last-event snapshot image for every camera.
- Accept JSON, form, query-string, and multipart-image webhook payloads.
- Normalize common motion, smart-detection, sound, tampering, and doorbell event names.
- Browse the last 1, 7, or 30 days of recordings through Home Assistant Media.
- Proxy MP4 recording streams with authentication and byte-range support.
- Add English and Czech setup strings and privacy-safe diagnostics.
