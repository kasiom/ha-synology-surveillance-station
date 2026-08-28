# Contributing

Thank you for helping improve Synology Surveillance Station for Home Assistant.

## Beta reports

The most useful contribution during the public beta is a report from a different NAS,
Surveillance Station release, camera vendor, or recording mode. Use the compatibility-report
issue form and include Home Assistant diagnostic data. Never include passwords, session IDs,
private camera addresses, recording paths, or camera images.

## Development

1. Fork and clone the repository.
2. Use Python 3.14.
3. Install `requirements_ha_test.txt` in a virtual environment.
4. Run `ruff check .` and `pytest --cov=custom_components/synology_surveillance_station`.
5. Keep behavior read-only unless a feature explicitly documents and tests a mutation.
6. Update English and Czech translations together when changing user-visible text.
7. Add a changelog entry under `Unreleased` for user-visible changes.

Pull requests should be focused and explain how they were tested. API changes should cite a
documented Synology endpoint or include sanitized hardware observations that can be converted
into a regression test.

## Design boundaries

- The official `synology_dsm` integration owns live cameras and Home Mode.
- This integration owns event recordings, last-event snapshots, camera status, and recording
  browsing/playback.
- NAS credentials and session IDs must never be exposed in entity attributes, URLs, logs, or
  diagnostics.
- Continuous recording segments must never be guessed to be detection events.
- Existing NAS recordings must not be modified or deleted.

By submitting a contribution, you agree that it is licensed under the repository's MIT
license.
