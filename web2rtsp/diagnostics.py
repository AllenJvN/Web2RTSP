"""Cached, container-local resource diagnostics. Never read process arguments or secrets."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    parent: int
    started: int
    ticks: int
    rss: int


def read_process(path: Path, page_size: int) -> ProcessSample:
    # comm can contain spaces and parentheses; everything after the final ')' is numeric/state.
    prefix, tail = (path / "stat").read_text().rsplit(")", 1)
    fields = tail.split()
    return ProcessSample(
        pid=int(prefix.split("(", 1)[0]), parent=int(fields[1]), started=int(fields[19]),
        ticks=int(fields[11]) + int(fields[12]), rss=max(0, int(fields[21])) * page_size,
    )


def read_pss(path: Path) -> int | None:
    try:
        for line in (path / "smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None  # Unavailable is not zero; protected Chromium processes can deny this read.


def owner(pid: int, processes: dict, roots: dict) -> tuple | None:
    seen = set()
    while pid in processes and pid not in seen:
        if pid in roots:
            return roots[pid]
        seen.add(pid)
        pid = processes[pid].parent
    return None


def read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def read_pairs(path: Path) -> dict:
    try:
        return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}
    except (OSError, ValueError):
        return {}


class ResourceSampler:
    """Sample owned process trees only, independent of the number of API clients."""

    def __init__(self, proc: Path = Path("/proc"), cgroup: Path = Path("/sys/fs/cgroup"),
                 clock=time.monotonic, ticks_per_second: int | None = None,
                 page_size: int | None = None, cpu_capacity: float | None = None):
        self.proc, self.cgroup, self.clock = proc, cgroup, clock
        self.ticks_per_second = ticks_per_second or (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)
        self.page_size = page_size or (os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096)
        self.cpu_capacity = cpu_capacity or self._capacity()
        self.previous: dict = {}
        self.previous_time: float | None = None
        self.previous_usage: int | None = None

    def _capacity(self) -> float:
        available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
        try:
            quota, period = (self.cgroup / "cpu.max").read_text().split()
            if quota != "max" and int(period) > 0:
                return max(0.001, min(float(available), int(quota) / int(period)))
        except (OSError, ValueError):
            pass
        return float(available)

    def sample(self, roots: dict[int, tuple[str, str | None]]) -> dict:
        began = self.clock()
        elapsed = began - self.previous_time if self.previous_time is not None else None
        processes = {}
        for path in self.proc.iterdir():
            if path.name.isdigit():
                try:
                    process = read_process(path, self.page_size)
                    processes[process.pid] = process
                except (OSError, ValueError, IndexError):
                    continue  # A process can exit at any point in this scan.
        if not any(pid in processes for pid in roots):
            raise OSError("owned process counters unavailable")

        groups = {}
        current = {}
        for process in processes.values():
            group = owner(process.pid, processes, roots)
            if group is None:
                continue
            bucket = groups.setdefault(group, {"count": 0, "ticks": 0, "rss": 0, "pss": 0, "cpu_known": True, "pss_known": True})
            bucket["count"] += 1
            bucket["rss"] += process.rss
            pss = read_pss(self.proc / str(process.pid))
            bucket["pss_known"] &= pss is not None
            bucket["pss"] += pss or 0
            identity = (process.pid, process.started)
            old = self.previous.get(identity)
            if old is None or old[0] != group or process.ticks < old[1]:
                bucket["cpu_known"] = False
            else:
                bucket["ticks"] += process.ticks - old[1]
            current[identity] = (group, process.ticks)

        components = []
        for (component, stream), bucket in sorted(groups.items(), key=lambda item: str(item[0])):
            cpu = (100 * bucket["ticks"] / self.ticks_per_second / elapsed
                   if elapsed and elapsed > 0 and bucket["cpu_known"] else None)
            components.append({
                "component": component, "stream": stream, "processes": bucket["count"],
                "cpu_percent": round(cpu, 2) if cpu is not None else None,
                "memory_rss_bytes": bucket["rss"],
                "memory_pss_bytes": bucket["pss"] if bucket["pss_known"] else None,
            })

        usage = read_pairs(self.cgroup / "cpu.stat").get("usage_usec")
        container_cpu = None
        if usage is not None and self.previous_usage is not None and elapsed and elapsed > 0 and usage >= self.previous_usage:
            container_cpu = (usage - self.previous_usage) / 1_000_000 / elapsed * 100
        memory = read_int(self.cgroup / "memory.current")
        inactive = read_pairs(self.cgroup / "memory.stat").get("inactive_file")
        container = {
            "cpu_percent": round(container_cpu, 2) if container_cpu is not None else None,
            "cpu_capacity_percent": round(container_cpu / self.cpu_capacity, 2) if container_cpu is not None else None,
            "memory_current_bytes": memory,
            "memory_working_set_bytes": max(0, memory - inactive) if memory is not None and inactive is not None else None,
            "memory_limit_bytes": read_int(self.cgroup / "memory.max"),
        }
        self.previous, self.previous_time, self.previous_usage = current, began, usage
        return {
            "sampled_at": datetime.now(UTC).isoformat(), "interval_seconds": round(elapsed, 3) if elapsed else None,
            "cpu_capacity_cores": self.cpu_capacity, "components": components, "container": container,
            "sample_cost_ms": round((self.clock() - began) * 1000, 2),
        }


class Diagnostics:
    def __init__(self, manager, sampler: ResourceSampler | None = None, interval: float = 5):
        self.manager = manager
        self.sampler = sampler or ResourceSampler()
        self.interval = interval
        self.history: deque = deque(maxlen=60)
        self.latest: dict | None = None
        self.sampled_monotonic: float | None = None
        self.failed = False
        self.task: asyncio.Task | None = None

    def roots(self) -> dict:
        result = {os.getpid(): ("app_playwright", None)}
        media = self.manager.mediamtx.process
        if media and media.returncode is None:
            result[media.pid] = ("mediamtx", None)
        for name, worker in list(self.manager.workers.items()):
            for attr, component in (("chrome", "chromium"), ("ffmpeg", "ffmpeg"), ("xvfb", "xvfb")):
                process = getattr(worker, attr)
                if process and process.returncode is None:
                    result[process.pid] = (component, name)
        return result

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="resource-diagnostics")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def _loop(self) -> None:
        while True:
            await self.collect()
            await asyncio.sleep(self.interval)

    async def collect(self) -> None:
        try:
            # Kernel memory walks are off the event loop; API polls only read cached samples.
            sample = await asyncio.to_thread(self.sampler.sample, self.roots())
            self.latest = sample
            self.sampled_monotonic = time.monotonic()
            self.history.append(copy.deepcopy(sample))
            self.failed = False
        except Exception:  # Diagnostics must never take down the stream or leak exception contents.
            self.failed = True

    def snapshot(self) -> dict:
        age = time.monotonic() - self.sampled_monotonic if self.sampled_monotonic is not None else None
        stale = self.failed or age is None or age > self.interval * 3
        streams = [{
            "name": config["name"], "enabled": config["enabled"],
            "width": config["width"], "height": config["height"],
            "configured_fps": config["fps"], "configured_bitrate_kbps": config["bitrate_kbps"],
            "state": worker.state if (worker := self.manager.workers.get(config["name"])) else "disabled",
            "last_browser_check": worker.last_frame_check if worker else None,
            "consecutive_failures": worker.restarts if worker else 0,
            "has_error": bool(worker.error) if worker else False,
        } for config in self.manager.config["streams"]]
        return {
            "schema_version": 1, "app_version": __version__, "sample_interval_seconds": self.interval,
            "status": "unavailable" if self.failed else "warming_up" if self.latest is None or self.latest["interval_seconds"] is None else "ok",
            "stale": stale, "sample_age_seconds": round(age, 2) if age is not None else None,
            "cpu_unit": "100% = one logical CPU; can exceed 100%",
            "memory_note": "PSS apportions shared pages; summed RSS double-counts them. Container memory is separate accounting.",
            "latest": copy.deepcopy(self.latest), "history": list(copy.deepcopy(self.history)),
            "streams": streams,
        }
