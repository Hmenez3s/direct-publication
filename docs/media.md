# Presentation media

## Opening / Abertura

**[Português: vídeo de 56 segundos](intro.pt-BR.mp4) · [English: 56-second video](intro.mp4)**

The opening follows one symbolic receipt, `R7`, through the system. It separates the offline machine, signed batch transaction, blockchain contract, threshold trustees and independent verifier. The video has on-screen captions and no audio. Open the MP4 in a video player to pause, replay or seek; the README uses a still poster rather than requiring readers to follow an autoplay loop.

Os sete trechos mostram: o mapa do sistema; a emissão do recibo; a decisão entre confirmar e testar; a publicação do lote na blockchain; a contribuição de três guardiões; a conferência pelo eleitor; e um resumo das responsabilidades. `R7` é apenas um código simbólico para acompanhar o exemplo, não uma prova criptográfica ou recibo real. A explicação é ilustrativa. A gravação de terminal abaixo é a execução real.

[`intro.svg`](intro.svg) and [`intro.pt-BR.svg`](intro.pt-BR.svg) remain the authoritative, self-contained artwork and animation sources. The MP4s and PNG posters are generated from those exact SVGs, not maintained as a second presentation. Each scene lasts eight seconds. Essential captions appear at the start and remain readable; reduced-motion mode shows the complete summary. The SVGs need no JavaScript, downloaded artwork or external fonts.

### Reproduce the explanation

With the project's Python environment ready, install Chrome/Chromium and ffmpeg. Then, from the repository root:

```sh
work="$(mktemp -d -t urna-intro-XXXXXX)"
uv run python -m demo.render_intro --lang en --output-root "$work/en" --workers 4
uv run python -m demo.render_intro --lang pt-BR --output-root "$work/pt-BR" --workers 4
```

The renderer creates its own browser profiles and local frame directories; it does not use your browser session, a web server, Anvil, Docker or an external service. `--output-root` must not exist. Use `--chrome /path/to/browser` if Chrome is not found automatically. The source hash and render settings are retained in `render.json`, alongside the frames and scene screenshots. The two language renders can run concurrently on a machine with sufficient CPU budget; worker counts share that budget.

After checking the generated videos, these commands replace the derived README assets:

```sh
cp "$work/en/intro.mp4" docs/intro.mp4
cp "$work/en/poster.png" docs/intro.png
cp "$work/pt-BR/intro.mp4" docs/intro.pt-BR.mp4
cp "$work/pt-BR/poster.png" docs/intro.pt-BR.png
```

The blockchain is shown as the public record of signed batches, not as a replacement for cryptographic proof verification. The demo uses one local Anvil node, not a deployed validator network. The opening does not claim that Brazil lacks auditing or that Urna Aberta eliminates every trust assumption. See [`architecture.md`](architecture.md) and [`threat-model.md`](threat-model.md).

## Brazilian system context

- [Justiça Eleitoral: oportunidades de auditoria e fiscalização](https://www.justicaeleitoral.jus.br/urna-eletronica/oportunidades-de-auditoria-e-fiscalizacao.html) describes the Boletim de Urna as containing a polling station's result and explains that anyone can compare the local bulletin with the TSE's published version. It also describes auditing, the RDV (digital vote record), and logs.
- [TSE Resolution 23.751/2026](https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-751-de-26-de-fevereiro-de-2026), article 204, distinguishes individual vote records from voter identity: the RDV records votes while preserving voter anonymity. Article 128(I) describes the voting acknowledgement delivered to voters after they vote.

The comparison is about the verification offered by each artifact: the bulletin reports totals; the voting acknowledgement documents participation; the RDV is not a voter-held cryptographic receipt. It is not a claim that Brazil lacks audits or evidence of fraud. Nor does anonymity alone prove whether a system could support a private, verifiable receipt.

A comparação trata do que cada documento permite conferir. Boletim com totais, comprovante de comparecimento e recibo criptográfico da cédula são coisas diferentes. O material não afirma que o sistema brasileiro não tenha auditorias, nem apresenta indício de fraude.

## Terminal recording / Gravação do terminal

The two GIFs are genuine VHS recordings of `make demo`, not a replay of saved results:

```sh
uv sync --locked
vhs docs/demo.tape
vhs docs/demo.pt-BR.tape
```

Run from the repository root with the README's Python, uv, Foundry and solc prerequisites installed, plus VHS 0.12.1, ttyd, ffmpeg and a browser supported by VHS. These commands replace the corresponding generated GIF. Both tapes use 3× playback and hold the final screen for five playback seconds.

For the terminal without recording:

```sh
make demo
make demo DEMO_LANG=pt-BR
```

The tape calls `demo.capture`, which runs `make demo` in a child process, streams its actual screen, and archives stdout/stderr. The screen is owned by `demo/screen.py`; the capture wrapper does not synthesize another version of the election. Each run starts its own fresh Anvil node, uses synthetic keys and writes to a new temporary directory. It never connects to an existing chain. No Docker or hosted service is required.

The capture directory retains `capture.json`, `child.stdout`, `child.stderr` and `demo-output/`. The latter contains the manifest, board evidence, receipts, signed carriers, verifier report and full protocol transcript. The wrapper requires successful child exit and consistent fresh evidence before printing its capture-completion marker. Hashes and run identifiers stay in the files, not across the presentation screen. Receipt locators differ on every genuine run.

The readable terminal view shows the receipt before `TEST` or `CONFIRM`. Only the tested ballot's candidate is opened; it is voided and excluded from the tally. Confirmed choices stay out of the screen. On a terminal, the display starts a second page at poll closure; redirected output remains a complete linear log. This also avoids VHS 0.12.1's `Wait+Screen` reading the initial buffer rather than scrolled lines, reproduced with a simple multi-line shell command.
