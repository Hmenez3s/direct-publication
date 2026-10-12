"""Tampered signed bytes fail software validation and the actual contract."""

from __future__ import annotations

from attacks.common import prepare_scenario, require_contract_revert, show_detection
from urna.signing import verify_batch_signature


def demonstrate_contract() -> None:
    with prepare_scenario(choices=(0,)) as scenario:
        machine = scenario.machine_accounts[0]
        signed = scenario.signed_batches[0]
        payload = bytes.fromhex(signed.payload_hex) + b" "
        signature_false = not verify_batch_signature(
            scenario.manifest.chain_id,
            scenario.manifest.board_address,
            scenario.manifest.election_id,
            payload,
            signed.signature,
            machine.address[2:].lower(),
        )
        if not signature_false:
            raise AssertionError("tampered bytes unexpectedly passed signature verifier")
        reason = require_contract_revert(
            lambda: scenario.board.functions.postBatch(
                payload, bytes.fromhex(signed.signature)
            ).transact({"from": machine.address}),
            ("WrongSigner", "InvalidSignature"),
        )
        show_detection(
            "transport",
            "signed payload bytes changed",
            f"signatureFalse={signature_false}; contract {reason}",
        )


def main() -> int:
    demonstrate_contract()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
