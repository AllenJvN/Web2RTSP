import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from web2rtsp.app import create_app
from web2rtsp.diagnostics import Diagnostics, ResourceSampler, owner, read_process
from web2rtsp.runtime import RuntimeManager


def process(proc, pid, parent=1, ticks=100, started=10, rss=10, pss=5):
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    fields = ["0"] * 22
    fields[0], fields[1], fields[11], fields[12] = "S", str(parent), str(ticks), "0"
    fields[19], fields[21] = str(started), str(rss)
    (directory / "stat").write_text(f"{pid} (name with ) spaces) " + " ".join(fields))
    if pss is not None:
        (directory / "smaps_rollup").write_text(f"Rss: 999 kB\nPss: {pss} kB\n")
    return directory


@pytest.fixture
def counters(tmp_path):
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    proc.mkdir()
    cgroup.mkdir()
    now = [0.0]
    sampler = ResourceSampler(proc, cgroup, clock=lambda: now[0], ticks_per_second=100, page_size=4096, cpu_capacity=4)
    return proc, cgroup, now, sampler


def test_group_descendants_once_and_exclude_unrelated(counters):
    proc, _, now, sampler = counters
    process(proc, 10, parent=1)
    process(proc, 20, parent=10)
    process(proc, 21, parent=20)
    process(proc, 30, parent=1, pss=9999)
    roots = {10: ("app_playwright", None), 20: ("chromium", "dashboard")}
    first = sampler.sample(roots)
    assert all(c["cpu_percent"] is None for c in first["components"])
    now[0] = 5
    process(proc, 10, ticks=110)
    process(proc, 20, parent=10, ticks=160)
    process(proc, 21, parent=20, ticks=190)
    result = sampler.sample(roots)
    app, chrome = result["components"]
    assert app["cpu_percent"] == 2
    assert chrome["cpu_percent"] == 30
    assert chrome["processes"] == 2
    assert chrome["memory_rss_bytes"] == 20 * 4096
    assert chrome["memory_pss_bytes"] == 10 * 1024
    assert sum(c["processes"] for c in result["components"]) == 3


def test_pid_reuse_missing_pss_and_process_exit(counters):
    proc, _, now, sampler = counters
    directory = process(proc, 10, pss=None)
    roots = {10: ("ffmpeg", "a")}
    assert sampler.sample(roots)["components"][0]["memory_pss_bytes"] is None
    now[0] = 5
    process(proc, 10, started=200, ticks=9000, pss=None)
    assert sampler.sample(roots)["components"][0]["cpu_percent"] is None
    now[0] = 10
    process(proc, 10, started=200, ticks=9500, pss=None)
    assert sampler.sample(roots)["components"][0]["cpu_percent"] == 100
    (directory / "stat").unlink()
    with pytest.raises(OSError):
        sampler.sample(roots)


def test_cgroup_cpu_scale_and_working_set(counters):
    proc, cgroup, now, sampler = counters
    process(proc, 10)
    (cgroup / "cpu.stat").write_text("usage_usec 1000000\n")
    (cgroup / "memory.current").write_text("104857600")
    (cgroup / "memory.stat").write_text("inactive_file 10485760\n")
    (cgroup / "memory.max").write_text("max")
    roots = {10: ("app_playwright", None)}
    assert sampler.sample(roots)["container"]["cpu_percent"] is None
    now[0] = 5
    (cgroup / "cpu.stat").write_text("usage_usec 11000000\n")
    result = sampler.sample(roots)["container"]
    assert result["cpu_percent"] == 200  # Two logical CPUs fully occupied.
    assert result["cpu_capacity_percent"] == 50  # Half this four-CPU container's capacity.
    assert result["memory_working_set_bytes"] == 90 * 1048576
    assert result["memory_limit_bytes"] is None
    now[0] = 10
    (cgroup / "cpu.stat").write_text("usage_usec 1\n")
    assert sampler.sample(roots)["container"]["cpu_percent"] is None


def test_fractional_cpu_quota(counters):
    proc, cgroup, _, _ = counters
    (cgroup / "cpu.max").write_text("50000 100000")
    assert ResourceSampler(proc, cgroup).cpu_capacity == 0.5


def test_parser_handles_parentheses_and_cycles(counters):
    proc, _, _, _ = counters
    path = process(proc, 10, parent=20)
    first = read_process(path, 4096)
    second = read_process(process(proc, 20, parent=10), 4096)
    assert first.ticks == 100 and first.parent == 20
    assert owner(10, {10: first, 20: second}, {}) is None


def test_missing_and_corrupt_cgroup_is_unavailable_not_zero(counters):
    proc, cgroup, _, sampler = counters
    process(proc, 10)
    (cgroup / "cpu.stat").write_text("not a counter")
    result = sampler.sample({10: ("app_playwright", None)})
    assert result["container"]["cpu_percent"] is None
    assert result["container"]["memory_current_bytes"] is None


def manager():
    return SimpleNamespace(
        workers={}, mediamtx=SimpleNamespace(process=None),
        config={"streams": [{"name": "private", "url": "https://private.invalid/secret", "auth": {"token": "SECRET"},
                             "enabled": False, "width": 1280, "height": 720, "fps": 10, "bitrate_kbps": 1500}]},
    )


def test_roots_use_running_owned_processes_and_stream_health_is_sanitized():
    runtime = manager()
    runtime.mediamtx.process = SimpleNamespace(pid=20, returncode=None)
    runtime.workers["private"] = SimpleNamespace(
        chrome=SimpleNamespace(pid=21, returncode=None), ffmpeg=SimpleNamespace(pid=22, returncode=None),
        xvfb=SimpleNamespace(pid=23, returncode=1), state="error", error="SECRET details",
        last_frame_check="2026-09-03T10:00:00Z", restarts=2,
    )
    service = Diagnostics(runtime)
    roots = service.roots()
    assert roots[20] == ("mediamtx", None)
    assert roots[21] == ("chromium", "private")
    assert roots[22] == ("ffmpeg", "private")
    assert 23 not in roots
    result = service.snapshot()
    assert result["streams"][0]["consecutive_failures"] == 2
    assert result["streams"][0]["has_error"] is True
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_history_bounded_snapshot_detached_and_failure_redacted(counters):
    proc, _, now, sampler = counters
    process(proc, os.getpid())
    service = Diagnostics(manager(), sampler)
    for i in range(65):
        now[0] = i * 5
        await service.collect()
    result = service.snapshot()
    assert len(result["history"]) == 60
    assert result["status"] == "ok" and result["stale"] is False
    assert "SECRET" not in json.dumps(result)
    assert "private.invalid" not in json.dumps(result)
    result["latest"]["components"].clear()
    assert service.snapshot()["latest"]["components"]
    service.sampler.sample = lambda _: (_ for _ in ()).throw(RuntimeError("SECRET exception"))
    await service.collect()
    result = service.snapshot()
    assert result["status"] == "unavailable" and result["stale"]
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_service_lifecycle_and_warmup(counters):
    proc, _, _, sampler = counters
    process(proc, os.getpid())
    service = Diagnostics(manager(), sampler)
    assert service.snapshot()["status"] == "warming_up"
    await service.collect()
    assert service.snapshot()["status"] == "warming_up"
    await service.start()
    first_task = service.task
    await service.start()
    assert service.task is first_task
    await service.stop()
    assert service.task is None


@pytest.mark.asyncio
async def test_api_cached_and_does_not_include_runtime_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(RuntimeManager, "start", AsyncMock())
    monkeypatch.setattr(RuntimeManager, "stop", AsyncMock())
    monkeypatch.setattr(Diagnostics, "start", AsyncMock())
    app = create_app(tmp_path / "config.json", tmp_path / "runtime")
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics?download=1")
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "attachment" in response.headers["Content-Disposition"]
        data = await response.json()
        assert data["latest"] is None and data["schema_version"] == 1
        assert "rtsp" not in data and "log_tail" not in data
        response = await client.get("/static/diagnostics.js")
        assert response.status == 200
