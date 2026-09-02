import json

from web2rtsp.app import configured_log_level


def test_log_level_reads_home_assistant_options(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"log_level": "debug"}), encoding="utf-8")
    assert configured_log_level(options) == "DEBUG"


def test_log_level_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert configured_log_level(tmp_path / "missing.json") == "WARNING"


def test_log_level_falls_back_for_invalid_value(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"log_level": "verbose"}), encoding="utf-8")
    assert configured_log_level(options) == "INFO"


def test_home_assistant_log_level_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"log_level": "trace"}), encoding="utf-8")
    assert configured_log_level(options) == "DEBUG"
    options.write_text(json.dumps({"log_level": "notice"}), encoding="utf-8")
    assert configured_log_level(options) == "INFO"
    options.write_text(json.dumps({"log_level": "fatal"}), encoding="utf-8")
    assert configured_log_level(options) == "CRITICAL"
