"""A resigned incomplete batch remains signature-valid but omits a retained receipt."""

from __future__ import annotations

from dataclasses import replace

from attacks.common import prepare_scenario, show_detection
from demo.chain import advance_time, post_batch, read_evidence
from urna.schema import BatchPayload
from urna.signing import sign_batch, verify_batch_signature
from verifier.audit import audit_board


def demonstrate() -> None:
    with prepare_scenario(choices=(0, 1)) as scenario:
        machine = scenario.machine_accounts[0]
        original = BatchPayload.from_bytes(bytes.fromhex(scenario.signed_batches[0].payload_hex))
        assert len(original.cast) == 2
        shortened = replace(original, cast=original.cast[:1])
        payload = shortened.to_bytes()
        signature = sign_batch(
            scenario.manifest.chain_id,
            scenario.manifest.board_address,
            scenario.manifest.election_id,
            payload,
            machine.key,
        )
        if not verify_batch_signature(
            scenario.manifest.chain_id,
            scenario.manifest.board_address,
            scenario.manifest.election_id,
            payload,
            signature,
            machine.address[2:].lower(),
        ):
            raise AssertionError("resigned incomplete batch failed signature validation")

        post_batch(scenario.handle, scenario.board, machine, payload, signature)
        advance_time(scenario.handle, scenario.manifest.batch_deadline + 1)
        evidence = read_evidence(scenario.handle, scenario.board, scenario.manifest)
        try:
            audit_board(scenario.manifest, evidence, scenario.receipts)
        except ValueError as exc:
            reason = str(exc)
            if "receipt ballot is not included" not in reason:
                raise AssertionError(f"unexpected audit failure: {reason}") from exc
            show_detection("drop_vote", "machine omitted one retained full receipt", reason)
            return
        raise AssertionError("retained receipt omission was not detected")


def main() -> int:
    demonstrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
