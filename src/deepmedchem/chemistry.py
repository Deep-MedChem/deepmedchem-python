"""Immutable, validated chemistry constraints and optimization objectives.

These describe user intent. Explicit serializers translate them to the service's
JSON schema; no chemistry toolkit or network access is needed to construct them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .subsets import property_definition

__all__ = [
    "Constraint",
    "Constraints",
    "ExcludeStructure",
    "ExternalScore",
    "Maximize",
    "Minimize",
    "Objective",
    "Objectives",
    "PredictedProperty",
    "PredictedPropertyRange",
    "PropertyRange",
    "RequireAnyStructure",
    "RequireStructure",
    "Scale",
    "Similarity",
]


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def _nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("identifiers, queries and units must not be blank")
        return value


class Scale(_Value):
    """Finite normalization bounds, not eligibility limits; lower must be below upper."""

    lower: float = Field(strict=True)
    upper: float = Field(strict=True)

    @model_validator(mode="after")
    def _ordered(self):
        if self.lower >= self.upper:
            raise ValueError("scale lower must be less than upper")
        return self


class _Range(_Value):
    min: float | None = Field(default=None, strict=True)
    max: float | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def _ordered(self):
        if self.min is None and self.max is None:
            raise ValueError("provide min, max, or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not exceed max")
        return self

    def _bounds(self) -> dict[str, Any]:
        if self.min is None:
            return {"operator": "lte", "value": self.max}
        if self.max is None:
            return {"operator": "gte", "value": self.min}
        return {"operator": "range", "value": {"lower": self.min, "upper": self.max}}


def _property(property_id: str, units: str | None = None) -> tuple[str, str | None]:
    try:
        canonical, known_units = property_definition(property_id)
    except ValueError:
        return property_id, units  # Future/custom registry IDs are checked by the service.
    if units is not None and units != known_units:
        raise ValueError(f"{property_id} requires units={known_units!r}")
    return canonical, known_units


class PropertyRange(_Range):
    """Inclusive descriptor bounds. Known names (MolWt, mw, rdkit.mol_wt) infer units.

    For a custom registry ID, supply units explicitly. The server checks availability.
    """

    property_id: str
    units: str | None = None

    def __init__(
        self,
        property_id: str,
        *,
        min: float | None = None,
        max: float | None = None,
        units: str | None = None,
    ):
        super().__init__(property_id=property_id, min=min, max=max, units=units)

    @model_validator(mode="after")
    def _known_units(self):
        _, units = _property(self.property_id, self.units)
        if units is None:
            raise ValueError("units are required for a custom property ID")
        return self

    def _to_wire(self) -> dict[str, Any]:
        property_id, units = _property(self.property_id, self.units)
        return {"property_id": property_id, "units": units, **self._bounds()}


class PredictedPropertyRange(_Range):
    """Inclusive ADMET bounds in the units advertised by database capabilities."""

    endpoint_id: str
    units: str

    def __init__(
        self,
        endpoint_id: str,
        *,
        units: str,
        min: float | None = None,
        max: float | None = None,
    ):
        super().__init__(endpoint_id=endpoint_id, units=units, min=min, max=max)

    def _to_wire(self) -> dict[str, Any]:
        return {"endpoint_id": self.endpoint_id, "units": self.units, **self._bounds()}


class RequireStructure(_Value):
    """Require a registered pattern, or exactly one inline smarts/smiles query.

    Counts apply to unique atom matches, including overlapping matches.
    """

    pattern_id: str | None = None
    smarts: str | None = None
    smiles: str | None = None
    min_count: int = Field(default=1, strict=True, ge=0, le=1000)
    max_count: int | None = Field(default=None, strict=True, ge=0, le=1000)

    def __init__(
        self,
        pattern_id: str | None = None,
        *,
        smarts: str | None = None,
        smiles: str | None = None,
        min_count: int = 1,
        max_count: int | None = None,
    ):
        super().__init__(
            pattern_id=pattern_id,
            smarts=smarts,
            smiles=smiles,
            min_count=min_count,
            max_count=max_count,
        )

    @model_validator(mode="after")
    def _source_and_count(self):
        if sum(value is not None for value in (self.pattern_id, self.smarts, self.smiles)) != 1:
            raise ValueError("provide exactly one of pattern_id, smarts, or smiles")
        if self.max_count is not None and self.min_count > self.max_count:
            raise ValueError("min_count must not exceed max_count")
        return self

    def _to_wire(self) -> dict[str, Any]:
        if self.pattern_id is not None:
            data: dict[str, Any] = {"pattern_id": self.pattern_id}
        else:
            format = "smarts" if self.smarts is not None else "smiles"
            data = {"query": {"format": format, "value": getattr(self, format)}}
        if self.min_count != 1 or self.max_count is not None:
            data["count"] = {"min": self.min_count}
            if self.max_count is not None:
                data["count"]["max"] = self.max_count
        return data


class ExcludeStructure(RequireStructure):
    """Reject products matching this pattern/query and its specified match counts."""


class RequireAnyStructure(_Value):
    """Require at least minimum of these patterns; one such group per specification."""

    structures: tuple[RequireStructure, ...] = Field(min_length=1, max_length=16)
    minimum: int = Field(default=1, strict=True, ge=1)

    def __init__(self, structures: Sequence[RequireStructure], *, minimum: int = 1):
        super().__init__(structures=structures, minimum=minimum)

    @model_validator(mode="after")
    def _attainable(self):
        if any(isinstance(item, ExcludeStructure) for item in self.structures):
            raise ValueError("RequireAnyStructure accepts required structures, not exclusions")
        if self.minimum > len(self.structures):
            raise ValueError("minimum exceeds the number of structures")
        return self


class PredictedProperty(_Value):
    """An advertised ADMET objective source and its model units."""

    endpoint_id: str
    units: str

    def __init__(self, endpoint_id: str, *, units: str):
        super().__init__(endpoint_id=endpoint_id, units=units)


class Similarity(_Value):
    """ECFP4 Tanimoto similarity to a reference molecule."""

    reference_smiles: str = Field(max_length=10000)
    metric_id: Literal["rdkit.ecfp4_tanimoto"] = "rdkit.ecfp4_tanimoto"

    def __init__(
        self,
        reference_smiles: str,
        *,
        metric_id: Literal["rdkit.ecfp4_tanimoto"] = "rdkit.ecfp4_tanimoto",
    ):
        super().__init__(reference_smiles=reference_smiles, metric_id=metric_id)


class ExternalScore(_Value):
    """The raw callback score in a mixed run; standalone callbacks use direction=."""

    units: str | None = None


class _Objective(_Value):
    source: str | PredictedProperty | Similarity | ExternalScore
    scale: Scale
    weight: float = Field(default=1, strict=True, gt=0)

    def __init__(
        self,
        source: str | PredictedProperty | Similarity | ExternalScore,
        *,
        scale: Scale,
        weight: float = 1,
    ):
        super().__init__(source=source, scale=scale, weight=weight)

    def _to_wire(self) -> dict[str, Any]:
        source = self.source
        if isinstance(source, str):
            data: dict[str, Any] = {"type": "property", "property_id": _property(source)[0]}
        elif isinstance(source, PredictedProperty):
            data = {"type": "predicted_property", **source.model_dump()}
        elif isinstance(source, Similarity):
            data = {"type": "similarity", **source.model_dump()}
        else:
            data = {"type": "external", **source.model_dump(exclude_none=True)}
        data.update(
            direction="minimize" if isinstance(self, Minimize) else "maximize",
            lower=self.scale.lower,
            upper=self.scale.upper,
            weight=self.weight,
        )
        return data


class Maximize(_Objective):
    """Prefer larger values of a property, prediction, similarity, or external score."""


class Minimize(_Objective):
    """Prefer smaller values; the service reverses the normalized desirability."""


Constraint = Union[PropertyRange, PredictedPropertyRange, RequireStructure, RequireAnyStructure]
Constraints = Union[Mapping[str, Any], Sequence[Constraint]]
Objective = Union[Maximize, Minimize]
Objectives = Sequence[Union[Objective, Mapping[str, Any]]]


def _constraints_to_wire(constraints: Sequence[Constraint]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for index, constraint in enumerate(constraints):
        if isinstance(constraint, (PropertyRange, PredictedPropertyRange)):
            key = "properties" if isinstance(constraint, PropertyRange) else "predicted_properties"
            data.setdefault(key, []).append(constraint._to_wire())
        elif isinstance(constraint, RequireStructure):
            key = "none" if isinstance(constraint, ExcludeStructure) else "all"
            data.setdefault("structures", {}).setdefault(key, []).append(constraint._to_wire())
        elif isinstance(constraint, RequireAnyStructure):
            structures = data.setdefault("structures", {})
            if "any" in structures:
                raise ValueError("only one RequireAnyStructure group is supported")
            structures["any"] = {
                "minimum": constraint.minimum,
                "conditions": [item._to_wire() for item in constraint.structures],
            }
        else:
            raise ValueError(f"constraints[{index}] must be a typed chemistry constraint")
    return data
