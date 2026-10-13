"""Named virtual subsets and search filters, sent as selection constraints.

A virtual subset is a definition, not a stored catalogue: structure patterns
(SMARTS with match counts) and RDKit property conditions. Searching a subset sends
one ``molecule-selection/1`` document with a similarity objective plus these
constraints, so the server checks every returned molecule exactly. Nothing is
filtered on the client.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Short names accepted by ``--where``/``where=``, mapped to the server's property IDs
# and the units it requires (molecule-selection/1 PROPERTY_REGISTRY).
PROPERTIES: dict[str, tuple[str, str]] = {
    "mw": ("rdkit.mol_wt", "Da"),
    "logp": ("rdkit.crippen_logp", "1"),
    "hbd": ("rdkit.hbd", "count"),
    "hba": ("rdkit.hba", "count"),
    "rotb": ("rdkit.rotatable_bonds", "count"),
    "tpsa": ("rdkit.tpsa", "A^2"),
    "fsp3": ("rdkit.fraction_csp3", "1"),
    "arom": ("rdkit.aromatic_rings", "count"),
}
_PROPERTY_ALIASES = {
    **{name: name for name in PROPERTIES},
    **{property_id: name for name, (property_id, _) in PROPERTIES.items()},
    **{property_id.removeprefix("rdkit."): name for name, (property_id, _) in PROPERTIES.items()},
    "molwt": "mw",
    "rotatable_bonds": "rotb",
    "aromatic_rings": "arom",
}

METRICS = {
    "morgan": "rdkit.ecfp4_tanimoto",
    "shape": "cheese.shape",
    "esp": "cheese.electrostatic",
}


@dataclass(frozen=True)
class Pattern:
    """One SMARTS (or SMILES) pattern and how many times it must match."""

    smarts: str
    min_count: int = 1
    max_count: int | None = None
    format: str = "smarts"

    def __post_init__(self) -> None:
        if self.format not in {"smarts", "smiles"}:
            raise ValueError("pattern format must be 'smarts' or 'smiles'")
        if self.min_count < 0 or (self.max_count is not None and self.max_count < self.min_count):
            raise ValueError("pattern counts need 0 <= min_count <= max_count")


@dataclass(frozen=True)
class PropertyCondition:
    """One exact RDKit property condition on the assembled product."""

    property_id: str
    units: str
    operator: str
    value: float | tuple[float, float]

    def to_where(self) -> dict[str, Any]:
        value = list(self.value) if self.operator == "range" else self.value
        return {"units": self.units, self.operator: value}


@dataclass(frozen=True)
class Subset:
    """A named, versioned virtual subset of any chemical space."""

    name: str
    version: int
    description: str
    require: tuple[Pattern, ...] = ()
    require_any: tuple[Pattern, ...] = ()
    exclude: tuple[Pattern, ...] = ()
    presets: tuple[str, ...] = ()
    properties: tuple[PropertyCondition, ...] = ()
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}/v{self.version}"

    @property
    def structural(self) -> bool:
        return bool(self.require or self.require_any or self.exclude)

    def definition(self) -> str:
        parts = [f"contains {_count_text(p)}{p.smarts}" for p in self.require]
        if self.require_any:
            parts.append("contains any of " + ", ".join(p.smarts for p in self.require_any))
        parts += [f"does not contain {p.smarts}" for p in self.exclude]
        parts += [f"preset {preset}" for preset in self.presets]
        parts += [describe_condition(condition) for condition in self.properties]
        return "; ".join(parts)


def _count_text(pattern: Pattern) -> str:
    if pattern.min_count == 1 and pattern.max_count is None:
        return ""
    if pattern.max_count is None:
        return f">={pattern.min_count}x "
    if pattern.max_count == pattern.min_count:
        return f"{pattern.min_count}x "
    return f"{pattern.min_count}-{pattern.max_count}x "


def _condition(name: str, operator: str, value) -> PropertyCondition:
    property_id, units = PROPERTIES[name]
    return PropertyCondition(property_id, units, operator, value)


# An alpha-amino-acid residue with a free or esterified carboxyl. The nitrogen may
# be free, alkylated, acylated, carbamate-protected, sulfonylated or in a ring (Pro).
# This matches 98-100% of every residue shard of the Mcule amino-acid sets (MCULE-AA,
# 2026-08-01 snapshot) except Gly and Ala (~90%); the rest attach the residue through
# an aromatic ring nitrogen or a quaternary ammonium.
_ACID_OR_ESTER = "[CX3](=O)[OX2H1,OX1-,$([OX2][#6])]"
ALPHA_AMINO_ACID = f"[#7X3]-[CX4]-{_ACID_OR_ESTER}"
BETA_AMINO_ACID = f"[#7X3]-[CX4]-[CX4]-{_ACID_OR_ESTER}"
AMINO_ACID_BUILDING_BLOCK = (
    "[NX3;!$(N-C(=[O,S])[#6,#1]);!$(N-C(=O)N);!$(N-S(=O)=O)]-[CX4]-" + _ACID_OR_ESTER
)

SUBSETS: dict[str, Subset] = {
    subset.name: subset
    for subset in (
        Subset(
            "amino-acids",
            1,
            "Contains an alpha-amino-acid residue whose carboxyl is a free acid or an ester.",
            require=(Pattern(ALPHA_AMINO_ACID),),
            note="Same scope as the Mcule amino-acid catalogues. Natural vs unnatural and "
            "D vs L cannot be told apart, because matching ignores stereochemistry.",
        ),
        Subset(
            "amino-acid-building-blocks",
            1,
            "Alpha-amino acids ready for peptide coupling: N free, alkylated or carbamate-"
            "protected (Boc, Fmoc, Cbz); free acid or ester.",
            require=(Pattern(AMINO_ACID_BUILDING_BLOCK),),
        ),
        Subset(
            "beta-amino-acids",
            1,
            "Contains a beta-amino-acid residue whose carboxyl is a free acid or an ester.",
            require=(Pattern(BETA_AMINO_ACID),),
        ),
        Subset(
            "carboxylic-acids",
            1,
            "Contains a free carboxylic acid (or carboxylate).",
            require=(Pattern("[CX3](=O)[OX2H1,OX1-]"),),
        ),
        Subset(
            "primary-amines",
            1,
            "Contains a primary amine on carbon, aliphatic or aromatic (not an amide or "
            "sulfonamide NH2).",
            require=(Pattern("[NX3;H2;!$(NC=[O,S,N]);!$(NS(=O)=O)][#6]"),),
        ),
        Subset(
            "acrylamides",
            1,
            "Contains an acyclic acrylamide or beta-substituted acrylamide (covalent warhead).",
            require=(Pattern("[CX3;H2,H1;!R]=[CX3;!R]-[CX3](=O)[NX3]"),),
        ),
        Subset(
            "covalent-warheads",
            1,
            "Contains at least one common covalent warhead: acrylamide, chloroacetamide, "
            "vinyl sulfonamide, sulfonyl fluoride or propiolamide.",
            require_any=(
                Pattern("[CX3;H2,H1;!R]=[CX3;!R]-[CX3](=O)[NX3]"),
                Pattern("Cl[CH2]C(=O)[NX3]"),
                Pattern("[CH2]=[CH]S(=O)(=O)[NX3]"),
                Pattern("[SX4](=O)(=O)F"),
                Pattern("C#CC(=O)[NX3]"),
            ),
        ),
        Subset(
            "ro5",
            1,
            "Lipinski Rule of 5: MW <= 500 Da, cLogP <= 5, HBD <= 5, HBA <= 10.",
            presets=("lipinski-ro5/v1",),
        ),
        Subset(
            "druglike",
            1,
            "Navigator drug-like window (navigator-druglike-v1).",
            properties=(
                _condition("mw", "lte", 500),
                _condition("logp", "range", (-10, 5)),
                _condition("hba", "lte", 10),
                _condition("hbd", "lte", 5),
                _condition("rotb", "lte", 10),
                _condition("tpsa", "lte", 140),
                _condition("fsp3", "range", (0.1, 0.8)),
                _condition("arom", "range", (1, 5)),
            ),
        ),
        Subset(
            "beyond-ro5",
            1,
            "Beyond the Rule of 5: MW > 500 Da.",
            properties=(_condition("mw", "gt", 500),),
            note="Property conditions are combined with AND, so 'violates any one Lipinski "
            "rule' cannot be expressed; MW > 500 Da is used instead.",
        ),
        Subset(
            "ro3",
            1,
            "Fragment-like Rule of 3: MW <= 300 Da, cLogP <= 3, HBD <= 3, HBA <= 3, "
            "rotatable bonds <= 3.",
            properties=(
                _condition("mw", "lte", 300),
                _condition("logp", "lte", 3),
                _condition("hbd", "lte", 3),
                _condition("hba", "lte", 3),
                _condition("rotb", "lte", 3),
            ),
        ),
    )
}
SUBSET_ALIASES = {
    "amino-acid": "amino-acids",
    "aa": "amino-acids",
    "lipinski": "ro5",
    "fragments": "ro3",
}


def get_subset(name: str) -> Subset:
    """Return a registry subset by name, alias, or ``name/vN``."""

    key = name.strip().lower()
    base, _, version = key.partition("/v")
    base = SUBSET_ALIASES.get(base, base)
    subset = SUBSETS.get(base)
    if subset is None or (version and version != str(subset.version)):
        known = ", ".join(sorted(SUBSETS))
        raise ValueError(f"unknown subset {name!r}; known subsets: {known}")
    return subset


_OPERATORS = {"<=": "lte", "<": "lt", ">=": "gte", ">": "gt"}
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_SIMPLE = re.compile(rf"^\s*([A-Za-z_.]+)\s*(<=|>=|<|>)\s*({_NUMBER})\s*$")
_RANGE = re.compile(rf"^\s*([A-Za-z_.]+)\s*=\s*({_NUMBER})\s*\.\.\s*({_NUMBER})\s*$")
_BETWEEN = re.compile(rf"^\s*({_NUMBER})\s*<=\s*([A-Za-z_.]+)\s*<=\s*({_NUMBER})\s*$")


def property_definition(name: str) -> tuple[str, str]:
    """Return ``(property_id, units)`` for a short name or a server property ID."""

    short = _PROPERTY_ALIASES.get(name.strip().lower())
    if short is None:
        known = ", ".join(PROPERTIES)
        raise ValueError(f"unknown property {name!r}; use one of: {known} (or rdkit.* IDs)")
    return PROPERTIES[short]


def parse_condition(text: str) -> PropertyCondition:
    """Parse ``mw<=450``, ``tpsa=40..90`` or ``1<=hbd<=3`` into a property condition."""

    if match := _SIMPLE.match(text):
        name, operator, value = match.groups()
        property_id, units = property_definition(name)
        return PropertyCondition(property_id, units, _OPERATORS[operator], float(value))
    if match := _RANGE.match(text):
        name, lower, upper = match.groups()
    elif match := _BETWEEN.match(text):
        lower, name, upper = match.groups()
    else:
        raise ValueError(
            f"cannot read property filter {text!r}; write e.g. 'mw<=450', 'logp<4', "
            "'tpsa=40..90' or '1<=hbd<=3'"
        )
    if float(lower) > float(upper):
        raise ValueError(f"empty range in property filter {text!r}")
    property_id, units = property_definition(name)
    return PropertyCondition(property_id, units, "range", (float(lower), float(upper)))


def describe_condition(condition: PropertyCondition) -> str:
    short = _PROPERTY_ALIASES.get(condition.property_id, condition.property_id)
    if condition.operator == "range":
        lower, upper = condition.value  # type: ignore[misc]
        return f"{_number(lower)} <= {short} <= {_number(upper)}"
    symbol = {value: key for key, value in _OPERATORS.items()}[condition.operator]
    return f"{short} {symbol} {_number(condition.value)}"


def _number(value) -> str:
    return f"{float(value):g}"


def validate_smarts(smarts: str, *, format: str = "smarts") -> None:
    """Reject an unreadable pattern before any request, when RDKit is installed.

    The API checks patterns only when it runs the search, so a typo would otherwise
    fail after the request was made. Without RDKit this check is skipped.
    """

    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        return
    RDLogger.DisableLog("rdApp.*")
    parsed = Chem.MolFromSmarts(smarts) if format == "smarts" else Chem.MolFromSmiles(smarts)
    if parsed is None:
        raise ValueError(f"invalid {format.upper()} pattern: {smarts!r}")


def _patterns(value) -> tuple[Pattern, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, Pattern)):
        value = [value]
    output = []
    for item in value:
        if isinstance(item, Pattern):
            output.append(item)
        elif isinstance(item, str):
            output.append(Pattern(item))
        elif isinstance(item, Sequence) and 2 <= len(item) <= 3:
            smarts, minimum, *maximum = item
            output.append(Pattern(smarts, int(minimum), int(maximum[0]) if maximum else None))
        else:
            raise ValueError(f"cannot read structure pattern {item!r}")
    return tuple(output)


def _conditions(value) -> tuple[PropertyCondition, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, PropertyCondition)):
        value = [value]
    if isinstance(value, Mapping):
        value = [f"{name}{expression}" for name, expression in value.items()]
    return tuple(
        item if isinstance(item, PropertyCondition) else parse_condition(str(item))
        for item in value
    )


def _names(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(get_subset(name).name for name in value)


@dataclass(frozen=True)
class Filters:
    """Subsets, property conditions and ad-hoc patterns for one search or sample.

    All parts are combined with AND. ``require`` patterns must each match (at least
    once, or within their counts); ``exclude`` patterns must not match at all.
    """

    subsets: tuple[str, ...] = ()
    where: tuple[PropertyCondition, ...] = ()
    require: tuple[Pattern, ...] = ()
    exclude: tuple[Pattern, ...] = ()

    @property
    def _resolved(self) -> tuple[Subset, ...]:
        return tuple(get_subset(name) for name in self.subsets)

    @classmethod
    def build(cls, *, subset=None, where=None, require=None, exclude=None) -> Filters:
        names = _names(subset)
        return cls(
            subsets=names,
            where=_conditions(where),
            require=_patterns(require),
            exclude=_patterns(exclude),
        )

    def __bool__(self) -> bool:
        return bool(self.subsets or self.where or self.require or self.exclude)

    @property
    def structural(self) -> bool:
        return bool(self.require or self.exclude) or any(s.structural for s in self._resolved)

    def validate(self) -> None:
        for subset in self._resolved:
            for pattern in (*subset.require, *subset.require_any, *subset.exclude):
                validate_smarts(pattern.smarts, format=pattern.format)
        for pattern in (*self.require, *self.exclude):
            validate_smarts(pattern.smarts, format=pattern.format)

    def apply(self, selection):
        """Return ``selection`` with every filter added as a constraint."""

        for subset in self._resolved:
            selection = selection.subset(subset.name)
        for condition in self.where:
            selection = selection.where(condition)
        for index, pattern in enumerate(self.require, 1):
            selection = selection.require_structure(
                pattern.smarts,
                min_count=pattern.min_count,
                max_count=pattern.max_count,
                format=pattern.format,
                id=f"require-{index}",
            )
        for index, pattern in enumerate(self.exclude, 1):
            selection = selection.exclude_structure(
                pattern.smarts, format=pattern.format, id=f"exclude-{index}"
            )
        return selection

    def describe(self) -> list[str]:
        lines = [f"{s.name}: {s.description}" for s in self._resolved]
        lines += [describe_condition(condition) for condition in self.where]
        lines += [f"contains {_count_text(p)}{p.smarts}" for p in self.require]
        lines += [f"does not contain {p.smarts}" for p in self.exclude]
        return lines


def split_database(database: str) -> tuple[str, tuple[str, ...]]:
    """Split ``synple:amino-acids,ro5`` into the database and its subset names."""

    name, separator, subsets = database.partition(":")
    if not separator:
        return database, ()
    names = tuple(part for part in re.split(r"[,+]", subsets) if part.strip())
    if not name or not names:
        raise ValueError(f"write a subset after the database as DATABASE:SUBSET, got {database!r}")
    return name, tuple(get_subset(part).name for part in names)


def merge_subsets(*groups: Iterable[str] | str | None) -> list[str] | None:
    names: list[str] = []
    for group in groups:
        if group is None:
            continue
        for name in [group] if isinstance(group, str) else group:
            if name not in names:
                names.append(name)
    return names or None


# The API returns at most this many ranked hits for one constrained selection.
MAX_FILTERED_LIMIT = 200


def search_filters(
    database: str, *, subset=None, where=None, require=None, exclude=None
) -> tuple[str, Filters]:
    """Resolve ``DATABASE:SUBSET`` and keyword filters into a database and filters."""

    database, named = split_database(database)
    filters = Filters.build(
        subset=merge_subsets(named, subset), where=where, require=require, exclude=exclude
    )
    return database, filters


def _include(filters: Filters, include_synthons: bool) -> list[str]:
    include = ["synthons"] if include_synthons else []
    has_properties = bool(filters.where) or any(
        s.presets or s.properties for s in filters._resolved
    )
    if has_properties:
        include.append("properties")
    return include


def similarity_selection(
    smiles: str,
    *,
    database: str,
    filters: Filters,
    method: str = "morgan",
    limit: int = 20,
    include_synthons: bool = False,
):
    """A ranked similarity selection restricted by ``filters``."""

    from .selection import Selection

    if method not in METRICS:
        raise ValueError("method must be one of: 'morgan', 'shape', 'esp'")
    if not 1 <= limit <= MAX_FILTERED_LIMIT:
        raise ValueError(f"a filtered search returns 1 to {MAX_FILTERED_LIMIT} hits")
    filters.validate()
    selection = (
        Selection.from_database(database)
        .reference("query", smiles=smiles)
        .maximize_similarity(METRICS[method], reference="query")
        .limit(limit)
    )
    selection = filters.apply(selection)
    include = _include(filters, include_synthons)
    return selection.include(*include) if include else selection


def sample_selection(
    *,
    database: str,
    filters: Filters,
    count: int = 100,
    seed: int | None = None,
    include_synthons: bool = False,
):
    """A random sample restricted by property filters."""

    from .selection import Selection

    if filters.structural:
        raise ValueError(
            "sampling with structure filters is not available: the API applies structure "
            "constraints only to similarity searches. Use property subsets (ro5, druglike, "
            "beyond-ro5, ro3) or property filters when sampling."
        )
    filters.validate()
    selection = filters.apply(Selection.from_database(database).sample(seed=seed).limit(count))
    include = _include(filters, include_synthons)
    return selection.include(*include) if include else selection
