# Contributing

Contributions to this study and its implementation are welcome. Please keep protocol and wire-format changes consistent with [`docs/interfaces.md`](docs/interfaces.md), preserve explicit security limitations, and avoid claims of production readiness, novelty, privacy or trust-free operation that the evidence does not support. Use synthetic fixtures only; never add real voter data, production keys or secrets.

## Development setup

Install Python 3.12, `uv` 0.11.7, Foundry 1.8.5 (`forge` and `anvil`) and Solidity 0.8.28 (`solc`). From the repository root:

```sh
uv sync --locked
```

The locked install uses the project-local environment. Do not remove or overwrite global user tools or caches as part of setup.

## Checks

The configured CI runs lint, formatting checks, Solidity tests, Python tests and all eight attack demonstrations. It is configuration, not evidence that a remote run has passed. Local commands are:

```sh
make lint
make format-check
make test
make test-contract
make attacks
```

`make test-contract` and attack commands may run local EVM or Anvil processes. Run them only with the repository's owned ephemeral fixtures and disposable test data; do not point them at a pre-existing, shared, staging or production chain. The attack targets are `make attack-machine`, `make attack-transport`, `make attack-drop-vote`, `make attack-tally`, `make attack-duplicate`, `make attack-rogue-machine`, `make attack-double-vote`, and `make attack-early-network`. The Compose demo is not a substitute for isolation: see the operator acknowledgements and private-VM requirement in [`README.md`](README.md#quickstart).

Before opening a contribution, review the relevant documentation and run the focused check your change affects. Report commands not run and reasons; do not describe unexecuted checks as passing. Do not modify generated measurements to present them as fresh results; retain provenance for any new measurements.

## Licensing and attribution

By intentionally submitting a contribution for inclusion, you agree that it is offered under Apache License 2.0, subject to that license and any separate agreement. See [`LICENSE`](LICENSE). The study is authored by João Henrique Menezes Silva Boia. Do not add other personal names, contact details, repository URLs, or persistent identifiers without verified authorization.
