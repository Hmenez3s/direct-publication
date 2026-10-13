"""Render the explanatory SVG intro as a deterministic, seekable MP4."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DURATION = 56
WIDTH, HEIGHT = 1000, 600
MAX_FPS, MAX_WORKERS = 30, 8
SCENE_STARTS = tuple(range(0, DURATION, 8))


class RenderError(RuntimeError):
    """A source, browser, or encoder operation failed."""


def inspect_source(svg: str) -> tuple[int, int, int]:
    """Validate the expected source contract and return width, height, duration."""
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", svg, re.I):
        raise ValueError("SVG DTDs and entities are not allowed")
    if re.search(r"<\?(?!xml\s)", svg, re.I):
        raise ValueError("SVG processing instructions are not allowed")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError(f"invalid SVG XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg" or root.get("viewBox") != "0 0 1000 600":
        raise ValueError("SVG must have viewBox='0 0 1000 600'")
    if root.get("data-duration") != str(DURATION):
        raise ValueError(f"SVG must have data-duration='{DURATION}'")

    scene_attrs: dict[str, dict[str, str | None]] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise ValueError(f"unsafe SVG element: {tag}")
        values = list(element.attrib.values())
        if element.text:
            values.append(element.text)
        if element.tail:
            values.append(element.tail)
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on"):
                raise ValueError("SVG event handlers are not allowed")
            if name == "base":
                raise ValueError("SVG xml:base is not allowed")
            if name in {"href", "src"} and not value.startswith("#"):
                raise ValueError(f"external SVG resource is not allowed: {value}")
        for value in values:
            if re.search(r"@import\b", value, re.I):
                raise ValueError("CSS imports are not allowed")
            for match in re.finditer(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", value, re.I):
                if not match.group(2).strip().startswith("#"):
                    raise ValueError("external CSS/SVG url() references are not allowed")
        if tag == "g" and (element.get("id") or "").startswith("scene-"):
            scene_id = element.get("id", "")
            if scene_id in scene_attrs:
                raise ValueError(f"duplicate SVG scene id: {scene_id}")
            scene_attrs[scene_id] = {
                "start": element.get("data-start"),
                "end": element.get("data-end"),
            }
    expected = {
        f"scene-{i + 1}": {"start": str(start), "end": str(start + 8)}
        for i, start in enumerate(SCENE_STARTS)
    }
    if scene_attrs != expected:
        raise ValueError(
            "SVG must contain scene-1..scene-7 with contiguous 8-second data-start/end"
        )
    return WIDTH, HEIGHT, DURATION


def frame_schedule(fps: int, duration: int = DURATION) -> tuple[int, tuple[int, ...]]:
    """Return frame count and the scene snapshot frame indices."""
    total = fps * duration
    snapshots = tuple(round((start + 4) * fps) for start in SCENE_STARTS)
    return total, snapshots


def validate_limits(fps: int, workers: int) -> None:
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= MAX_FPS:
        raise ValueError(f"fps must be an integer from 1 to {MAX_FPS}")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be an integer from 1 to {MAX_WORKERS}")


def find_chrome(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        raise FileNotFoundError(f"Chrome executable not found or not executable: {explicit}")
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Chrome not found; install Chrome/Chromium or pass --chrome PATH")


def _cdp(ws: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = _cdp.next_id
    _cdp.next_id += 1
    ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        message = json.loads(ws.recv(timeout=30))
        if message.get("id") == request_id:
            if "error" in message:
                raise RenderError(f"Chrome DevTools {method}: {message['error']}")
            return message.get("result", {})


_cdp.next_id = 1


def _evaluate(ws: Any, expression: str) -> Any:
    result = _cdp(
        ws,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    if result.get("exceptionDetails"):
        raise RenderError(f"browser evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def _render_worker(
    chrome: str, work_dir: Path, html_url: str, frame_indices: range | list[int], fps: int
) -> None:
    profile = work_dir / f"profile-{threading.current_thread().name}"
    profile.mkdir()
    command = [
        chrome,
        "--headless=new",
        "--no-first-run",
        "--disable-background-networking",
        "--disable-extensions",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-component-update",
        "--disable-features=MediaRouter",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "about:blank",
    ]
    chrome_log = profile / "chrome.log"
    log_stream = chrome_log.open("w", encoding="utf-8")
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log_stream, text=True)
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while not port_file.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if not port_file.exists():
            error = chrome_log.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RenderError(f"Chrome failed to start: {error or 'startup timed out'}")
        port = int(port_file.read_text().splitlines()[0])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/json/list")
            response = connection.getresponse()
            if response.status != 200:
                raise RenderError(f"Chrome DevTools target list returned HTTP {response.status}")
            targets = json.loads(response.read())
        finally:
            connection.close()
        target = next((t for t in targets if t.get("type") == "page"), None)
        if target is None:
            raise RenderError("Chrome did not expose a page target")
        with connect(
            target["webSocketDebuggerUrl"],
            proxy=None,
            open_timeout=10,
        ) as ws:
            _cdp(ws, "Page.enable")
            _cdp(ws, "Runtime.enable")
            _cdp(ws, "Network.enable")
            _cdp(ws, "Network.setBlockedURLs", {"urls": ["http://*", "https://*"]})
            _cdp(
                ws,
                "Emulation.setDeviceMetricsOverride",
                {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False},
            )
            _cdp(
                ws,
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]},
            )
            _cdp(ws, "Page.navigate", {"url": html_url})
            ready = (
                "document.readyState === 'complete' && "
                "!!document.querySelector('svg[data-duration=\"56\"]')"
            )
            until = time.monotonic() + 15
            while not _evaluate(ws, ready):
                if time.monotonic() >= until:
                    raise RenderError("intro SVG did not finish loading")
                time.sleep(0.05)
            _evaluate(ws, "document.getAnimations().forEach(a => a.pause()); true")
            for index in frame_indices:
                ms = index * 1000 / fps
                _evaluate(
                    ws,
                    "(t => { const a=document.getAnimations(); "
                    "a.forEach(x => {x.pause(); x.currentTime=t}); "
                    "return new Promise(r => requestAnimationFrame(() => "
                    "requestAnimationFrame(() => r(true)))) })(" + str(ms) + ")",
                )
                shot = _cdp(ws, "Page.captureScreenshot", {"format": "png", "fromSurface": True})
                import base64

                (work_dir / "frames" / f"frame-{index:06d}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log_stream.close()
        if proc.poll() is None:
            raise RenderError("owned Chrome process could not be stopped")


def render(
    svg_path: Path, output_root: Path, fps: int, workers: int, chrome_path: str | None = None
) -> None:
    validate_limits(fps, workers)
    output_root = output_root.expanduser()
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"refusing to reuse render output: {output_root}")
    svg_path = svg_path.resolve()
    source_bytes = svg_path.read_bytes()
    source = source_bytes.decode("utf-8")
    width, height, duration = inspect_source(source)
    chrome = find_chrome(chrome_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg not found on PATH")
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    work_dir = output_root
    (work_dir / "frames").mkdir()
    html_path = work_dir / "intro.html"
    html_path.write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><style>'
        f"html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}"
        f"svg{{display:block;width:{width}px;height:{height}px}}</style></head>"
        f"<body>{source}</body></html>",
        encoding="utf-8",
    )
    frame_count, snapshot_indices = frame_schedule(fps, duration)
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="renderer"
        ) as pool:
            futures = [
                pool.submit(
                    _render_worker,
                    chrome,
                    work_dir,
                    html_path.as_uri(),
                    list(range(worker, frame_count, workers)),
                    fps,
                )
                for worker in range(workers)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        ffmpeg_cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(work_dir / "frames/frame-%06d.png"),
            "-frames:v",
            str(frame_count),
            "-threads",
            str(workers),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(work_dir / "intro.mp4"),
        ]
        completed = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
        if completed.returncode:
            raise RenderError(f"ffmpeg failed ({completed.returncode}): {completed.stderr[-4000:]}")
        for scene_no, index in enumerate(snapshot_indices, 1):
            shutil.copyfile(
                work_dir / "frames" / f"frame-{index:06d}.png", work_dir / f"scene-{scene_no}.png"
            )
        shutil.copyfile(work_dir / "scene-1.png", work_dir / "poster.png")
        metadata = {
            "status": "finished",
            "source": svg_path.name,
            "sourceSHA256": hashlib.sha256(source_bytes).hexdigest(),
            "size": [width, height],
            "width": width,
            "height": height,
            "duration": duration,
            "fps": fps,
            "frames": frame_count,
            "workers": workers,
            "chrome": chrome,
            "ffmpeg": ffmpeg,
            "command": ffmpeg_cmd,
        }
        metadata_temp = work_dir / "render.json.tmp"
        metadata_temp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        metadata_temp.replace(work_dir / "render.json")
    except BaseException:
        (work_dir / "render.json").unlink(missing_ok=True)
        raise


def _default_workers() -> int:
    return max(1, min(MAX_WORKERS, math.floor((os.cpu_count() or 1) * 0.8)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=("en", "pt-BR"), required=True)
    parser.add_argument(
        "--output-root", type=Path, required=True, help="new, empty output directory"
    )
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--workers", type=int, default=_default_workers())
    parser.add_argument("--chrome", help="Chrome/Chromium executable path")
    args = parser.parse_args(argv)
    try:
        render(
            PROJECT_ROOT / "docs" / ("intro.svg" if args.lang == "en" else "intro.pt-BR.svg"),
            args.output_root,
            args.fps,
            args.workers,
            args.chrome,
        )
    except Exception as exc:
        print(f"render_intro: {exc}", file=sys.stderr)
        return 1
    print(f"Rendered {args.lang}: {args.output_root.resolve() / 'intro.mp4'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
