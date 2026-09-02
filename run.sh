#!/usr/bin/env bash
set -euo pipefail

export CONFIG_PATH="${CONFIG_PATH:-/data/web2rtsp.json}"
export RUNTIME_DIR="${RUNTIME_DIR:-/tmp/web2rtsp}"
export CHROMIUM_PATH="${CHROMIUM_PATH:-/usr/bin/chromium}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/ms-playwright}"

exec python3 -m web2rtsp.app
