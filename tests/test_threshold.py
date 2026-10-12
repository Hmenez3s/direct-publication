"""Synthetic proof, interpolation, and bounded-tally oracles."""

from dataclasses import replace
from itertools import combinations

import pytest

from trustees.dealer import Share, create_dealer, verify_share
from trustees.threshold import decrypt_tally, make_partial, verify_partial
from urna.crypto import P, Q, encrypt, multiply
from urna.schema import Aggregate, ElectionManifest, TrusteePublic, manifest_digest


def hx(value: int) -> str:
    return f"{value:0512x}"


def election():
    setup = create_dealer("12" * 32)
    manifest = ElectionManifest(
        1,
        setup.election_id,
        "rfc3526-group14",
        1,
        "10" * 20,
        10,
        20,
        ("A", "B", "C"),
        ("20" * 20,),
        tuple(TrusteePublic(i, f"{i:040x}", setup.verification_keys[i - 1]) for i in range(1, 6)),
        setup.coefficient_commitments,
        setup.public_key,
        100,
    )
    return setup, manifest


def aggregate(manifest, totals=(2, 1, 1)):
    encryptions = [
        [encrypt(manifest.public_key, 1, 20 + ballot * 3 + candidate) for ballot in range(total)]
        for candidate, total in enumerate(totals)
    ]
    return Aggregate(
        manifest.election_id,
        manifest_digest(manifest),
        "34" * 32,
        sum(totals),
        tuple(multiply(items) for items in encryptions),
    )


def partials(setup, manifest, agg):
    return tuple(make_partial(manifest, agg, share) for share in setup.shares)


def test_dealer_degree_two_shares_and_commitments():
    setup, manifest = election()
    assert setup.public_key == setup.coefficient_commitments[0]
    assert setup.coefficient_commitments[2] != hx(1)
    for i, share in enumerate(setup.shares):
        assert verify_share(setup, share)
        assert share.index == i + 1
        assert setup.verification_keys[i] == manifest.trustees[i].verification_key
    changed = replace(setup.shares[0], value=hx((int(setup.shares[0].value, 16) + 1) % Q))
    assert not verify_share(setup, changed)
    assert not verify_share(replace(setup, public_key=hx(4)), setup.shares[0])
    with pytest.raises(ValueError):
        Share(True, hx(1))


@pytest.mark.parametrize("subset", list(combinations(range(5), 3)))
def test_every_three_trustees_recover_independent_known_totals(subset):
    setup, manifest = election()
    agg = aggregate(manifest, (2, 1, 1))
    all_partials = partials(setup, manifest, agg)
    selected = tuple(all_partials[i] for i in subset)
    assert decrypt_tally(manifest, agg, selected) == (2, 1, 1)


def test_reject_insufficient_duplicate_unregistered_and_foreign_aggregate():
    setup, manifest = election()
    agg = aggregate(manifest)
    generated = partials(setup, manifest, agg)
    with pytest.raises(ValueError, match="at least three"):
        decrypt_tally(manifest, agg, generated[:2])
    with pytest.raises(ValueError, match="duplicate"):
        decrypt_tally(manifest, agg, (generated[0], generated[0], generated[1]))
    unregistered = replace(generated[0], trustee_index=5)
    with pytest.raises(ValueError):
        decrypt_tally(manifest, agg, (generated[1], generated[2], unregistered))
    other = aggregate(manifest, (1, 2, 1))
    with pytest.raises(ValueError, match="different election or aggregate"):
        verify_partial(manifest, other, generated[0])


def test_validate_every_supplied_partial_including_fourth_and_fifth():
    setup, manifest = election()
    agg = aggregate(manifest)
    generated = list(partials(setup, manifest, agg))
    for invalid_index in (3, 4):
        item = generated[invalid_index]
        bad_value = hx((int(item.values[0], 16) * 2) % P)
        generated[invalid_index] = replace(item, values=(bad_value, *item.values[1:]))
        with pytest.raises(ValueError, match="proof"):
            decrypt_tally(manifest, agg, generated)
        generated[invalid_index] = item


def test_partial_transcript_and_group_mutations_fail():
    setup, manifest = election()
    agg = aggregate(manifest)
    partial = partials(setup, manifest, agg)[0]
    mutated = replace(
        partial,
        proofs=(
            replace(partial.proofs[0], z=hx(int(partial.proofs[0].z, 16) + 1)),
            *partial.proofs[1:],
        ),
    )
    with pytest.raises(ValueError, match="proof"):
        verify_partial(manifest, agg, mutated)
    invalid_group = replace(partial, values=(hx(P), *partial.values[1:]))
    with pytest.raises(ValueError, match="subgroup"):
        verify_partial(manifest, agg, invalid_group)
    other_election = replace(manifest, election_id="56" * 32)
    with pytest.raises(ValueError):
        verify_partial(other_election, agg, partial)


def test_manifest_commitments_and_public_key_are_recomputed():
    setup, manifest = election()
    agg = aggregate(manifest)
    wrong_key = replace(manifest.trustees[0], verification_key=hx(4))
    bad_manifest = replace(manifest, trustees=(wrong_key, *manifest.trustees[1:]))
    with pytest.raises(ValueError, match="polynomial commitments"):
        make_partial(bad_manifest, agg, setup.shares[0])
    bad_commitment = replace(
        manifest,
        coefficient_commitments=(manifest.public_key, hx(16), manifest.coefficient_commitments[2]),
    )
    with pytest.raises(ValueError, match="polynomial commitments"):
        make_partial(bad_commitment, agg, setup.shares[0])


def test_bounded_discrete_log_and_incompatible_totals_rejected():
    setup, manifest = election()
    over_bound = aggregate(manifest, (2, 0, 0))
    over_bound = replace(over_bound, cast_count=1)
    with pytest.raises(ValueError, match="outside the allowed range"):
        decrypt_tally(manifest, over_bound, partials(setup, manifest, over_bound)[:3])
    inconsistent = replace(aggregate(manifest, (2, 1, 0)), cast_count=5)
    parts = partials(setup, manifest, inconsistent)
    with pytest.raises(ValueError, match="do not equal cast_count"):
        decrypt_tally(manifest, inconsistent, parts[:3])


def test_aggregate_manifest_binding_rejects_foreign_manifest():
    setup, manifest = election()
    agg = aggregate(manifest)
    foreign = replace(manifest, election_id="56" * 32)
    with pytest.raises(ValueError, match="election"):
        decrypt_tally(foreign, agg, partials(setup, manifest, agg)[:3])


@pytest.mark.parametrize("coefficients", [(7, 0, 0), (2, Q - 3, 1)])
def test_reject_setups_that_reduce_the_three_trustee_privacy_threshold(coefficients):
    _, original = election()
    commitments = tuple(hx(pow(2, coefficient, P)) for coefficient in coefficients)
    values = tuple(
        sum(c * pow(i, degree, Q) for degree, c in enumerate(coefficients)) % Q for i in range(1, 6)
    )
    m = replace(
        original,
        public_key=commitments[0],
        coefficient_commitments=commitments,
        trustees=tuple(
            TrusteePublic(i, f"{i:040x}", hx(pow(2, values[i - 1], P))) for i in range(1, 6)
        ),
    )
    with pytest.raises(ValueError, match="degree two|subgroup"):
        make_partial(m, aggregate(m), Share(3, hx(values[2])))


def test_dealer_resamples_a_polynomial_with_public_zero_shares(monkeypatch):
    draws = iter((1, Q - 3, 0, 0, 0, 0))
    monkeypatch.setattr("trustees.dealer.secrets.randbelow", lambda _: next(draws))
    setup = create_dealer("12" * 32)
    assert tuple(int(s.value, 16) for s in setup.shares) == (2, 5, 10, 17, 26)
    assert all(key != hx(1) for key in setup.verification_keys)
