# Web2RTSP

Web2RTSP renders a Home Assistant dashboard or any HTTP(S) webpage in Chromium and publishes the rendered display as an H.264 RTSP virtual camera.

```text
Webpage → Chromium/Playwright → Xvfb → FFmpeg/H.264 → MediaMTX → RTSP NVR
```

This repository is both a Home Assistant App repository and a standalone Docker application. The current version is a pre-release intended for controlled LAN testing.

## Features

- Multiple independent webpage streams (up to eight)
- Home Assistant long-lived-token injection, HTTP-header authentication, or no authentication
- H.264 Baseline/yuv420p RTSP over TCP for conservative NVR compatibility
- Home Assistant ingress management UI
- Standalone Docker Compose deployment
- RTSP viewer authentication
- JPEG live-render preview, status, restart counts, and bounded diagnostic tails
- Browser and FFmpeg health supervision with automatic restart
- Optional periodic page reload
- HLS output for convenient browser/VLC checks
- `amd64` and `aarch64` App metadata
- Bounded Docker JSON logs in the supplied Compose deployment

## Home Assistant installation

1. In Home Assistant, open **Settings → Apps → Install app → Repositories**.
2. Add `https://github.com/AllenJvN/Web2RTSP`.
3. Install and start Web2RTSP.
4. Open its Web UI through ingress.
5. Change the default RTSP password, add a stream, and save.

The App exposes TCP 8554 for RTSP and TCP 8888 for HLS. Port 8099 is ingress-only by default. Do not forward these ports to the internet.

## Standalone Docker

```bash
docker compose up --build -d
```

Open `http://HOST:8099`. Persistent configuration is stored in the `web2rtsp_data` volume. The supplied Compose file caps container JSON logs at three 10 MB files.

For a first-run command-line seed, set `WEBPAGE_URL`. Optional variables are `STREAM_NAME`, `STREAM_WIDTH`, `STREAM_HEIGHT`, `STREAM_FPS`, `STREAM_BITRATE_KBPS`, `STREAM_RELOAD_SECONDS`, `HA_URL`, and `HA_TOKEN`. After the first run, the persisted UI configuration is authoritative.

## Adding a stream

Suggested starting values for an NVR dashboard tile:

| Setting | Value |
|---|---:|
| Resolution | 1280×720 |
| Frame rate | 10 FPS |
| Bitrate | 1800 kbps |
| Reload | 3600 seconds |
| Audio | None |

For an HA dashboard, use the dashboard's full internal URL, choose `Home Assistant token`, enter the HA base URL and a dedicated long-lived access token. Use a dedicated non-administrator HA account where practical.

The NVR URL is:

```text
rtsp://HOST:8554/STREAM_NAME
```

Enter the configured viewer username and password in the NVR's separate credential fields. RTSP/TCP should be preferred. The default credentials are `viewer` / `change-me` and must be changed.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Container watchdog health |
| `GET` | `/api/status` | Media server and stream status |
| `GET` | `/api/config` | Masked configuration |
| `PUT` | `/api/config` | Validate, persist, and apply configuration |
| `POST` | `/api/streams/{name}/restart` | Restart one stream |
| `GET` | `/api/streams/{name}/snapshot` | Current rendered JPEG |

## Security model

- The application intentionally makes outbound requests to configured URLs, including private-network URLs. Only trusted administrators should be able to configure it.
- HA tokens and header values are stored in `/data/web2rtsp.json` with mode `0600` and are masked by the API.
- The management API relies on HA ingress authentication when installed as an App. A standalone deployment must remain on a trusted LAN or be placed behind an authenticating reverse proxy.
- RTSP is authenticated but unencrypted. Keep it on a trusted LAN/VLAN. MediaMTX accepts publishers only through an internal random credential restricted to loopback.
- Chromium runs without its setuid sandbox because of container constraints. The App remains Supervisor-protected and does not request host networking, privileged mode, Docker access, or host filesystem mounts.
- Version 0.1.0 has `apparmor: false` pending a tested Chromium-compatible profile. This must be revisited before a stable release.

See [SECURITY.md](SECURITY.md) for reporting and deployment guidance.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
docker build -t web2rtsp:test .
```

The Home Assistant authentication work builds on
[DashSnap](https://github.com/italo-lombardi/DashSnap), while the continuous
render-and-stream architecture was informed by
[stream-webpage-container](https://github.com/Zozman/stream-webpage-container).
Attribution is retained in the relevant source files and Git history.

## Known pre-release limitations

- No ONVIF discovery; configure the NVR as a custom RTSP camera.
- Software x264 encoding only; hardware acceleration is planned after compatibility testing.
- Video only; no audio track.
- Configuration changes restart all streams and MediaMTX.
- The Home Assistant frontend's private token-store shape can change. Authentication failure is surfaced in stream status.
- HLS is intended for diagnostics; the management UI preview uses browser screenshots.
