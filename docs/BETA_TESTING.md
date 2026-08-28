# Public beta testing

The goal of the `0.6.x` beta is to validate documented API behavior across Synology models,
Surveillance Station 9.x releases, camera vendors, and recording configurations without
putting recordings at risk. The integration is read-only.

## Before testing

- Use the latest published beta.
- Use a dedicated DSM account with access only to the cameras and recordings under test.
- Keep the official `synology_dsm` integration for live views and Home Mode.
- Remove any old Surveillance Station Action Rule or webhook made for this integration; it is
  neither used nor required.
- Keep **Verify TLS certificate** enabled only when the NAS certificate is trusted by Home
  Assistant. A self-signed certificate normally requires that option to be disabled.

## Compatibility checklist

1. Add the integration and select only intended cameras in **Configure**.
2. Confirm each camera device has **Camera event**, **Last event snapshot**, **Events today**,
   and **Connection**.
3. Trigger a real motion or analytics event on one camera.
4. Confirm only that camera's event entity changes and the snapshot updates.
5. Confirm **Events today** increases once, without duplicate events.
6. Start or wait for a continuous recording segment and confirm it does not create a camera
   event.
7. Browse a recent range under **Media → Synology Surveillance Station** and play a clip.
8. Restart Home Assistant and confirm the latest event and snapshot are restored without
   replaying an old event.
9. Temporarily deny the account or change its password, confirm Home Assistant offers
   reauthentication, then restore it.

## What to report

Open the compatibility-report issue form and include:

- Synology NAS model and DSM version;
- Surveillance Station version;
- Home Assistant installation type and version;
- camera vendor/model and codec;
- continuous, motion, alarm, analytics, or action-rule recording mode;
- which checklist items passed or failed;
- sanitized diagnostics downloaded from the integration page.

Diagnostics intentionally omit credentials, IP addresses, session IDs, recording paths, and
image bytes. Review the JSON before posting it anyway. Do not attach snapshots or videos unless
you explicitly intend to publish them.
