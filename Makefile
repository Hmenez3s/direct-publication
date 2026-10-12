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
