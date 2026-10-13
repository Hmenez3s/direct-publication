from __future__ import annotations

import base64
import json
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from demo.render_intro import (
    RenderError,
    frame_schedule,
    inspect_source,
    main,
    normalize_screenshot,
    render,
    validate_limits,
)


def valid_svg() -> str:
    scenes = "".join(
        f'<g id="scene-{number}" data-start="{start}" data-end="{start + 8}" />'
        for number, start in enumerate(range(0, 56, 8), 1)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-duration="56"><style>.scene { fill: url(#paint) }</style>'
        '<defs><linearGradient id="paint" /></defs>' + scenes + "</svg>"
    )


def test_inspect_source_accepts_expected_contract() -> None:
    assert inspect_source(valid_svg()) == (1000, 600, 56)


@pytest.mark.parametrize(
    "case, message",
    [
        ("viewBox", "viewBox"),
        ("duration", "data-duration"),
        ("script", "script"),
        ("event", "event handlers"),
        ("foreignObject", "foreignobject"),
        ("scene", "scene-1"),
        ("href", "external SVG resource"),
        ("url", "CSS/SVG url()"),
    ],
)
def test_inspect_source_rejects_unsafe_or_inconsistent_svg(case: str, message: str) -> None:
    source = valid_svg()
    if case == "viewBox":
        source = source.replace('viewBox="0 0 1000 600"', 'viewBox="0 0 1000 520"')
    elif case == "duration":
        source = source.replace('data-duration="56"', 'data-duration="55"')
    elif case == "script":
        source = source.replace("</svg>", "<script /></svg>")
    elif case == "event":
        source = source.replace("<svg ", '<svg onload="alert(1)" ', 1)
    elif case == "foreignObject":
        source = source.replace("</svg>", "<foreignObject /></svg>")
    elif case == "scene":
        source = source.replace('id="scene-1" data-start="0"', 'id="scene-1" data-start="1"')
    elif case == "href":
        source = source.replace("</svg>", '<image href="https://example.com/a.png" /></svg>')
    else:
        source = source.replace("url(#paint)", "url(https://example.com/a.png)")
    with pytest.raises(ValueError, match=message):
        inspect_source(source)


@pytest.mark.parametrize(
    "prefix, message",
    [
        ("<?xml-stylesheet href='https://example.com/a.css'?>", "processing instructions"),
        ('<g xml:base="https://example.com/" />', "xml:base"),
    ],
)
def test_inspect_source_rejects_xml_external_resources(prefix: str, message: str) -> None:
    source = valid_svg().replace("</svg>", prefix + "</svg>")
    with pytest.raises(ValueError, match=message):
        inspect_source(source)


def test_inspect_source_rejects_css_import() -> None:
    source = valid_svg().replace("</style>", "@import url('https://example.com/x');</style>")
    with pytest.raises(ValueError, match="imports"):
        inspect_source(source)


def test_frame_schedule_is_chronological_and_includes_scene_midpoints() -> None:
    frames, snapshots = frame_schedule(12)
    assert frames == 672
    assert snapshots == (48, 144, 240, 336, 432, 528, 624)


@pytest.mark.parametrize("fps, workers", [(0, 1), (31, 1), (12, 0), (12, 9), (True, 1)])
def test_validate_limits_rejects_invalid_bounds(fps: int, workers: int) -> None:
    with pytest.raises(ValueError):
        validate_limits(fps, workers)


def test_cli_rejects_bounds_before_creating_output(tmp_path, capsys) -> None:
    output = tmp_path / "must-not-exist"
    assert main(["--lang", "en", "--output-root", str(output), "--fps", "31"]) == 1
    assert not output.exists()
    assert "fps must be" in capsys.readouterr().err


def test_existing_output_root_is_never_reused(tmp_path, monkeypatch) -> None:
    def must_not_start(*args):
        pytest.fail("existing output must be rejected before looking up tools")

    monkeypatch.setattr("demo.render_intro.find_chrome", must_not_start)
    source = tmp_path / "intro.svg"
    source.write_text(valid_svg(), encoding="utf-8")
    output = tmp_path / "already-there"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(FileExistsError):
        render(source, output, 12, 1)
    assert marker.read_text(encoding="utf-8") == "untouched"


def test_render_pins_screen_animation_encoding_quality(tmp_path, monkeypatch) -> None:
    def capture_frames(_chrome, work_dir, _url, indices, _fps):
        for index in indices:
            (work_dir / "frames" / f"frame-{index:06d}.png").write_bytes(b"test frame")

    commands = []

    def encode(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("demo.render_intro.find_chrome", lambda _: "/unused/chrome")
    monkeypatch.setattr("demo.render_intro.shutil.which", lambda _: "/unused/ffmpeg")
    monkeypatch.setattr("demo.render_intro._render_worker", capture_frames)
    monkeypatch.setattr("demo.render_intro.subprocess.run", encode)
    source = tmp_path / "intro.svg"
    source.write_text(valid_svg(), encoding="utf-8")
    output = tmp_path / "render"
    render(source, output, fps=1, workers=1)

    assert len(commands) == 1
    command = commands[0]
    for flag, value in (("-crf", "16"), ("-preset", "slow"), ("-tune", "animation")):
        assert flag in command
        assert command[command.index(flag) + 1] == value
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert json.loads((output / "render.json").read_text())["command"] == command


@pytest.mark.parametrize("size", [(1000, 600), (2000, 1200)])
def test_normalize_screenshot_preserves_canvas_and_color(size) -> None:
    buffer = BytesIO()
    Image.new("RGB", size, (24, 24, 24)).save(buffer, format="PNG")
    png = buffer.getvalue()
    normalized = normalize_screenshot(png)
    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (1000, 600)
        assert image.getextrema() == ((24, 24), (24, 24), (24, 24))
    if size == (1000, 600):
        assert normalized == png


def test_normalize_screenshot_rejects_clipped_view() -> None:
    buffer = BytesIO()
    Image.new("RGB", (1000, 513)).save(buffer, format="PNG")
    with pytest.raises(RenderError, match="aspect ratio"):
        normalize_screenshot(buffer.getvalue())


def test_worker_captures_native_canvas_without_virtual_resizing(tmp_path, monkeypatch) -> None:
    from demo import render_intro

    (tmp_path / "frames").mkdir()
    png = BytesIO()
    Image.new("RGB", (2000, 1200), (24, 24, 24)).save(png, format="PNG")
    calls = []
    launches = []
    process_status = [None]

    def launch(command, **_kwargs):
        launches.append(command)
        profile = Path(
            next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir="))
        )
        (profile / "DevToolsActivePort").write_text("12345\n")
        return SimpleNamespace(
            poll=lambda: process_status[0],
            terminate=lambda: process_status.__setitem__(0, 0),
            wait=lambda timeout: process_status[0],
        )

    def cdp(_ws, method, params=None):
        calls.append((method, params))
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(png.getvalue()).decode()}
        return {}

    target = json.dumps([{"type": "page", "webSocketDebuggerUrl": "ws://unused"}]).encode()
    connection = SimpleNamespace(
        request=lambda *_: None,
        getresponse=lambda: SimpleNamespace(status=200, read=lambda: target),
        close=lambda: None,
    )
    monkeypatch.setattr(render_intro.subprocess, "Popen", launch)
    monkeypatch.setattr(render_intro.http.client, "HTTPConnection", lambda *a, **kw: connection)
    monkeypatch.setattr(render_intro, "connect", lambda *a, **kw: nullcontext(object()))
    monkeypatch.setattr(render_intro, "_cdp", cdp)
    monkeypatch.setattr(render_intro, "_evaluate", lambda *_: True)
    render_intro._render_worker("/unused/chrome", tmp_path, "file:///unused", [0], 12)

    assert "--window-size=1200,800" in launches[0]
    assert not any(method == "Emulation.setDeviceMetricsOverride" for method, _ in calls)
    params = next(params for method, params in calls if method == "Page.captureScreenshot")
    assert params["captureBeyondViewport"] is False
    assert params["clip"] == {"x": 0, "y": 0, "width": 1000, "height": 600, "scale": 1}
    with Image.open(tmp_path / "frames/frame-000000.png") as frame:
        assert frame.size == (1000, 600)
    assert process_status == [0]


def test_intro_sources_are_one_continuous_story() -> None:
    import xml.etree.ElementTree as ET

    expected = {
        "docs/intro.svg": ("Brazil", "offline", "bulletin"),
        "docs/intro.pt-BR.svg": ("brasileira", "offline", "boletim"),
    }
    for name, comparison_terms in expected.items():
        source = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
        inspect_source(source)
        assert "pg-dot" not in source and "pg-bar" not in source
        root = ET.fromstring(source)
        parent = {child: element for element in root.iter() for child in list(element)}
        headlines = [
            "".join(element.itertext())
            for element in root.iter()
            if "h" in (element.get("class") or "").split()
        ]
        assert len(headlines) == 7
        assert all(
            "?" not in line and not line.startswith(("Why ", "Por que")) for line in headlines
        )
        assert all(term in source for term in comparison_terms)
        receipt = next(element for element in root.iter() if element.get("id") == "receipt")
        while receipt in parent:
            receipt = parent[receipt]
            assert not (receipt.get("id") or "").startswith("scene-")
        for scene in root.iter():
            if (scene.get("id") or "").startswith("scene-"):
                # Timing groups may change the words, never hide/reset the visual story.
                assert all(
                    element.tag.rsplit("}", 1)[-1] in {"g", "text", "tspan", "title", "desc"}
                    for element in scene.iter()
                )
