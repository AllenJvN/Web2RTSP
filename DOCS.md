# Web2RTSP app

Open the Web UI, change the default RTSP viewer password, and add one or more virtual cameras. A stream may render a public webpage, use custom HTTP headers, or authenticate to Home Assistant with a dedicated long-lived access token.

For a first NVR test, use 1280×720, 10 FPS, and 1800 kbps. Configure the NVR as a custom RTSP camera using the URL shown beside the running stream, the viewer credentials from the Web2RTSP UI, and RTSP over TCP. Audio is not provided.

The HLS listener is for diagnostics. Keep the management, RTSP, and HLS ports on a trusted LAN and do not forward them to the internet.

See the project [README](https://github.com/AllenJvN/Web2RTSP#readme) for complete setup, security, standalone Docker, and troubleshooting details.
