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
