"""Versioned, chemistry-free structure query schema (also shipped by the SDK).

No chemistry is compiled here. Keep this module identical to deepmedchem.query_schema;
contract tests compare the JSON schema. Matching lives in chemical_query.py.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueryValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Contains(QueryValue):
    type: Literal["contains"] = "contains"
    smarts: str | None = Field(default=None, min_length=1, max_length=2048)
    smiles: str | None = Field(default=None, min_length=1, max_length=2048)
    pattern_id: str | None = Field(default=None, min_length=1, max_length=256)
    min_count: int = Field(default=1, strict=True, ge=0, le=1000)
    max_count: int | None = Field(default=None, strict=True, ge=0, le=1000)

    @model_validator(mode="after")
    def source_and_bounds(self):
        if sum(x is not None for x in (self.smarts, self.smiles, self.pattern_id)) != 1:
            raise ValueError("provide exactly one of smarts, smiles or pattern_id")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("max_count must be at least min_count")
        return self


class AllOf(QueryValue):
    type: Literal["all"] = "all"
    conditions: tuple[QueryNode, ...] = Field(min_length=1, max_length=32)


class AnyOf(QueryValue):
    type: Literal["any"] = "any"
    conditions: tuple[QueryNode, ...] = Field(min_length=1, max_length=32)


class AtLeast(QueryValue):
    type: Literal["at_least"] = "at_least"
    minimum: int = Field(strict=True, ge=1, le=32)
    conditions: tuple[QueryNode, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def attainable(self):
        if self.minimum > len(self.conditions):
            raise ValueError("minimum exceeds the number of conditions")
        return self


class Not(QueryValue):
    type: Literal["not"] = "not"
    condition: QueryNode


class Linker(QueryValue):
    """A path between mapped atoms of a SMARTS scaffold.

    Scaffold atoms other than the endpoints cannot occur inside the path. Length
    counts internal atoms by default. 'any' is existence, not shortest distance.
    """

    type: Literal["linker"] = "linker"
    scaffold: str = Field(min_length=1, max_length=2048)
    start: int = Field(strict=True, ge=1)
    end: int = Field(strict=True, ge=1)
    min_length: int = Field(default=0, strict=True, ge=0, le=32)
    max_length: int = Field(default=10, strict=True, ge=0, le=32)
    length_unit: Literal["internal_atoms", "bonds"] = "internal_atoms"
    path: Literal["any", "shortest"] = "any"
    atom_smarts: str = Field(default="*", min_length=1, max_length=256)
    bond_types: tuple[Literal["single", "double", "triple", "aromatic"], ...] = (
        "single",
        "double",
        "triple",
        "aromatic",
    )
    allow_ring_atoms: bool = True

    @model_validator(mode="after")
    def endpoints_and_bounds(self):
        if self.start == self.end:
            raise ValueError("linker endpoints must differ")
        if self.max_length < self.min_length or not self.bond_types:
            raise ValueError("invalid linker length range or empty bond_types")
        return self


class RGroup(QueryValue):
    """A pendant substituent at a mapped scaffold atom.

    smarts matches from its first atom at the attachment; it is a substituent
    pattern, not a whole-product condition. Equal labels require identical rooted
    substituent multisets (including hydrogen/absence). Bridging groups use Linker.
    """

    site: int = Field(strict=True, ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=64)
    smarts: str = Field(default="[!#1]", min_length=1, max_length=2048)
    allow_hydrogen: bool = False
    min_count: int = Field(default=1, strict=True, ge=0, le=8)
    max_count: int = Field(default=1, strict=True, ge=0, le=8)

    @model_validator(mode="after")
    def counts(self):
        if self.max_count < self.min_count:
            raise ValueError("R-group max_count must be at least min_count")
        return self


class RGroups(QueryValue):
    type: Literal["r_groups"] = "r_groups"
    scaffold: str = Field(min_length=1, max_length=2048)
    groups: tuple[RGroup, ...] = Field(min_length=1, max_length=16)
    exact: bool = True

    @model_validator(mode="after")
    def unique_sites(self):
        sites = [group.site for group in self.groups]
        if len(set(sites)) != len(sites):
            raise ValueError("each R-group site must occur once")
        return self


class MatchRelation(QueryValue):
    """Existence of two embeddings with a stated relation.

    connected means disjoint matches joined by a direct bond. shared_site binds
    the mapped atoms named by left_site/right_site to the same product atom.
    Counts belong in separate Contains conditions; relation operands have count=1.
    """

    type: Literal["match_relation"] = "match_relation"
    left: Contains
    right: Contains
    relation: Literal["disjoint", "overlap", "connected", "shared_site"]
    left_site: int | None = Field(default=None, strict=True, ge=1)
    right_site: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def sites(self):
        if any(x.min_count != 1 or x.max_count is not None for x in (self.left, self.right)):
            raise ValueError("relation operands must use the default occurrence bounds")
        supplied = self.left_site is not None and self.right_site is not None
        if self.relation == "shared_site":
            if not supplied:
                raise ValueError("shared_site requires left_site and right_site")
        elif self.left_site is not None or self.right_site is not None:
            raise ValueError("site arguments are only used with shared_site")
        return self


QueryNode = Annotated[
    Union[Contains, AllOf, AnyOf, AtLeast, Not, Linker, RGroups, MatchRelation],  # noqa: UP007
    Field(discriminator="type"),
]


class StructureQuery(QueryValue):
    schema_version: Literal["structure-query/1"] = "structure-query/1"
    expression: QueryNode
    stereo: Literal["strict", "ignore"] = "strict"

    @model_validator(mode="before")
    @classmethod
    def bound_document(cls, value):
        # Bound the raw tree before recursive model parsing (including malicious depth).
        if not isinstance(value, dict):
            return value
        stack = [(value.get("expression"), 0)]
        nodes = 0
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > 128 or depth > 12:
                raise ValueError("structure query exceeds 128 nodes or 12 levels")
            if isinstance(node, QueryValue):
                node = node.model_dump()
            if isinstance(node, dict):
                children = node.get("conditions", ())
                if isinstance(children, (tuple, list)):  # noqa: UP038 (Python 3.9 contract)
                    stack.extend((child, depth + 1) for child in children)
                if "condition" in node:
                    stack.append((node["condition"], depth + 1))
        return value


for _model in (AllOf, AnyOf, AtLeast, Not, StructureQuery):
    _model.model_rebuild()


def as_structure_query(value):
    """Wrap a native expression, or validate a serialized versioned document."""
    if isinstance(value, StructureQuery):
        return value
    if isinstance(value, QueryValue):
        return StructureQuery(expression=value)
    return StructureQuery.model_validate(value)


def combine_queries(left, right):
    """Conjoin documents without silently changing their stereo policy."""
    right = as_structure_query(right)
    if left is None:
        return right
    left = as_structure_query(left)
    if left.stereo != right.stereo:
        raise ValueError("combined queries must use the same stereo policy")
    return StructureQuery(
        expression=AllOf(conditions=(left.expression, right.expression)), stereo=left.stereo
    )
