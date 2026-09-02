# Security policy

Web2RTSP is pre-release software. Report vulnerabilities privately to the repository owner rather than opening an issue containing tokens, passwords, internal URLs, screenshots, or logs.

## Deployment requirements

- Change the default RTSP password before enabling a stream.
- Keep management, RTSP, and HLS ports on a trusted LAN or camera VLAN.
- Never expose the standalone management API directly to the internet.
- Use a dedicated, least-privileged Home Assistant user/token.
- Treat `/data/web2rtsp.json` and backups containing it as secrets.
- Review configured arbitrary URLs: the renderer can reach internal services available to the container.
- Retain bounded container logging and monitor disk consumption.

Diagnostic output deliberately excludes configured tokens and RTSP passwords. Do not post a complete configuration file when requesting support.
