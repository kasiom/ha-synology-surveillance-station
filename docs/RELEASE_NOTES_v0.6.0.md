# Synology Surveillance Station v0.6.0 — public beta

This is the first public beta for broader hardware validation. It is read-only and complements
Home Assistant's official Synology DSM integration.

## Highlights

- native camera event entities driven by documented Surveillance Station counters;
- authenticated last-event snapshots without `/config/www` files;
- per-camera event counters and connectivity;
- on-demand recording browsing through Home Assistant Media;
- authenticated range-aware recording playback proxy;
- polling-only architecture for isolated camera networks;
- restart-safe latest-event persistence and automatic reauthentication;
- Czech and English UI translations and native light/dark branding.

## Validated environment

- Home Assistant 2026.8.3;
- Surveillance Station 9.3.12139;
- three HIKVISION cameras;
- HTTPS with a self-signed NAS certificate and certificate verification disabled;
- NAS and Home Assistant on isolated networks where only Home Assistant can reach the NAS.

Other Surveillance Station 9.x and camera combinations are beta-test targets, not yet claimed
as verified. Follow `docs/BETA_TESTING.md` when reporting results.

## Upgrade notes

No webhook or Surveillance Station Action Rule is used. Existing pre-0.3 webhook identifiers
are removed automatically. The integration keeps entity unique IDs stable across upgrades.
