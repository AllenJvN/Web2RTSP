"""HTTP API and management UI for Web2RTSP."""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .config import load_config, masked_config, merge_masked, save_config
from .runtime import RuntimeManager

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
LOG_LEVEL_ALIASES = {
    "TRACE": "DEBUG",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "NOTICE": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "FATAL": "CRITICAL",
    "CRITICAL": "CRITICAL",
}


def configured_log_level(options_path: Path | None = None) -> str:
    """Read the HA App log option, with an environment override for Docker."""
    environment = os.getenv("LOG_LEVEL", "").upper()
    if environment in LOG_LEVEL_ALIASES:
        return LOG_LEVEL_ALIASES[environment]
    path = options_path or Path(os.getenv("OPTIONS_PATH", "/data/options.json"))
    try:
        configured = str(json.loads(path.read_text(encoding="utf-8")).get("log_level", "INFO"))
    except (OSError, ValueError, TypeError):
        configured = "INFO"
    configured = configured.upper()
    return LOG_LEVEL_ALIASES.get(configured, "INFO")


def _host(request: web.Request) -> str:
    configured = os.getenv("PUBLIC_HOST", "").strip()
    if configured:
        return configured
    authority = request.headers.get("X-Forwarded-Host", "") or request.host
    forwarded = urlsplit(f"//{authority}").hostname or ""
    if forwarded and forwarded not in {"supervisor", "localhost", "127.0.0.1"}:
        return forwarded
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "homeassistant.local"


def _advertised_host(request: web.Request) -> str:
    configured = request.app["config"]["rtsp"].get("advertise_host", "").strip()
    host = configured or _host(request)
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(ROOT / "static" / "index.html")


async def api_get_config(request: web.Request) -> web.Response:
    return web.json_response(masked_config(request.app["config"]))


async def api_save_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        merged = merge_masked(body, request.app["config"])
        config = save_config(request.app["config_path"], merged)
        request.app["config"] = config
        await request.app["manager"].apply_config(config)
        return web.json_response({"ok": True, "config": masked_config(config)})
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("failed to save configuration")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(request.app["manager"].status(_advertised_host(request)))


async def api_restart(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        await request.app["manager"].restart_stream(name)
    except KeyError as exc:
        raise web.HTTPNotFound(reason="unknown or disabled stream") from exc
    return web.json_response({"ok": True})


async def api_snapshot(request: web.Request) -> web.Response:
    worker = request.app["manager"].workers.get(request.match_info["name"])
    if not worker:
        raise web.HTTPNotFound(reason="unknown or disabled stream")
    try:
        image = await worker.snapshot()
    except RuntimeError as exc:
        raise web.HTTPConflict(reason=str(exc)) from exc
    return web.Response(body=image, content_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def health(request: web.Request) -> web.Response:
    status = request.app["manager"].status(_advertised_host(request))
    media_ok = status["mediamtx"]["running"]
    failed = [s for s in status["streams"] if s["state"] == "error"]
    return web.json_response(
        {"ok": media_ok and not failed, "mediamtx": media_ok, "failed_streams": len(failed)},
        status=200 if media_ok else 503,
    )


async def on_startup(app: web.Application) -> None:
    await app["manager"].start()


async def on_cleanup(app: web.Application) -> None:
    await app["manager"].stop()


def create_app(config_path: Path | None = None, runtime_dir: Path | None = None) -> web.Application:
    config_path = config_path or Path(os.getenv("CONFIG_PATH", "/data/web2rtsp.json"))
    runtime_dir = runtime_dir or Path(os.getenv("RUNTIME_DIR", "/tmp/web2rtsp"))
    config = load_config(config_path)
    app = web.Application(client_max_size=256 * 1024)
    app["config_path"] = config_path
    app["config"] = config
    app["manager"] = RuntimeManager(config, runtime_dir)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/config", api_get_config)
    app.router.add_put("/api/config", api_save_config)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/streams/{name}/restart", api_restart)
    app.router.add_get("/api/streams/{name}/snapshot", api_snapshot)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    level = configured_log_level()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8099")))


if __name__ == "__main__":
    main()
