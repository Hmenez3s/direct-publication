from __future__ import annotations

from io import StringIO

import pytest

from demo.screen import Screen


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_screen_titles_and_sections_are_localized(lang: str) -> None:
    stream = StringIO()
    screen = Screen(lang, file=stream)
    screen.title()
    screen.section("voting")
    rendered = stream.getvalue()
    assert screen.text["subtitle"] in rendered
    assert screen.text["voting"] in rendered
    assert screen.text["voting_note"] in rendered
    screen.receipt(1, "fixture-code")
    screen.decision("CONFIRM")
    assert "fixture-code" in stream.getvalue()
    assert "CONFIRM" in stream.getvalue()


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_zero_tally_and_literal_candidate_markup(lang: str) -> None:
    stream = StringIO()
    screen = Screen(lang, file=stream)
    candidate = "[bold red]literal candidate[/bold red]"
    screen.board([candidate])
    screen.results([candidate], [0])
    rendered = stream.getvalue()
    assert candidate in rendered
    assert "0 " in rendered
    assert "[bold red]literal candidate[/bold red]" in rendered


def test_working_context_does_not_pollute_non_tty_output() -> None:
    stream = StringIO()
    screen = Screen(file=stream)
    with screen.working("checking"):
        pass
    assert stream.getvalue() == ""
