# Web2RTSP app

Open the Web UI, set **Advertised host/IP** to the Home Assistant LAN address reachable by your NVR, change the default RTSP viewer password, and add one or more virtual cameras. A stream may render a public webpage, use custom HTTP headers, or authenticate to Home Assistant with a dedicated long-lived access token.

For a first NVR test, use 1280×720, 10 FPS, and 1800 kbps. Configure the NVR as a custom RTSP camera using the URL shown beside the running stream, the viewer credentials from the Web2RTSP UI, and RTSP over TCP. Audio is not provided.

The HLS listener is for diagnostics. Keep the management, RTSP, and HLS ports on a trusted LAN and do not forward them to the internet.

The **Resource diagnostics** panel shows CPU and memory by component, container
totals, stream health, and about five minutes of history. CPU uses a one-core scale
(100% = one CPU); PSS apportions shared memory while RSS can double-count it.
Unavailable readings show a dash, not zero. **Download JSON** exports metrics
without URLs, credentials, or logs. Protection mode can remain enabled.

For HA history and alerts, enable the existing Web2RTSP CPU Percent, Memory
Percent, and Running binary sensor under the Home Assistant Supervisor integration.
The app does not auto-register duplicate entities. Configured FPS and browser
heartbeats are not proof of actual frame delivery to the NVR.

See the project [README](https://github.com/AllenJvN/Web2RTSP#readme) for complete setup, security, standalone Docker, and troubleshooting details.
