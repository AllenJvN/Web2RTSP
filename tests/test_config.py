import json
import os

import pytest

from web2rtsp.config import MASK, load_config, masked_config, merge_masked, save_config


def sample_config():
    return {
        "version": 1,
        "rtsp": {
            "port": 8554,
            "hls_port": 8888,
            "username": "viewer",
            "password": "correct-horse",
            "publisher_password": "internal-secret",
        },
        "streams": [
            {
                "name": "ha_dashboard",
                "url": "http://homeassistant.local:8123/dashboard-cameras/broadcast",
                "enabled": True,
                "width": 1280,
                "height": 720,
                "fps": 10,
                "bitrate_kbps": 1800,
                "reload_seconds": 3600,
                "auth": {
                    "strategy": "ha_token",
                    "base_url": "http://homeassistant.local:8123",
                    "token": "ha-secret",
                    "headers": {},
                },
            }
        ],
    }


def test_round_trip_and_permissions(tmp_path):
    path = tmp_path / "web2rtsp.json"
    saved = save_config(path, sample_config())
    assert load_config(path) == saved
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["streams"][0]["auth"]["token"] == "ha-secret"


def test_mask_and_merge_preserves_secrets():
    original = sample_config()
    masked = masked_config(original)
    assert masked["rtsp"]["password"] == MASK
    assert masked["streams"][0]["auth"]["token"] == MASK
    merged = merge_masked(masked, original)
    assert merged == original


@pytest.mark.parametrize(
    ("field", "value"),
    [("fps", 31), ("width", 10), ("bitrate_kbps", 100_000)],
)
def test_rejects_out_of_range_stream_values(tmp_path, field, value):
    config = sample_config()
    config["streams"][0][field] = value
    with pytest.raises(ValueError, match=field):
        save_config(tmp_path / "config.json", config)


def test_rejects_duplicate_names(tmp_path):
    config = sample_config()
    config["streams"].append(dict(config["streams"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        save_config(tmp_path / "config.json", config)


def test_rejects_non_http_url(tmp_path):
    config = sample_config()
    config["streams"][0]["url"] = "file:///etc/passwd"
    with pytest.raises(ValueError, match="HTTP"):
        save_config(tmp_path / "config.json", config)


def test_first_run_env_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBPAGE_URL", "https://example.com/status")
    monkeypatch.setenv("STREAM_FPS", "5")
    config = load_config(tmp_path / "config.json")
    assert config["streams"][0]["url"] == "https://example.com/status"
    assert config["streams"][0]["fps"] == 5
    assert config["streams"][0]["auth"]["strategy"] == "none"
