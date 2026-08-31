# Synology Surveillance Station v0.6.2 — complete snapshot transfer

This beta fixes snapshots that appeared as a thin strip or an empty black image. The NAS was
returning a normal multi-chunk JPEG response, but the integration retained only its first
network chunk.

## Fixed

- Read all snapshot response chunks through EOF.
- Keep the existing 8 MiB safety limit while streaming the response.
- Reject an incomplete JPEG instead of storing and presenting it as a valid event image.
- Replace snapshots already truncated by an older beta with a fresh camera image during the
  first restart, without emitting a duplicate camera event.
- Add regression tests for fragmented and truncated JPEG responses and upgrade repair.

The standard authenticated Home Assistant image proxy introduced in v0.6.1 remains enabled.
No webhook or Surveillance Station Action Rule is required.
