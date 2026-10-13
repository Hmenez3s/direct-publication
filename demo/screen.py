"""Readable terminal view of the demo election, in English or Brazilian Portuguese."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "subtitle": "a local election, live on this computer",
        "software": "simulated machine image matches the approved source release",
        "key": "election key split among 5 trustees; 3 are needed to open the result",
        "board": "public board deployed on a local blockchain",
        "candidates": "candidates",
        "voting": "VOTING",
        "voting_note": "machines offline. The receipt prints before the voter decides.",
        "machine": "machine {n}",
        "voter": "voter {n}",
        "receipt": "receipt",
        "cast": "cast in secret",
        "opened": "the machine opens it: {name}, as chosen",
        "voided": "test ballot voided, the voter votes again",
        "closing": "POLLS CLOSED",
        "closing_note": "the seal is broken, the network turns on, each machine publishes once",
        "published": "machine {n} published {ballots}, signature accepted",
        "tally": "TALLY",
        "tally_note": "3 of the 5 trustees decrypt only the total",
        "decrypting": "trustees checking the board and decrypting the total",
        "partial": "trustee {n} posted its share of the decryption, with proof",
        "check": "PUBLIC CHECK",
        "check_note": "an independent verifier, using only the public files",
        "checking": "verifier checking every signature and proof",
        "valid": "every signature, ballot proof and decryption proof is valid",
        "counted": "on the board, counted",
        "not_counted": "test ballot, not counted",
        "verdict": "Result verified: {cast} counted, {spoiled} excluded.",
        "vote": "vote",
        "votes": "votes",
        "test": "test ballot",
        "tests": "test ballots",
        "and": "and",
    },
    "pt-BR": {
        "subtitle": "uma eleição local, ao vivo neste computador",
        "software": "imagem simulada da urna confere com o código aprovado",
        "key": "chave da eleição dividida entre 5 guardiões; 3 abrem o resultado",
        "board": "mural público criado numa blockchain local",
        "candidates": "candidatos",
        "voting": "VOTAÇÃO",
        "voting_note": "urnas offline. O recibo sai antes de o eleitor decidir.",
        "machine": "urna {n}",
        "voter": "eleitor {n}",
        "receipt": "recibo",
        "cast": "registrado em sigilo",
        "opened": "a urna abre a cédula: {name}, como escolhido",
        "voided": "cédula de teste anulada, o eleitor vota de novo",
        "closing": "ENCERRAMENTO",
        "closing_note": "o mesário rompe o lacre, a rede liga, cada urna publica uma vez",
        "published": "urna {n} publicou {ballots}, assinatura aceita",
        "tally": "APURAÇÃO",
        "tally_note": "3 dos 5 guardiões decifram só o total",
        "decrypting": "guardiões conferindo o mural e decifrando o total",
        "partial": "guardião {n} publicou sua parte da decifração, com prova",
        "check": "CONFERÊNCIA PÚBLICA",
        "check_note": "um verificador independente, só com os arquivos públicos",
        "checking": "verificador conferindo cada assinatura e prova",
        "valid": "todas as assinaturas e provas conferem",
        "counted": "está no mural e foi contado",
        "not_counted": "teste anulado, não contado",
        "verdict": "Resultado conferido: {cast} na apuração, {spoiled} fora.",
        "vote": "voto",
        "votes": "votos",
        "test": "teste",
        "tests": "testes",
        "and": "e",
    },
}
LANGUAGES = tuple(TEXT)
BAR_WIDTH = 20


class Screen:
    """Print only facts the demo has already checked, one short line at a time."""

    def __init__(self, lang: str = "en", file: TextIO | None = None) -> None:
        if lang not in TEXT:
            raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
        self.text = TEXT[lang]
        # Bind the stream now: the demo later redirects sys.stdout into its protocol log.
        stream = file if file is not None else sys.stdout
        self.console = Console(file=stream, highlight=False, emoji=False)

    def _count(self, n: int, singular: str, plural: str) -> str:
        return f"{n} {self.text[singular] if n == 1 else self.text[plural]}"

    def _ok(self, *parts: str | tuple[str, str]) -> None:
        self.console.print(Text.assemble("  ", ("✓ ", "bold green"), *parts))

    @contextmanager
    def working(self, key: str, **values: object) -> Iterator[None]:
        """Show a spinner on a real terminal; print nothing anywhere else."""
        message = Text(self.text[key].format(**values) + "...", style="dim")
        with Live(
            Spinner("dots", message, style="cyan"),
            console=self.console,
            transient=True,
            redirect_stdout=False,
            redirect_stderr=False,
            refresh_per_second=12,
        ):
            yield

    def title(self) -> None:
        self.console.print()
        self.console.print(
            Text.assemble((" DIRECT PUBLICATION ", "bold black on green"), "  ", self.text["subtitle"])
        )
        self.console.print()

    def software(self) -> None:
        self._ok(self.text["software"])

    def key(self) -> None:
        self._ok(self.text["key"])

    def board(self, candidates: Sequence[str]) -> None:
        self._ok(self.text["board"])
        self.console.print(
            Text.assemble(
                "    ",
                (self.text["candidates"] + ": ", "dim"),
                (" · ".join(candidates), "bold"),
            )
        )

    def section(self, key: str) -> None:
        # Two readable pages on a terminal; pipes retain the complete linear history.
        if key == "closing" and self.console.is_terminal:
            self.console.clear()
            self.title()
        self.console.print()
        self.console.print(Text(self.text[key], style="bold cyan"))
        self.console.print(Text("  " + self.text[key + "_note"], style="dim"))

    def machine(self, n: int) -> None:
        self.console.print(Text("  " + self.text["machine"].format(n=n), style="bold"))

    def receipt(self, voter: int, lookup_code: str) -> None:
        """Display the real receipt before the voter makes either decision."""
        self.console.print(
            Text.assemble(
                "    ",
                (self.text["voter"].format(n=voter), "bold"),
                "  ",
                (self.text["receipt"] + " ", "dim"),
                (lookup_code, "bold yellow"),
                "  ",
            ),
            end="",
        )

    def decision(self, decision: str) -> None:
        line = Text(decision, style="bold magenta" if decision == "TEST" else "bold green")
        if decision == "CONFIRM":
            line.append("  " + self.text["cast"], style="dim")
        self.console.print(line)

    def opened(self, voter: int, name: str) -> None:
        indent = " " * (6 + len(self.text["voter"].format(n=voter)))
        before, after = self.text["opened"].split("{name}")
        self.console.print(
            Text.assemble(indent, before, (name, "bold"), after, (" ✓", "bold green"))
        )
        self.console.print(Text(indent + self.text["voided"], style="dim"))

    def published(self, n: int, cast: int, spoiled: int) -> None:
        ballots = self._count(cast, "vote", "votes")
        if spoiled:
            ballots += f" {self.text['and']} " + self._count(spoiled, "test", "tests")
        self._ok(self.text["published"].format(n=n, ballots=ballots))

    def partial(self, n: int) -> None:
        self._ok(self.text["partial"].format(n=n))

    def valid(self) -> None:
        self._ok(self.text["valid"])

    def receipt_status(self, voter: int, lookup_code: str, counted: bool) -> None:
        self._ok(
            (self.text["voter"].format(n=voter), "bold"),
            "  ",
            (self.text["receipt"] + " ", "dim"),
            (lookup_code, "bold yellow"),
            "  ",
            self.text["counted" if counted else "not_counted"],
        )

    def results(self, candidates: Sequence[str], totals: Sequence[int]) -> None:
        self.console.print()
        width = max(len(name) for name in candidates)
        top = max(totals) or 1
        for name, total in zip(candidates, totals, strict=True):
            bar = "━" * round(BAR_WIDTH * total / top)
            self.console.print(
                Text.assemble(
                    "    ",
                    (name.ljust(width), "bold"),
                    "  ",
                    (bar.ljust(BAR_WIDTH), "green"),
                    "  ",
                    self._count(total, "vote", "votes"),
                )
            )

    def verdict(self, cast: int, spoiled: int, seconds: float) -> None:
        message = self.text["verdict"].format(
            cast=self._count(cast, "vote", "votes"),
            spoiled=self._count(spoiled, "test", "tests"),
        )
        self.console.print()
        self.console.print(
            Text.assemble("  ", (message, "bold green"), (f"  {seconds:.0f} s", "dim"))
        )
        self.console.print()
