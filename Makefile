# BEGIN quality
WORKERS ?= $(shell python3 -c 'import math,os; print(max(1,math.floor(0.8*(os.cpu_count() or 1))))')

.PHONY: lint format-check test

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

test:
	@tmpdir=$$(mktemp -d -t urna-schema-XXXXXX); uv run pytest -n $(WORKERS) --basetemp "$$tmpdir"
# END quality

# BEGIN attacks
.PHONY: build-contract test-contract attacks attack-machine attack-transport attack-drop-vote attack-tally attack-duplicate attack-rogue-machine attack-double-vote attack-early-network check

build-contract:
	forge build --root contracts -j $(WORKERS)

# Public-chain checks need the generated ABI and bytecode on a fresh checkout.
test attack-transport attack-drop-vote attack-tally attack-duplicate attack-rogue-machine: build-contract

test-contract: build-contract
	forge test --root contracts -j $(WORKERS)

attack-machine:
	uv run python -m attacks.machine

attack-transport:
	uv run python -m attacks.transport

attack-drop-vote:
	uv run python -m attacks.drop_vote

attack-tally:
	uv run python -m attacks.tally

attack-duplicate:
	uv run python -m attacks.duplicate

attack-rogue-machine:
	uv run python -m attacks.rogue_machine

attack-double-vote:
	uv run python -m attacks.double_vote

attack-early-network:
	uv run python -m attacks.early_network

attacks: attack-machine attack-transport attack-drop-vote attack-tally attack-duplicate attack-rogue-machine attack-double-vote attack-early-network

check: lint format-check test test-contract attacks
# END attacks

# BEGIN demo
.PHONY: demo measure

OUTPUT_ROOT ?=

demo: build-contract
	@if [ -n "$(OUTPUT_ROOT)" ]; then \
		uv run python -m demo.e2e --output-root "$(OUTPUT_ROOT)"; \
	else \
		scratch=$$(mktemp -d -t urna-demo-XXXXXX) && uv run python -m demo.e2e --output-root "$$scratch/output"; \
	fi

measure: build-contract
	@if [ -n "$(OUTPUT_ROOT)" ]; then \
		uv run python -m demo.measure --output-root "$(OUTPUT_ROOT)"; \
	else \
		scratch=$$(mktemp -d -t urna-measure-XXXXXX) && uv run python -m demo.measure --output-root "$$scratch/output"; \
	fi
# END demo

# BEGIN paper
.PHONY: paper

paper:
	@set -eu; \
	root="$$PWD"; \
	scratch=$$(mktemp -d -t urna-paper-XXXXXX); \
	trap 'rm -rf "$$scratch"' EXIT HUP INT TERM; \
	mkdir -p "$$scratch/paper/figures" "$$scratch/out" "$$scratch/home" "$$scratch/cache" "$$scratch/mpl"; \
	cp paper/main.tex paper/refs.bib paper/llncs.cls paper/splncs04.bst "$$scratch/paper/"; \
	cp paper/figures/challenge-probability.pdf "$$scratch/paper/figures/"; \
	MPLCONFIGDIR="$$scratch/mpl" uv run python paper/figures/evaluation.py --source demo/results/measurements.json --output "$$scratch/paper/figures/evaluation.pdf"; \
	cd "$$scratch/paper"; \
	HOME="$$scratch/home" XDG_CACHE_HOME="$$scratch/cache" tectonic --outdir "$$scratch/out" main.tex; \
	test -s "$$scratch/out/main.pdf"; \
	cd "$$root"; \
	python3 -c 'from pathlib import Path; targets=[Path("paper/main.pdf"),Path("paper/figures/evaluation.pdf")]; assert all(not p.is_symlink() and (not p.exists() or (p.is_file() and p.stat().st_nlink == 1)) for p in targets), "refusing linked or non-file generated output"'; \
	cp "$$scratch/out/main.pdf" "$$scratch/main.pdf"; \
	cp "$$scratch/paper/figures/evaluation.pdf" "$$scratch/evaluation.pdf"; \
	mv "$$scratch/main.pdf" paper/main.pdf; \
	mv "$$scratch/evaluation.pdf" paper/figures/evaluation.pdf
# END paper
