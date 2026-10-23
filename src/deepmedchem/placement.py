"""Where a motif sits in an assembled product: inside one synthon, or across a junction.

A result says which molecules matched, never which part of the molecule came from which
synthon, so a question like "is this amide formed at the coupling, or was it already in
the building block?" cannot be answered from the hit alone. It can be answered from the
hit *plus* its decomposition: request ``include=["synthons"]`` and each row carries its
synthon SMILES with their attachment markers intact. Rebuilding the product from those
parts recovers every atom's slot, and the bonds formed to join them.

    result = client.search(..., include_synthons=True)
    for hit in result.hits:
        mapped = synthon_map(hit)
        if mapped is None:
            continue                     # this space's parts do not pair up; see below
        for match in mapped.placements("[CX3](=[OX1])[NX3]"):
            match.junctions              # 0 inside one synthon, 1 across a junction, 2 across two

This is classification after the fact, not a constraint: a search cannot be asked for a
placement, only for the motif. Filtering the results narrows them -- it does not make the
service return more.

The rebuild is verified against the structure the service returned, so a space whose
parts this cannot join reports ``None`` rather than a confident wrong answer. Spaces whose
markers are consumed by a reaction template, rather than bonded in pairs, are not
supported: the template is not part of the response.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: [U], [Np], [Pu], [Am]: attachment markers, paired by element across the tuple.
_MARKERS = {92, 93, 94, 95}


def _require_rdkit():
    try:
        from rdkit import Chem
    except ImportError as error:
        raise ImportError(
            "Mapping a product onto its synthons requires RDKit. Install it with "
            "`python -m pip install rdkit` (or `pip install 'deepmedchem[rdkit]'`)."
        ) from error
    return Chem


@dataclass(frozen=True)
class Placement:
    """One embedding of a motif, and how it sits across the assembly."""

    atoms: tuple[int, ...]
    slots: tuple[int, ...]
    bonds: tuple[tuple[int, int], ...]

    @property
    def junctions(self) -> int:
        """Assembly bonds this match traverses: 0 lies inside one synthon."""
        return len(self.bonds)

    @property
    def within_one_synthon(self) -> bool:
        """Whether every matched atom came from the same synthon."""
        return len(self.slots) == 1


@dataclass(frozen=True)
class SynthonMap:
    """A rebuilt product, with each atom's synthon slot and the bonds that joined them."""

    product: Any
    slots: tuple[int, ...]
    assembly_bonds: tuple[tuple[int, int], ...]

    def slot_of(self, atom_index: int) -> int:
        return self.slots[atom_index]

    def placements(self, smarts: str) -> list[Placement]:
        """Every embedding of ``smarts``, ordered as RDKit finds them.

        An assembly bond counts only when the match actually **traverses** it. Both its
        atoms being present is not enough: in a ring closed by two assembly bonds, a
        path through four atoms contains all of them while running over only one.
        """
        Chem = _require_rdkit()
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is None:
            raise ValueError(f"invalid SMARTS: {smarts!r}")
        if len(Chem.GetMolFrags(pattern)) != 1:
            raise ValueError(
                f"placement needs a connected motif; {smarts!r} is disconnected. "
                "Query each fragment separately: a match spread over several pieces "
                "has no single path across the assembly."
            )
        query_bonds = [
            (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()) for bond in pattern.GetBonds()
        ]
        found = []
        seen = set()
        # uniquify would keep one embedding per atom set, and in a ring the same atoms
        # can be covered by paths that run over different bonds -- including a different
        # number of assembly bonds. Enumerate every embedding and make the edges, not
        # the atoms, the identity of a placement.
        for match in self.product.GetSubstructMatches(pattern, uniquify=False):
            traversed = frozenset(
                frozenset((match[first], match[second])) for first, second in query_bonds
            )
            if traversed in seen:
                continue
            seen.add(traversed)
            atoms = set(match)
            crossed = tuple(bond for bond in self.assembly_bonds if frozenset(bond) in traversed)
            found.append(
                Placement(
                    atoms=tuple(sorted(atoms)),
                    slots=tuple(sorted({self.slots[index] for index in atoms})),
                    bonds=crossed,
                )
            )
        return found


def _synthons(hit: Any) -> list[tuple[int, str]] | None:
    """``(slot, smiles)`` per part, or None if the decomposition is unusable.

    Every part needs its own slot. Defaulting a missing one would rebuild the right
    molecule while attributing all of it to a single synthon -- the right structure
    with the wrong provenance, which is worse than refusing.
    """
    raw = getattr(hit, "raw", None)
    source = raw if isinstance(raw, Mapping) else hit
    if not isinstance(source, Mapping):
        return None
    parts = source.get("synthons")
    if not isinstance(parts, list) or len(parts) < 2:
        return None
    resolved = []
    for part in parts:
        if not isinstance(part, Mapping):
            return None
        smiles, slot = part.get("smiles"), part.get("slot")
        if not isinstance(smiles, str) or not smiles:
            return None
        if isinstance(slot, bool) or not isinstance(slot, int):
            return None
        resolved.append((slot, smiles))
    if len({slot for slot, _ in resolved}) != len(resolved):
        return None
    return resolved


def _assemble(parts, Chem):
    combined = None
    # One entry per marker ATOM, so a marker repeated on a single attachment atom is
    # still counted twice. Tagging the atom cannot see that: the second tag overwrites
    # the first and the extra marker disappears.
    marker_slots: dict[int, list[int]] = {}
    for slot, smiles in sorted(parts):
        # The parts carry attachment markers, so they are fragments rather than valid
        # molecules until joined; sanitizing now would reject them.
        molecule = Chem.MolFromSmiles(smiles, sanitize=False)
        if molecule is None:
            return None
        for atom in molecule.GetAtoms():
            atom.SetIntProp("slot", slot)
        markers = [atom for atom in molecule.GetAtoms() if atom.GetAtomicNum() in _MARKERS]
        if not markers:
            return None
        for marker in markers:
            neighbours = marker.GetNeighbors()
            if len(neighbours) != 1:
                return None
            marker_slots.setdefault(marker.GetAtomicNum(), []).append(slot)
            # Deliberately not "_attach": RDKit treats property names beginning with an
            # underscore as private and omits them from GetPropNames, which would lose
            # these silently and leave the pieces unjoined.
            neighbours[0].SetIntProp(f"attach{marker.GetAtomicNum()}", 1)
        editable = Chem.RWMol(molecule)
        for index in sorted((marker.GetIdx() for marker in markers), reverse=True):
            editable.RemoveAtom(index)
        piece = editable.GetMol()
        combined = piece if combined is None else Chem.CombineMols(combined, piece)

    # A marker names one bond, so it must occur exactly twice, once in each of two
    # different parts. Three of a kind, a lone one, or a pair inside a single part is
    # not a decomposition this can join.
    for slots_seen in marker_slots.values():
        if len(slots_seen) != 2 or slots_seen[0] == slots_seen[1]:
            return None

    editable = Chem.RWMol(combined)
    groups: dict[int, list[int]] = {}
    for atom in editable.GetAtoms():
        for name in atom.GetPropNames(includePrivate=True):
            if name.startswith("attach"):
                groups.setdefault(int(name[len("attach") :]), []).append(atom.GetIdx())
    bonds = []
    for _, atoms in sorted(groups.items()):
        if len(atoms) != 2:
            return None  # markers are not a matched pair: not a space this can rebuild
        first, second = atoms
        # Two markers can land on one atom, or two markers can name the same pair of
        # atoms. RDKit refuses a self-bond or a second bond between the same atoms with
        # a hard error, so catch the degenerate decomposition before asking.
        if first == second or editable.GetBondBetweenAtoms(first, second) is not None:
            return None
        editable.AddBond(first, second, Chem.BondType.SINGLE)
        bonds.append((first, second))
    product = editable.GetMol()
    try:
        Chem.SanitizeMol(product)
    except Exception:
        return None
    return product, bonds


def synthon_map(hit: Any) -> SynthonMap | None:
    """Rebuild one hit from its synthons, or ``None`` if it cannot be rebuilt exactly.

    ``None`` means the decomposition is absent (request ``include=["synthons"]``), the
    parts do not pair up, or the rebuilt structure differs from the one served. It never
    means "no placement": an unsupported space is reported, not guessed at.
    """
    parts = _synthons(hit)
    if parts is None:
        return None
    Chem = _require_rdkit()
    built = _assemble(parts, Chem)
    if built is None:
        return None
    product, bonds = built
    raw = getattr(hit, "raw", None)
    source = raw if isinstance(raw, Mapping) else hit
    served = source.get("smiles")
    if not served:
        return None
    try:
        if Chem.MolToSmiles(product) != Chem.CanonSmiles(served):
            return None
    except Exception:
        return None
    return SynthonMap(
        product=product,
        slots=tuple(atom.GetIntProp("slot") for atom in product.GetAtoms()),
        assembly_bonds=tuple(bonds),
    )
