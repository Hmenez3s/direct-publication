"""Executable attack demonstrations; EVM cases use integration-named selectors."""

from __future__ import annotations

import pytest

from attacks import double_vote, early_network, machine
from attacks.common import offline_manifest


def test_machine_attack_uses_valid_ballot_and_exposes_opening_mismatch(capsys):
    manifest = offline_manifest()
    machine.demonstrate(manifest)
    output = capsys.readouterr().out
    assert "FAULT:" in output and "DETECTED:" in output
    assert "invalid opening" in output
    assert "differs from voter intent" in output
    assert output.count("DETECTED:") == 2


def test_valid_substituted_opening_passes_crypto_before_intent_check(monkeypatch):
    accepted_choices = []
    real_verify = machine.verify_opening

    def record_valid_opening(manifest, ballot, choice, randomness):
        real_verify(manifest, ballot, choice, randomness)
        accepted_choices.append(choice)

    monkeypatch.setattr(machine, "verify_opening", record_valid_opening)
    machine.demonstrate(offline_manifest())
    assert accepted_choices == [1]


def test_missing_independent_intent_check_does_not_detect_substitution(monkeypatch):
    monkeypatch.setattr(machine, "_compare_intent", lambda *_args: None)
    with pytest.raises(AssertionError, match="was not detected"):
        machine.demonstrate(offline_manifest())


def test_double_vote_has_fresh_valid_bit_proofs_and_fails_sum_proof(capsys):
    double_vote.demonstrate(offline_manifest())
    output = capsys.readouterr().out
    assert "exactly-one sum proof" in output


def test_early_network_is_denied_before_transport_enable(capsys):
    manifest, key = offline_manifest(with_key=True)
    early_network.demonstrate(manifest, key)
    assert "before any send" in capsys.readouterr().out


def test_disabled_double_vote_detector_is_a_failure(monkeypatch):
    monkeypatch.setattr("attacks.double_vote.verify_ballot", lambda *_args: None)
    with pytest.raises(AssertionError, match="was not detected"):
        double_vote.demonstrate(offline_manifest())


def test_unexpected_error_with_detector_text_is_not_detection():
    from attacks.common import assert_detected

    def broken_setup():
        raise RuntimeError("invalid opening")

    with pytest.raises(RuntimeError):
        assert_detected("machine", "synthetic fault", "invalid opening", broken_setup)


def test_contract_detector_matches_selector_and_rejects_unrelated_failure():
    from eth_utils import keccak
    from web3.exceptions import ContractCustomError

    from attacks.common import require_contract_revert

    selector = "0x" + keccak(text="WrongSigner()")[:4].hex()

    def revert():
        raise ContractCustomError(selector, data=selector)

    assert require_contract_revert(revert, ("WrongSigner",)) == f"WrongSigner ({selector})"
    with pytest.raises(AssertionError, match="unrelated contract revert"):
        require_contract_revert(revert, ("BatchAlreadyPosted",))
    with pytest.raises(AssertionError, match="accepted the attack"):
        require_contract_revert(lambda: None, ("WrongSigner",))

    def disconnected():
        raise ConnectionError("node unavailable")

    with pytest.raises(ConnectionError):
        require_contract_revert(disconnected, ("WrongSigner",))


def test_integration_transport_detects_tamper_with_contract_and_signature():
    from attacks.transport import demonstrate_contract

    demonstrate_contract()


def test_integration_duplicate_rejected_before_deadline():
    from attacks.duplicate import demonstrate

    demonstrate()


def test_integration_rogue_batch_rejected_before_deadline():
    from attacks.rogue_machine import demonstrate

    demonstrate()


def test_integration_drop_vote_fails_full_receipt_audit():
    from attacks.drop_vote import demonstrate

    demonstrate()


def test_integration_false_tally_is_compared_to_complete_verifier():
    from attacks.common import honest_scenario
    from attacks.tally import demonstrate

    with honest_scenario() as scenario:
        demonstrate(scenario.manifest, scenario.evidence, scenario.receipts)
