# Security policy

This repository is a study and a local implementation, not an election system for real voters or production use. See [`docs/threat-model.md`](docs/threat-model.md) for explicit assumptions and limitations. Do not submit real voter data, private keys, production credentials or sensitive election material.

## Reporting a vulnerability

Please contact the maintainers through a private channel agreed with them before publicly disclosing a vulnerability. This repository does not provide a security email, named maintainer contact, or private reporting endpoint; none is invented here. If no private channel has been agreed, do not include secrets or sensitive data in a public issue. Share only a minimal, non-sensitive description and request a private reporting route.

Please include the affected file or command, a concise impact description, reproducible steps using synthetic data, and any suggested mitigation. Allow maintainers time to investigate and coordinate disclosure before publishing exploit details. Do not test against production networks, real elections, systems you do not own, or other people's data.

## Scope and response

The repository's Python, Solidity and local demo code are in scope for responsible reports. The demonstration's simulated switch, scratch cleanup and local Anvil chain are not real security boundaries. This project makes no promise of a response time, bounty, security support, or production suitability. CI configuration is not equivalent to a security audit.
