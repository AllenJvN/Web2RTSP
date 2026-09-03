# Changelog

## 0.1.3 — pre-release

- Reduced software H.264 encoder cost with the tested x264 `superfast` preset and a single encoder thread.
- Preserved the existing 1280×720, 10 FPS, H.264 Baseline/yuv420p NVR output contract.
- Verified the candidate with an animated stress page, independent RTSP decoding, and native-resolution dashboard-card inspection.

## 0.1.2 — pre-release

- Added container-local diagnostics for Chromium, FFmpeg, Xvfb, MediaMTX, and the app/Playwright process tree.
- Added a cached diagnostics API, five-minute in-memory history, UI charts, and a JSON download without URLs, credentials, command lines, or logs.
- Distinguished one-core CPU percentages, container-capacity percentages, PSS, RSS, and cgroup memory accounting; missing data is reported as unavailable.
- Kept configured FPS and browser heartbeats explicitly separate from measured video delivery.
- Periodic status refresh no longer rebuilds configuration forms or erases unsaved input.
- Diagnostics require no Docker socket, host PID access, new dependencies, or protection-mode changes.

## 0.1.1 — pre-release

- Added an explicit advertised host/IP so ingress displays an NVR-reachable RTSP URL.
- Mapped all Home Assistant App log-level choices to Python logging levels.

## 0.1.0 — pre-release

- Initial Home Assistant App and standalone Docker implementation.
- Multi-stream Chromium rendering and H.264 RTSP publication.
- HA-token, HTTP-header, and unauthenticated URL modes.
- Ingress UI, masked persistent configuration, status, snapshots, and stream restarts.
- Embedded MediaMTX with authenticated RTSP readers and loopback-only publishers.
