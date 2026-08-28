# Troubleshooting

## Cannot connect

- Confirm the address is the NAS address reachable from Home Assistant, not from the browser
  you are currently using.
- DSM normally uses port `5001` for HTTPS and `5000` for HTTP, unless customized.
- Keep **Use HTTPS** enabled when using `5001`.
- Disable **Verify TLS certificate** only for a self-signed or otherwise untrusted NAS
  certificate. Prefer installing a trusted certificate where practical.
- The NAS does not need a route back to Home Assistant; this integration uses outbound polling
  from Home Assistant to the NAS.

## Unsupported API

Upgrade Surveillance Station to a supported 9.x release. The integration discovers the
required API versions before creating the config entry and refuses to guess when an endpoint is
missing.

## No camera event

- Confirm API event polling is enabled under **Configure**.
- Confirm the camera is selected and the account can read its recordings.
- Trigger a recording-producing motion or analytics rule. Plain camera-side motion that does
  not create a Surveillance Station event recording cannot be observed through the recording
  counter.
- Wait at least one polling interval. Existing recordings are deliberately seeded and not
  replayed after setup or restart.

## Event without a clip or specific type

Some Surveillance Station 9 installations omit the recording reason from `Recording.List`.
The integration then emits the truthful generic **Event recording** type and does not attach a
possibly unrelated recording. This is safer than labelling a continuous segment as motion.

## Snapshot unavailable

The image entity remains unavailable until the first verified event has a snapshot. A newer
event that cannot produce a snapshot clears the old image so Home Assistant never displays a
stale picture as current.

## Recording will not play

The integration proxies the original recording and does not transcode it. H.264 MP4 has the
widest browser and app support. H.265 playback depends on the client. Download integration
diagnostics and open a bug report if H.264 playback fails.

## Useful diagnostics

Open **Settings → Devices & services → Synology Surveillance Station**, open the integration
entry, and choose **Download diagnostics**. Also capture relevant Home Assistant log lines after
enabling debug logging:

```yaml
logger:
  logs:
    custom_components.synology_surveillance_station: debug
```

Disable debug logging after reproducing the problem.
