# Security policy

## Supported version

Only the newest published beta or stable release receives security fixes.

## Reporting a vulnerability

Do not open a public issue for a credential leak, authentication bypass, private recording
exposure, or another security-sensitive problem. Open the repository's **Security** tab, choose
**Advisories**, and use **Report a vulnerability** to contact the maintainer privately.

Include the integration version, Home Assistant version, Surveillance Station version, and a
minimal reproduction. Remove passwords, cookies, Synology session IDs, IP addresses, recording
paths, snapshots, and video content. You should receive an acknowledgement within seven days.

The integration is designed so that Home Assistant initiates all NAS traffic. Use a dedicated
least-privilege account and do not expose DSM or Home Assistant directly to the public internet.
