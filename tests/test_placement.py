"""Mapping a product back onto its synthons.

Fixtures are real rows captured from the service on 2026-10-19, trimmed to the fields
this reads, so the parsing is exercised against the shape actually served.
"""

import dataclasses

import pytest

Chem = pytest.importorskip("rdkit.Chem")

from deepmedchem.models import Hit  # noqa: E402
from deepmedchem.placement import Placement, SynthonMap, synthon_map  # noqa: E402

AMIDE = "[CX3](=[OX1])[NX3]"
#: Seven connected heavy atoms: long enough to reach across two junctions, which a
#: three-atom motif never can.
LINKER = "[!#1]~[!#1]~[!#1]~[!#1]~[!#1]~[!#1]~[!#1]"

TWO_SYNTHON = {
    "smiles": "CC(=O)Nc1ccc(-c2ccc(O)cc2)cc1",
    "rank": 1,
    "catalog_id": "s_271570____17825444____12089798",
    "synthons": [
        {"slot": 1, "smiles": "CC(=O)Nc1cc[c]([U])cc1"},
        {"slot": 2, "smiles": "Oc1cc[c]([U])cc1"},
    ],
}

THREE_SYNTHON = {
    "smiles": "CC(=O)Nc1ccc(C(=O)NCCNC(=O)c2ccc(O)cc2)cc1",
    "rank": 1,
    "synthons": [
        {"slot": 0, "smiles": "[U]NCCN[Np]"},
        {"slot": 1, "smiles": "CC(=O)Nc1ccc(C(=O)[U])cc1"},
        {"slot": 2, "smiles": "O=C([Np])c1ccc(O)cc1"},
    ],
}

#: A space whose markers are consumed by a reaction template: [U] appears three times
#: and [Np] marks a double bond, so the parts cannot be joined pairwise.
UNSUPPORTED = {
    "smiles": "CC(=O)Nc1ccc(C(=O)Nc2ccc(C(=O)Nc3ccc(O)cc3)cc2)cc1",
    "rank": 1,
    "synthons": [
        {"slot": 0, "smiles": "[U][NH]C1=CC=C([C](=[Np])[Pu])C=C1"},
        {"slot": 1, "smiles": "OC1=CC=C([NH][U])C=C1"},
        {"slot": 2, "smiles": "CC(=O)NC1=CC=C(C(=O)[U])C=C1"},
    ],
}


def test_a_two_synthon_product_is_rebuilt_exactly_with_one_assembly_bond():
    mapped = synthon_map(TWO_SYNTHON)
    assert isinstance(mapped, SynthonMap)
    assert Chem.MolToSmiles(mapped.product) == Chem.CanonSmiles(TWO_SYNTHON["smiles"])
    assert len(mapped.assembly_bonds) == 1
    assert set(mapped.slots) == {1, 2}
    assert len(mapped.slots) == mapped.product.GetNumAtoms()


def test_a_three_synthon_product_carries_two_assembly_bonds():
    mapped = synthon_map(THREE_SYNTHON)
    assert Chem.MolToSmiles(mapped.product) == Chem.CanonSmiles(THREE_SYNTHON["smiles"])
    assert len(mapped.assembly_bonds) == 2
    assert set(mapped.slots) == {0, 1, 2}


def test_an_amide_inside_one_synthon_is_told_from_one_formed_at_a_junction():
    """The distinction the hit alone cannot make: both molecules contain an amide."""
    mapped = synthon_map(THREE_SYNTHON)
    spans = sorted(match.junctions for match in mapped.placements(AMIDE))
    assert spans == [0, 1, 1], "the acetamido amide is internal; the two couplings are not"

    internal = [m for m in mapped.placements(AMIDE) if m.within_one_synthon]
    assert len(internal) == 1 and len(internal[0].slots) == 1
    for match in mapped.placements(AMIDE):
        if not match.within_one_synthon:
            assert len(match.slots) == 2
            assert len(match.bonds) == 1


def test_a_long_motif_spans_both_junctions_where_a_short_one_cannot():
    mapped = synthon_map(THREE_SYNTHON)
    assert max(match.junctions for match in mapped.placements(AMIDE)) == 1
    assert max(match.junctions for match in mapped.placements(LINKER)) == 2


def test_every_reported_bond_is_a_real_assembly_bond_the_match_contains():
    mapped = synthon_map(THREE_SYNTHON)
    for match in mapped.placements(LINKER):
        for first, second in match.bonds:
            assert (first, second) in mapped.assembly_bonds
            assert first in match.atoms and second in match.atoms
            assert mapped.product.GetBondBetweenAtoms(first, second) is not None
        assert match.slots == tuple(sorted({mapped.slot_of(i) for i in match.atoms}))


#: A ring closed by BOTH assembly bonds. Any four-atom match contains every atom, so
#: counting bonds by "both endpoints matched" cannot tell a path from the whole ring.
CYCLOBUTANE = {
    "smiles": "C1CCC1",
    "rank": 1,
    "synthons": [
        {"slot": 0, "smiles": "[U]CC[Np]"},
        {"slot": 1, "smiles": "[U]CC[Np]"},
    ],
}


def test_only_assembly_bonds_the_match_traverses_are_counted():
    mapped = synthon_map(CYCLOBUTANE)
    assert len(mapped.assembly_bonds) == 2

    paths = mapped.placements("[C]-[C]-[C]-[C]")
    assert all(path.atoms == (0, 1, 2, 3) for path in paths)
    for path in paths:
        assert all(bond in mapped.assembly_bonds for bond in path.bonds)
        assert path.junctions == len(path.bonds)

    # Closing the query into a ring must traverse both.
    ring = mapped.placements("[C]1-[C]-[C]-[C]-1")
    assert [placement.junctions for placement in ring] == [2]
    assert set(ring[0].bonds) == set(mapped.assembly_bonds)


def test_every_embedding_is_reported_not_one_per_atom_set():
    """RDKit's default uniquify keeps one match per atom set. Around a ring the same
    four atoms are covered by paths running over different bonds -- and over different
    numbers of assembly bonds -- so collapsing by atoms hides real placements."""
    mapped = synthon_map(CYCLOBUTANE)
    spans = sorted(path.junctions for path in mapped.placements("[C]-[C]-[C]-[C]"))
    assert spans == [1, 1, 2, 2], "both the one-junction and two-junction paths exist"

    # Identity is the set of bonds the embedding covers, so the same path found from
    # either end collapses to one placement: RDKit enumerates eight, four remain. Two
    # distinct paths may still cross the same pair of assembly bonds, so the crossed
    # bonds alone are not the identity.
    pattern = Chem.MolFromSmarts("[C]-[C]-[C]-[C]")
    raw = mapped.product.GetSubstructMatches(pattern, uniquify=False)
    assert len(raw) == 8
    assert len(mapped.placements("[C]-[C]-[C]-[C]")) == 4


@pytest.mark.parametrize(
    "synthons",
    [
        # no slot: defaulting it would attribute the whole product to one synthon
        [{"smiles": "CC(=O)Nc1cc[c]([U])cc1"}, {"smiles": "Oc1cc[c]([U])cc1"}],
        # the same slot twice
        [
            {"slot": 1, "smiles": "CC(=O)Nc1cc[c]([U])cc1"},
            {"slot": 1, "smiles": "Oc1cc[c]([U])cc1"},
        ],
        # slot that is not an integer
        [
            {"slot": "first", "smiles": "CC(=O)Nc1cc[c]([U])cc1"},
            {"slot": 2, "smiles": "Oc1cc[c]([U])cc1"},
        ],
        [
            {"slot": True, "smiles": "CC(=O)Nc1cc[c]([U])cc1"},
            {"slot": 2, "smiles": "Oc1cc[c]([U])cc1"},
        ],
        # smiles that is not a string
        [{"slot": 1, "smiles": 123}, {"slot": 2, "smiles": "Oc1cc[c]([U])cc1"}],
        [{"slot": 1, "smiles": None}, {"slot": 2, "smiles": "Oc1cc[c]([U])cc1"}],
    ],
)
def test_provenance_is_refused_when_the_slots_are_not_trustworthy(synthons):
    """These rebuild the correct molecule but cannot say which part came from where.
    The right structure with the wrong provenance is worse than no answer."""
    assert synthon_map(dict(TWO_SYNTHON, synthons=synthons)) is None


@pytest.mark.parametrize(
    "served,synthons",
    [
        # both markers on the same atom in both parts: joining them twice would be the
        # same bond, which RDKit rejects outright
        ("C1CCC1", [{"slot": 0, "smiles": "C([U])[Np]"}, {"slot": 1, "smiles": "C([U])[Np]"}]),
        # one marker used three times, two of them sharing an attachment atom. The
        # served SMILES matches what the surviving pair would build, so only counting
        # the marker atoms themselves catches it: tagging the attachment atom cannot,
        # because the second tag overwrites the first and the third marker vanishes.
        ("CC", [{"slot": 0, "smiles": "C([U])[U]"}, {"slot": 1, "smiles": "C[U]"}]),
        # a marker paired inside one part rather than across two
        ("C1CC1", [{"slot": 0, "smiles": "C([U])C[U]"}, {"slot": 1, "smiles": "C([Np])C[Np]"}]),
        # a marker with no partner
        ("CC", [{"slot": 0, "smiles": "[U]CC[Np]"}, {"slot": 1, "smiles": "[U]CC"}]),
    ],
)
def test_a_degenerate_decomposition_returns_none_rather_than_raising(served, synthons):
    assert synthon_map({"smiles": served, "rank": 1, "synthons": synthons}) is None


def test_each_marker_names_one_bond_between_two_different_parts():
    """Valid decompositions are unaffected by that check: two and three synthons."""
    assert len(synthon_map(CYCLOBUTANE).assembly_bonds) == 2
    assert len(synthon_map(TWO_SYNTHON).assembly_bonds) == 1
    assert len(synthon_map(THREE_SYNTHON).assembly_bonds) == 2


def test_a_disconnected_motif_is_refused():
    """A match spread over separate pieces has no single path across the assembly, so
    'how many junctions does it cross' has no answer."""
    mapped = synthon_map(TWO_SYNTHON)
    with pytest.raises(ValueError, match="connected motif"):
        mapped.placements("[C].[O]")


def test_a_space_whose_parts_do_not_pair_reports_nothing_rather_than_guessing():
    assert synthon_map(UNSUPPORTED) is None


@pytest.mark.parametrize(
    "hit",
    [
        {"smiles": "CCO", "rank": 1},  # synthons not requested
        {"smiles": "CCO", "rank": 1, "synthons": []},
        {"smiles": "CCO", "rank": 1, "synthons": [{"slot": 1, "smiles": "C[U]"}]},
        {"smiles": "CCO", "rank": 1, "synthons": [{"slot": 1}, {"slot": 2}]},
        {"smiles": "CCO", "rank": 1, "synthons": "nonsense"},
        # parts with no marker at all cannot be joined
        {
            "smiles": "CCO",
            "rank": 1,
            "synthons": [{"slot": 1, "smiles": "C"}, {"slot": 2, "smiles": "O"}],
        },
    ],
)
def test_incomplete_or_malformed_rows_return_none(hit):
    assert synthon_map(hit) is None


def test_a_rebuild_that_does_not_reproduce_the_served_structure_is_refused():
    """The served SMILES is the authority: if the parts rebuild something else, the
    mapping is wrong and must not be offered."""
    tampered = dict(TWO_SYNTHON, smiles="CCOCC")
    assert synthon_map(tampered) is None


def test_a_typed_hit_works_as_well_as_a_plain_row():
    mapped = synthon_map(Hit(**TWO_SYNTHON))
    assert mapped is not None
    assert len(mapped.assembly_bonds) == 1


def test_an_invalid_smarts_is_refused():
    mapped = synthon_map(TWO_SYNTHON)
    with pytest.raises(ValueError, match="invalid SMARTS"):
        mapped.placements("[not-a-query")


def test_a_motif_absent_from_the_product_yields_no_placements():
    assert synthon_map(TWO_SYNTHON).placements("[Si]") == []


def test_placement_is_immutable_and_self_consistent():
    match = synthon_map(TWO_SYNTHON).placements(AMIDE)[0]
    assert isinstance(match, Placement)
    assert match.junctions == len(match.bonds)
    assert match.within_one_synthon == (not match.bonds)
    with pytest.raises(dataclasses.FrozenInstanceError):
        match.atoms = ()
