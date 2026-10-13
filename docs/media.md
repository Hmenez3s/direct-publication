# Presentation media

## Opening / Abertura

**[Português: vídeo de 56 segundos](intro.pt-BR.mp4) · [English: 56-second video](intro.mp4)**

The opening follows the data from a private choice to a publicly checkable result: encrypted ballot, receipt and decision, signed batch on the blockchain, encrypted sum, opening of the total, and independent verification. Each diagram shows what happens to the data; its headline and caption explain why the step matters: privacy, committing before a test, publicly checkable publication rules, counting without exposing choices, splitting decryption authority, and independent verification. These are reasons for the proposed design, not a claim that blockchain is the only possible public-record system. The artwork uses neutral tones, restrained color accents and regular-weight typography. The video has on-screen captions and no audio. The README embeds an animated GIF that plays automatically. Open the linked MP4 in a video player to pause, replay or seek; the inline GIF loops without playback controls.

A sequência acompanha os dados: a escolha vira uma cédula cifrada; o recibo sai antes de confirmar ou testar; o lote assinado vai para a blockchain após o encerramento; as cédulas são somadas ainda cifradas; três guardiões permitem abrir o total; e um verificador confere as provas, a inclusão do recibo fornecido e a apuração. A última cena reúne o que pode ser conferido. `R7` é apenas um código simbólico para acompanhar o exemplo, não uma prova criptográfica ou recibo real. A explicação é ilustrativa. A gravação de terminal abaixo é a execução real.

[`intro.svg`](intro.svg) and [`intro.pt-BR.svg`](intro.pt-BR.svg) remain the authoritative, self-contained artwork and animation sources. The MP4s, PNG posters and inline GIFs all come from the same lossless SVG-rendered frames, not from separate presentations or a second lossy video conversion. Each scene lasts eight seconds. Essential captions appear at the start and remain readable. The SVGs show the complete summary in reduced-motion mode; GIF playback does not follow that preference. The SVGs need no JavaScript, downloaded artwork or external fonts.

### Reproduce the explanation

With the project's Python environment ready, install Chrome/Chromium and ffmpeg. Then, from the repository root:

```sh
work="$(mktemp -d -t urna-intro-XXXXXX)"
uv run python -m demo.render_intro --lang en --output-root "$work/en" --workers 4
uv run python -m demo.render_intro --lang pt-BR --output-root "$work/pt-BR" --workers 4
```

The renderer creates its own browser profiles and local frame directories; it does not use your browser session, a web server, Anvil, Docker or an external service. `--output-root` must not exist. Use `--chrome /path/to/browser` if Chrome is not found automatically. The source hash and render settings are retained in `render.json`, alongside the frames and scene screenshots. The two language renders can run concurrently on a machine with sufficient CPU budget; worker counts share that budget. MP4 encoding pins H.264 CRF 16, the slow preset and animation tuning, retaining broadly compatible `yuv420p`. A solid neutral background avoids near-black gradient bands. Capture uses a native window larger than the canvas and an explicit screenshot clip, then normalizes Retina pixels to 1000×600. Do not replace this with virtual device-metrics resizing without checking every frame: on Chrome 155/macOS, a virtual canvas larger than the native view produced intermittent tiled copies of the page before encoding.

After checking the generated videos, these commands replace the derived README assets:

```sh
cp "$work/en/intro.mp4" docs/intro.mp4
cp "$work/en/poster.png" docs/intro.png
cp "$work/pt-BR/intro.mp4" docs/intro.pt-BR.mp4
cp "$work/pt-BR/poster.png" docs/intro.pt-BR.png
```

Generate the inline animations directly from the lossless frames (ffmpeg 7.1.1, 1000×600, 10 fps, 56 seconds, infinite loop). `-framerate 12` matches the renderer default. Do not use the MP4 as the GIF source: it carries video quantization noise into the palette. Disabling dithering also avoids checkerboard textures on neutral surfaces.

```sh
for lang in en pt-BR; do
  suffix=""
  if [ "$lang" = pt-BR ]; then suffix=".pt-BR"; fi
  ffmpeg -nostdin -v error -n -threads 2 -framerate 12 \
    -i "$work/$lang/frames/frame-%06d.png" -filter_complex_threads 2 \
    -filter_complex '[0:v]fps=10,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle[v]' \
    -map '[v]' -threads 1 -loop 0 "$work/$lang/intro.gif" && \
    cp "$work/$lang/intro.gif" "docs/intro${suffix}.gif"
done
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
