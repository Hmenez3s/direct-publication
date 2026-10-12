# Solidity 0.8.28's official Linux native compiler is amd64; ARM builds need
# their container engine's amd64 emulation only for the compiler build step.
FROM ghcr.io/foundry-rs/foundry:v1.8.5 AS foundry
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM --platform=linux/amd64 python:3.12.11-slim AS compiler-runtime

FROM python:3.12.11-slim
WORKDIR /app

COPY --from=foundry /usr/local/bin/forge /usr/local/bin/forge
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil
COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY attacks/ ./attacks/
COPY demo/ ./demo/
COPY trustees/ ./trustees/
COPY urna/ ./urna/
COPY verifier/ ./verifier/
COPY contracts/foundry.toml ./contracts/foundry.toml
COPY contracts/src/ ./contracts/src/

RUN uv sync --locked --no-dev
RUN python - <<'PY'
import hashlib
from pathlib import Path
from urllib.request import urlopen

url = "https://raw.githubusercontent.com/ethereum/solc-bin/gh-pages/linux-amd64/solc-linux-amd64-v0.8.28+commit.7893614a"
expected = "9a0fb7e0db2c0641dbae1c5cc645dc686820c83af516226abb1c0a2f76636f25"
with urlopen(url, timeout=60) as response:
    data = response.read()
actual = hashlib.sha256(data).hexdigest()
if actual != expected:
    raise RuntimeError(f"solc checksum mismatch: {actual}")
target = Path("/usr/local/bin/solc-0.8.28")
target.write_bytes(data)
target.chmod(0o755)
PY
# The official compiler needs its amd64 dynamic loader, even on an ARM build.
COPY --from=compiler-runtime /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu
RUN mkdir -p /lib64 && ln -sf /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /lib64/ld-linux-x86-64.so.2
ENV FOUNDRY_SOLC=/usr/local/bin/solc-0.8.28
RUN forge build --root contracts

CMD ["python", "-m", "attacks.machine"]
