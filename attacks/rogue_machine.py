"""A validly signed batch from an unregistered account is rejected by the board."""

from __future__ import annotations

from attacks.common import prepare_scenario, require_contract_revert, show_detection
from urna.schema import BatchPayload, manifest_digest
from urna.signing import sign_batch


def demonstrate() -> None:
    with prepare_scenario(choices=(0,)) as scenario:
        rogue = scenario.handle.accounts[8]
        payload_record = BatchPayload(
            1,
            scenario.manifest.election_id,
            manifest_digest(scenario.manifest),
            scenario.manifest.chain_id,
            scenario.manifest.board_address,
            rogue.address[2:].lower(),
            (),
            (),
        )
        payload = payload_record.to_bytes()
        signature = sign_batch(
            scenario.manifest.chain_id,
            scenario.manifest.board_address,
            scenario.manifest.election_id,
            payload,
            rogue.key,
        )
        reason = require_contract_revert(
            lambda: scenario.board.functions.postBatch(payload, bytes.fromhex(signature)).transact(
                {"from": rogue.address}
            ),
            ("UnregisteredMachine",),
        )
        show_detection("rogue_machine", "unregistered account signed and submitted", reason)


def main() -> int:
    demonstrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
