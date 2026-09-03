"""Supervise MediaMTX and webpage-to-RTSP stream workers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .auth import apply_auth

LOGGER = logging.getLogger(__name__)

# The supported NVR workload is deliberately biased toward low encoder cost.
# At 1280x720/10 FPS, one superfast x264 worker sustained the target rate under
# a high-animation stress page while materially reducing CPU and memory use.
X264_PRESET = "superfast"
X264_THREADS = "1"


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _wait_port(port: int, timeout: float = 15) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.25)
    raise RuntimeError(f"port {port} did not become ready")


def mediamtx_config(config: dict) -> dict:
    rtsp = config["rtsp"]
    return {
        "logLevel": os.getenv("MEDIAMTX_LOG_LEVEL", "warn"),
        "logDestinations": ["stdout"],
        "readTimeout": "10s",
        "writeTimeout": "10s",
        "authMethod": "internal",
        "authInternalUsers": [
            {
                "user": "web2rtsp-publisher",
                "pass": rtsp["publisher_password"],
                "ips": ["127.0.0.1", "::1"],
                "permissions": [{"action": "publish"}],
            },
            {
                "user": rtsp["username"],
                "pass": rtsp["password"],
                "ips": [],
                "permissions": [{"action": "read"}],
            },
        ],
        "api": True,
        "apiAddress": "127.0.0.1:9997",
        "metrics": False,
        "playback": False,
        "rtsp": True,
        "rtspAddress": f":{rtsp['port']}",
        "rtspTransports": ["tcp"],
        "rtmp": False,
        "hls": True,
        "hlsAddress": f":{rtsp['hls_port']}",
        "hlsAllowOrigins": ["*"],
        "webrtc": False,
        "srt": False,
        "pathDefaults": {"source": "publisher"},
        "paths": {"all_others": {}},
    }


class MediaMTX:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.process: asyncio.subprocess.Process | None = None
        self.log_tail: deque[str] = deque(maxlen=40)
        self._reader_task: asyncio.Task | None = None

    async def start(self, config: dict) -> None:
        await self.stop()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path = self.runtime_dir / "mediamtx.json"
        path.write_text(json.dumps(mediamtx_config(config), indent=2), encoding="utf-8")
        path.chmod(0o600)
        self.process = await asyncio.create_subprocess_exec(
            os.getenv("MEDIAMTX_PATH", "/usr/local/bin/mediamtx"),
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader_task = asyncio.create_task(self._read_output())
        await _wait_port(config["rtsp"]["port"])

    async def _read_output(self) -> None:
        assert self.process and self.process.stdout
        async for raw in self.process.stdout:
            line = raw.decode(errors="replace").rstrip()
            self.log_tail.append(line)
            LOGGER.debug("MediaMTX: %s", line)

    async def stop(self) -> None:
        await _terminate(self.process)
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self.process = None
        self._reader_task = None

    def status(self) -> dict:
        return {
            "running": bool(self.process and self.process.returncode is None),
            "pid": self.process.pid if self.process and self.process.returncode is None else None,
            "log_tail": list(self.log_tail)[-10:],
        }


class StreamWorker:
    def __init__(self, manager: RuntimeManager, stream: dict, display: int) -> None:
        self.manager = manager
        self.stream = stream
        self.display = display
        self.task: asyncio.Task | None = None
        self.xvfb: asyncio.subprocess.Process | None = None
        self.chrome: asyncio.subprocess.Process | None = None
        self.ffmpeg: asyncio.subprocess.Process | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.state = "stopped"
        self.error: str | None = None
        self.started_at: str | None = None
        self.last_frame_check: str | None = None
        self.restarts = 0
        self.log_tail: deque[str] = deque(maxlen=30)
        self._stopping = False

    def start(self) -> None:
        self.task = asyncio.create_task(self._supervise(), name=f"stream-{self.stream['name']}")

    async def stop(self) -> None:
        self._stopping = True
        if self.task:
            self.task.cancel()
        await self._cleanup()
        if self.task:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.task = None
        self.state = "stopped"

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                await self._run_once()
                if not self._stopping:
                    raise RuntimeError("FFmpeg exited unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                self.state = "error"
                self.restarts += 1
                LOGGER.exception("stream %s failed; retrying", self.stream["name"])
                await self._cleanup()
                await asyncio.sleep(min(30, 3 * self.restarts))

    async def _run_once(self) -> None:
        self.state = "starting"
        self.error = None
        width, height, fps = self.stream["width"], self.stream["height"], self.stream["fps"]
        display_name = f":{self.display}"
        self.xvfb = await asyncio.create_subprocess_exec(
            "Xvfb", display_name, "-screen", "0", f"{width}x{height}x24", "-ac", "-noreset",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        await self._wait_display(display_name)

        browser_env = dict(os.environ)
        browser_env["DISPLAY"] = display_name
        profile_dir = self.manager.runtime_dir / f"chromium-{self.stream['name']}"
        shutil.rmtree(profile_dir, ignore_errors=True)
        debug_port = 9222 + (self.display - 100)
        self.chrome = await asyncio.create_subprocess_exec(
            os.getenv("CHROMIUM_PATH", "/usr/bin/chromium"),
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--test-type",
            "--app=about:blank", "--kiosk", "--start-fullscreen",
            "--hide-scrollbars", "--no-first-run",
            "--disable-session-crashed-bubble", "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding", "--autoplay-policy=no-user-gesture-required",
            f"--window-size={width},{height}", "--window-position=0,0",
            "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile_dir}",
            env=browser_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await _wait_port(debug_port)
        self.browser = await self.manager.playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{debug_port}"
        )
        if not self.browser.contexts:
            raise RuntimeError("Chromium exposed no browser context")
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await apply_auth(self.context, self.page, self.stream["auth"])
        await self.page.goto(
            self.stream["url"], wait_until="domcontentloaded", timeout=45_000
        )
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=8_000)
        await self.page.wait_for_timeout(int(self.stream.get("settle_seconds", 3)) * 1000)
        if self.stream["auth"]["strategy"] == "ha_token" and await self.page.query_selector(
            "input[name='username']"
        ):
            raise RuntimeError("Home Assistant rejected the configured token")

        publisher = quote(self.manager.config["rtsp"]["publisher_password"], safe="")
        target = (
            f"rtsp://web2rtsp-publisher:{publisher}@127.0.0.1:"
            f"{self.manager.config['rtsp']['port']}/{self.stream['name']}"
        )
        bitrate = self.stream["bitrate_kbps"]
        gop = max(fps, fps * 2)
        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-thread_queue_size", "512", "-f", "x11grab", "-draw_mouse", "0",
            "-video_size", f"{width}x{height}", "-framerate", str(fps),
            "-i", f"{display_name}+0,0", "-an", "-c:v", "libx264",
            "-preset", X264_PRESET, "-tune", "zerolatency", "-threads", X264_THREADS,
            "-pix_fmt", "yuv420p",
            "-profile:v", "baseline", "-level", "3.1", "-bf", "0",
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
            "-bufsize", f"{bitrate * 2}k", "-f", "rtsp", "-rtsp_transport", "tcp", target,
        ]
        self.ffmpeg = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        output_task = asyncio.create_task(self._read_ffmpeg())
        monitor_task = asyncio.create_task(self._monitor_page())
        self.state = "running"
        self.started_at = utcnow()
        self.restarts = 0
        try:
            await self.ffmpeg.wait()
        finally:
            for task in (output_task, monitor_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._cleanup()

    async def _wait_display(self, display_name: str) -> None:
        for _ in range(40):
            if self.xvfb and self.xvfb.returncode is not None:
                error = (await self.xvfb.stderr.read()).decode(errors="replace")
                raise RuntimeError(f"Xvfb exited: {error[-500:]}")
            process = await asyncio.create_subprocess_exec(
                "xdpyinfo", "-display", display_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() == 0:
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("Xvfb did not become ready")

    async def _read_ffmpeg(self) -> None:
        assert self.ffmpeg and self.ffmpeg.stderr
        async for raw in self.ffmpeg.stderr:
            line = raw.decode(errors="replace").rstrip()
            self.log_tail.append(line)
            LOGGER.debug("FFmpeg %s: %s", self.stream["name"], line)

    async def _monitor_page(self) -> None:
        reload_seconds = self.stream["reload_seconds"]
        last_reload = asyncio.get_running_loop().time()
        failures = 0
        while True:
            await asyncio.sleep(10)
            try:
                assert self.page
                await self.page.evaluate("() => document.readyState")
                self.last_frame_check = utcnow()
                failures = 0
                if reload_seconds and asyncio.get_running_loop().time() - last_reload >= reload_seconds:
                    await self.page.reload(wait_until="domcontentloaded", timeout=45_000)
                    last_reload = asyncio.get_running_loop().time()
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures >= 3:
                    self.error = f"browser health check failed: {exc}"
                    await _terminate(self.ffmpeg)
                    return

    async def snapshot(self) -> bytes:
        if not self.page or self.state != "running":
            raise RuntimeError("stream is not running")
        return await self.page.screenshot(type="jpeg", quality=80)

    async def _cleanup(self) -> None:
        await _terminate(self.ffmpeg)
        self.ffmpeg = None
        if self.browser:
            with contextlib.suppress(Exception):
                await self.browser.close()
        self.browser = None
        self.context = None
        await _terminate(self.chrome)
        self.chrome = None
        self.page = None
        await _terminate(self.xvfb)
        self.xvfb = None

    def status(self, host: str) -> dict:
        port = self.manager.config["rtsp"]["port"]
        return {
            "name": self.stream["name"], "url": self.stream["url"], "state": self.state,
            "error": self.error, "started_at": self.started_at,
            "last_frame_check": self.last_frame_check, "restarts": self.restarts,
            "rtsp_url": f"rtsp://{host}:{port}/{self.stream['name']}",
            "log_tail": list(self.log_tail)[-8:],
        }


class RuntimeManager:
    def __init__(self, config: dict, runtime_dir: Path) -> None:
        self.config = config
        self.runtime_dir = runtime_dir
        self.mediamtx = MediaMTX(runtime_dir)
        self.workers: dict[str, StreamWorker] = {}
        self._playwright: Playwright | None = None
        self._lock = asyncio.Lock()

    @property
    def playwright(self) -> Playwright:
        if not self._playwright:
            raise RuntimeError("Playwright has not started")
        return self._playwright

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        await self.apply_config(self.config)

    async def apply_config(self, config: dict) -> None:
        async with self._lock:
            await self._stop_workers()
            self.config = config
            await self.mediamtx.start(config)
            for index, stream in enumerate(config["streams"]):
                if stream["enabled"]:
                    worker = StreamWorker(self, stream, 100 + index)
                    self.workers[stream["name"]] = worker
                    worker.start()

    async def restart_stream(self, name: str) -> None:
        stream = next((s for s in self.config["streams"] if s["name"] == name), None)
        if not stream or not stream["enabled"]:
            raise KeyError(name)
        old = self.workers.pop(name, None)
        if old:
            await old.stop()
        display = 100 + self.config["streams"].index(stream)
        worker = StreamWorker(self, stream, display)
        self.workers[name] = worker
        worker.start()

    async def _stop_workers(self) -> None:
        await asyncio.gather(*(w.stop() for w in self.workers.values()), return_exceptions=True)
        self.workers.clear()

    async def stop(self) -> None:
        await self._stop_workers()
        await self.mediamtx.stop()
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    def status(self, host: str) -> dict:
        configured = {s["name"] for s in self.config["streams"]}
        statuses = [worker.status(host) for worker in self.workers.values()]
        for stream in self.config["streams"]:
            if stream["name"] not in self.workers:
                statuses.append({
                    "name": stream["name"], "url": stream["url"], "state": "disabled",
                    "error": None, "started_at": None, "last_frame_check": None,
                    "restarts": 0,
                    "rtsp_url": f"rtsp://{host}:{self.config['rtsp']['port']}/{stream['name']}",
                    "log_tail": [],
                })
        return {"mediamtx": self.mediamtx.status(), "streams": statuses, "configured": len(configured)}
