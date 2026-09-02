"""Configuration loading, validation, persistence, and secret masking."""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlparse

MASK = "***"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ALLOWED_AUTH = {"none", "ha_token", "http_header"}


def default_config() -> dict:
    return {
        "version": 1,
        "rtsp": {
            "port": 8554,
            "hls_port": 8888,
            "advertise_host": "",
            "username": "viewer",
            "password": "change-me",
            "publisher_password": secrets.token_urlsafe(24),
        },
        "streams": [],
    }


def _env_seed(config: dict) -> None:
    """Seed a first-run standalone configuration from environment variables."""
    url = os.getenv("WEBPAGE_URL", "").strip()
    if not url or config["streams"]:
        return
    strategy = "ha_token" if os.getenv("HA_TOKEN") else "none"
    config["streams"].append(
        {
            "name": os.getenv("STREAM_NAME", "dashboard"),
            "url": url,
            "enabled": True,
            "width": int(os.getenv("STREAM_WIDTH", "1280")),
            "height": int(os.getenv("STREAM_HEIGHT", "720")),
            "fps": int(os.getenv("STREAM_FPS", "10")),
            "bitrate_kbps": int(os.getenv("STREAM_BITRATE_KBPS", "1800")),
            "reload_seconds": int(os.getenv("STREAM_RELOAD_SECONDS", "3600")),
            "auth": {
                "strategy": strategy,
                "base_url": os.getenv("HA_URL", "").rstrip("/"),
                "token": os.getenv("HA_TOKEN", ""),
                "headers": {},
            },
        }
    )


def validate_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("configuration must be an object")
    cfg = copy.deepcopy(raw)
    cfg.setdefault("version", 1)
    rtsp = cfg.setdefault("rtsp", {})
    rtsp.setdefault("port", 8554)
    rtsp.setdefault("hls_port", 8888)
    rtsp.setdefault("advertise_host", "")
    rtsp.setdefault("username", "viewer")
    rtsp.setdefault("password", "change-me")
    rtsp.setdefault("publisher_password", secrets.token_urlsafe(24))
    for key in ("port", "hls_port"):
        value = int(rtsp[key])
        if not 1 <= value <= 65535:
            raise ValueError(f"rtsp.{key} must be between 1 and 65535")
        rtsp[key] = value
    if rtsp["port"] == rtsp["hls_port"]:
        raise ValueError("RTSP and HLS ports must differ")
    advertise_host = str(rtsp["advertise_host"]).strip()
    if advertise_host and (
        len(advertise_host) > 253
        or not re.fullmatch(r"[A-Za-z0-9_.:-]+", advertise_host)
    ):
        raise ValueError("rtsp.advertise_host must be a hostname or IP address")
    rtsp["advertise_host"] = advertise_host
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(rtsp["username"])):
        raise ValueError("RTSP username contains unsupported characters")
    if not 1 <= len(str(rtsp["password"])) <= 128:
        raise ValueError("RTSP password must contain 1-128 characters")

    streams = cfg.setdefault("streams", [])
    if not isinstance(streams, list) or len(streams) > 8:
        raise ValueError("streams must be a list containing at most 8 entries")
    seen: set[str] = set()
    normalized = []
    for index, stream in enumerate(streams):
        if not isinstance(stream, dict):
            raise ValueError(f"streams[{index}] must be an object")
        item = copy.deepcopy(stream)
        name = str(item.get("name", "")).lower().strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"streams[{index}].name must match {NAME_RE.pattern}")
        if name in seen:
            raise ValueError(f"duplicate stream name: {name}")
        seen.add(name)
        item["name"] = name
        url = str(item.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"streams[{index}].url must be an HTTP(S) URL")
        item["url"] = url
        item["enabled"] = bool(item.get("enabled", True))
        for key, default, low, high in (
            ("width", 1280, 320, 3840),
            ("height", 720, 240, 2160),
            ("fps", 10, 1, 30),
            ("bitrate_kbps", 1800, 250, 12000),
            ("reload_seconds", 3600, 0, 86400),
        ):
            value = int(item.get(key, default))
            if not low <= value <= high:
                raise ValueError(f"streams[{index}].{key} must be {low}-{high}")
            item[key] = value
        auth = item.setdefault("auth", {})
        strategy = str(auth.get("strategy", "none"))
        if strategy not in ALLOWED_AUTH:
            raise ValueError(f"unsupported authentication strategy: {strategy}")
        auth["strategy"] = strategy
        auth["base_url"] = str(auth.get("base_url", "")).rstrip("/")
        auth["token"] = str(auth.get("token", ""))
        headers = auth.get("headers", {})
        if not isinstance(headers, dict) or len(headers) > 16:
            raise ValueError("auth.headers must be an object with at most 16 entries")
        auth["headers"] = {str(k): str(v) for k, v in headers.items()}
        if strategy == "ha_token":
            base = urlparse(auth["base_url"])
            if base.scheme not in {"http", "https"} or not base.netloc:
                raise ValueError(f"streams[{index}] requires a valid auth.base_url")
            if not auth["token"]:
                raise ValueError(f"streams[{index}] requires an HA token")
        normalized.append(item)
    cfg["streams"] = normalized
    return cfg


def save_config(path: Path, config: dict) -> dict:
    config = validate_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".web2rtsp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return config


def load_config(path: Path) -> dict:
    if path.exists():
        return validate_config(json.loads(path.read_text(encoding="utf-8")))
    config = default_config()
    _env_seed(config)
    return save_config(path, config)


def masked_config(config: dict) -> dict:
    result = copy.deepcopy(config)
    rtsp = result.get("rtsp", {})
    for key in ("password", "publisher_password"):
        if rtsp.get(key):
            rtsp[key] = MASK
    for stream in result.get("streams", []):
        auth = stream.get("auth", {})
        if auth.get("token"):
            auth["token"] = MASK
        auth["headers"] = dict.fromkeys(auth.get("headers", {}), MASK)
    return result


def merge_masked(incoming: dict, existing: dict) -> dict:
    """Replace mask placeholders with their current secret values."""
    result = copy.deepcopy(incoming)
    old_rtsp = existing.get("rtsp", {})
    new_rtsp = result.setdefault("rtsp", {})
    for key in ("password", "publisher_password"):
        if new_rtsp.get(key) == MASK:
            new_rtsp[key] = old_rtsp.get(key, "")
    old_streams = {s.get("name"): s for s in existing.get("streams", [])}
    for stream in result.get("streams", []):
        old_auth = old_streams.get(stream.get("name"), {}).get("auth", {})
        auth = stream.setdefault("auth", {})
        if auth.get("token") == MASK:
            auth["token"] = old_auth.get("token", "")
        old_headers = old_auth.get("headers", {})
        auth["headers"] = {
            key: old_headers.get(key, "") if value == MASK else value
            for key, value in auth.get("headers", {}).items()
        }
    return result
