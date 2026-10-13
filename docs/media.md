# Presentation media

## Opening / Abertura

**[Português: vídeo de 56 segundos](intro.pt-BR.mp4) · [English: 56-second video](intro.mp4)**

The opening follows a ballot from the offline machine to the final tally check. Persistent objects connect the stages instead of disappearing with each title; there is no slide pager. The receipt is issued before the voter confirms or tests. After voting closes, the machine goes online and sends its signed batch straight to the blockchain. No intermediary receives or changes the votes: the contract accepts only the batch signed by the machine. A poll worker enables that simulated connection after close; the voting software cannot turn it on early. A fallback copy, if used, carries the same signed bytes and does not create a new batch. Published ballots contribute to an encrypted sum, three of five trustees open the aggregate, and an independent verifier checks receipt inclusion and recomputes the tally from public data and proofs. Each title states the action; its caption explains the reason. The comparison with Brazil concerns what the voter can check: voting is already offline, the attendance slip is not a cryptographic ballot receipt, and the polling-station bulletin reports totals rather than identifying the voter's ballot. These are reasons for this proposal, not a claim that Brazil has no audits or that a blockchain is the only possible public record. The artwork uses neutral tones, restrained color accents and regular-weight typography. The video has on-screen captions and no audio. The README embeds an animated GIF that plays automatically. Open the linked MP4 in a video player to pause, replay or seek; the inline GIF loops without playback controls.

A sequência acompanha a cédula e o recibo até a conferência da apuração, mantendo os objetos reconhecíveis nas transições. O recibo sai antes de confirmar ou testar; encerrada a votação, a urna fica online e envia o lote assinado direto à blockchain, sem intermediário que receba ou altere os votos, porque o contrato só aceita o lote assinado pela própria urna. Quem habilita essa conexão simulada, depois do encerramento, é o mesário; o programa da urna não liga a rede antes. Uma cópia de reserva, se for usada, carrega os mesmos bytes assinados e não cria outro lote. Os votos publicados compõem a soma cifrada; três de cinco guardiões abrem o total; outro programa confere a inclusão e refaz a apuração. O título diz o que acontece; a legenda explica o motivo. A comparação com o Brasil distingue o Boletim de Urna, o comprovante de comparecimento e o recibo criptográfico, sem apresentar o sigilo ou a votação offline como novidades. `R7` é apenas um código simbólico para acompanhar o exemplo, não uma prova criptográfica ou recibo real. A explicação é ilustrativa. A gravação de terminal abaixo é a execução real.

[`intro.svg`](intro.svg) and [`intro.pt-BR.svg`](intro.pt-BR.svg) remain the authoritative, self-contained artwork and animation sources. The MP4s, PNG posters and inline GIFs all come from the same lossless SVG-rendered frames, not from separate presentations or a second lossy video conversion. The words change on eight-second beats. The picture keeps moving; those beats are not separate slides. Essential captions appear with each beat and remain readable. The SVGs show the complete summary in reduced-motion mode; GIF playback does not follow that preference. The SVGs need no JavaScript, downloaded artwork or external fonts.

### Reproduce the explanation

With the project's Python environment ready, install Chrome/Chromium and ffmpeg. Then, from the repository root:

```sh
work="$(mktemp -d -t urna-intro-XXXXXX)"
uv run python -m demo.render_intro --lang en --output-root "$work/en" --fps 24 --workers 4
uv run python -m demo.render_intro --lang pt-BR --output-root "$work/pt-BR" --fps 24 --workers 4
```

The renderer creates its own browser profiles and local frame directories; it does not use your browser session, a web server, Anvil, Docker or an external service. `--output-root` must not exist. Use `--chrome /path/to/browser` if Chrome is not found automatically. The source hash and render settings are retained in `render.json`, alongside the frames and scene screenshots. The two language renders can run concurrently on a machine with sufficient CPU budget; worker counts share that budget. MP4 encoding pins H.264 CRF 16, the slow preset and animation tuning, retaining broadly compatible `yuv420p`. A solid neutral background avoids near-black gradient bands. Capture uses a native window larger than the canvas and an explicit screenshot clip, then normalizes Retina pixels to 1000×600. Do not replace this with virtual device-metrics resizing without checking every frame: on Chrome 155/macOS, a virtual canvas larger than the native view produced intermittent tiled copies of the page before encoding.

After checking the generated videos, these commands replace the derived README assets:

```sh
cp "$work/en/intro.mp4" docs/intro.mp4
cp "$work/en/poster.png" docs/intro.png
cp "$work/pt-BR/intro.mp4" docs/intro.pt-BR.mp4
cp "$work/pt-BR/poster.png" docs/intro.pt-BR.png
```

Generate the inline animations directly from the lossless frames (ffmpeg 7.1.1, 1000×600, 20 fps, 56 seconds, infinite loop). The MP4 runs at 24 fps for smoother motion; `-framerate 24` below matches the explicit render commands above, not the renderer's 12 fps default. Do not use the MP4 as the GIF source: it carries video quantization noise into the palette. Disabling dithering also avoids checkerboard textures on neutral surfaces.

```sh
for lang in en pt-BR; do
  suffix=""
  if [ "$lang" = pt-BR ]; then suffix=".pt-BR"; fi
  ffmpeg -nostdin -v error -n -threads 2 -framerate 24 \
    -i "$work/$lang/frames/frame-%06d.png" -filter_complex_threads 2 \
    -filter_complex '[0:v]fps=20,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle[v]' \
    -map '[v]' -threads 1 -loop 0 "$work/$lang/intro.gif" && \
    cp "$work/$lang/intro.gif" "docs/intro${suffix}.gif"
done
```

The blockchain is shown as the public record of signed batches, not as a replacement for cryptographic proof verification. The demo uses one local Anvil node, not a deployed validator network. The opening does not claim that Brazil lacks auditing or that Urna Aberta eliminates every trust assumption. See [`architecture.md`](architecture.md) and [`threat-model.md`](threat-model.md).

## Brazilian system context

- [Justiça Eleitoral: oportunidades de auditoria e fiscalização](https://www.justicaeleitoral.jus.br/urna-eletronica/oportunidades-de-auditoria-e-fiscalizacao.html) describes the Boletim de Urna as containing a polling station's result and explains that anyone can compare the local bulletin with the TSE's published version. It also describes the machine's lack of network connectivity, the RDV (digital vote record), logs, publication of election files, and external auditing with third-party programs.
- [TSE Resolution 23.751/2026](https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-751-de-26-de-fevereiro-de-2026), article 204, distinguishes individual vote records from voter identity: the RDV records votes while preserving voter anonymity. Article 128(I) describes the voting acknowledgement delivered to voters after they vote.

The comparison is about the verification offered by each artifact: the bulletin reports totals; the voting acknowledgement documents participation; the RDV is not a voter-held cryptographic receipt. Brazil already publishes the RDV and other files for auditing; independent checking or recounting alone is not presented as an invention of this prototype. The additional voter-facing artifact is the signed receipt linking a supplied ballot to the public records. It is not a claim that Brazil lacks audits or evidence of fraud. Nor does anonymity alone prove whether a system could support a private, verifiable receipt.

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
