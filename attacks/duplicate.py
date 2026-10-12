"""The board rejects a second raw publication from one registered machine."""

from __future__ import annotations

from attacks.common import prepare_scenario, require_contract_revert, show_detection
from demo.chain import post_batch


def demonstrate() -> None:
    with prepare_scenario(choices=(0,)) as scenario:
        machine = scenario.machine_accounts[0]
        signed = scenario.signed_batches[0]
        payload, signature = bytes.fromhex(signed.payload_hex), bytes.fromhex(signed.signature)
        post_batch(scenario.handle, scenario.board, machine, payload, signed.signature)
        reason = require_contract_revert(
            lambda: scenario.board.functions.postBatch(payload, signature).transact(
                {"from": machine.address}
            ),
            ("BatchAlreadyPosted",),
        )
        show_detection("duplicate", "same registered machine submitted twice", reason)


def main() -> int:
    demonstrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
