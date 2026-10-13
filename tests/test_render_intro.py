from __future__ import annotations

import pytest

from demo.render_intro import frame_schedule, inspect_source, main, render, validate_limits


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
