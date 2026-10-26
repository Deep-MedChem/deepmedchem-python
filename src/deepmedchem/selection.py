"""Chemistry-thin builders for the public selection and run documents."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .databases import resolve_database


class PredictedPropertyRankQuantileAcquisition(BaseModel):
    """Fast factorized preselection followed by an exact product prediction."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["predicted_property_rank_quantile"] = (
        "predicted_property_rank_quantile"
    )
    endpoint_id: str = Field(min_length=1, max_length=256)
    direction: Literal["minimize", "maximize"]
    keep_fraction: float = Field(gt=0, le=1)


class SelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["molecule-selection/1"] = "molecule-selection/1"
    database: dict[str, str | None]
    calculation_mode: Literal["original", "deprotected"] = "original"
    references: list[dict[str, Any]] = Field(default_factory=list)
    strategy: dict[str, Any]
    constraints: dict[str, Any] = Field(default_factory=dict)
    objectives: list[dict[str, Any]] = Field(default_factory=list)
    portfolio: dict[str, Any] = Field(default_factory=lambda: {"limit": 100})
    execution: dict[str, Any] = Field(default_factory=lambda: {"quality": "balanced"})
    include: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.strategy.get("type") not in {"ranked", "sample"}:
            raise ValueError("strategy.type must be ranked or sample")
        if self.calculation_mode == "deprotected" and self.strategy.get("type") != "ranked":
            raise ValueError("deprotected calculation is supported only for ranked similarity")
        ids = [str(item.get("id", "")) for item in self.references]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise ValueError("reference IDs must be non-empty and unique")
        if len(self.include) != len(set(self.include)):
            raise ValueError("include values must be unique")
        return self


class Selection:
    """Copy-on-write fluent builder for ``molecule-selection/1``."""

    def __init__(self, model: SelectionModel):
        self._model = model

    @classmethod
    def from_database(cls, database: str, *, release: str | None = None) -> Selection:
        database_value = {"database_id": resolve_database(database)}
        if release:
            database_value["release_id"] = release
        return cls(
            SelectionModel(
                database=database_value,
                strategy={"type": "ranked"},
            )
        )

    @classmethod
    def model_validate(cls, value: Selection | dict[str, Any]) -> Selection:
        if isinstance(value, cls):
            return value
        return cls(SelectionModel.model_validate(value))

    def _changed(self, update) -> Selection:
        payload = self.to_dict()
        update(payload)
        return Selection.model_validate(payload)

    def reference(
        self,
        reference_id: str,
        *,
        smiles: str | None = None,
        smarts: str | None = None,
    ) -> Selection:
        if (smiles is None) == (smarts is None):
            raise ValueError("provide exactly one of smiles or smarts")

        def update(payload):
            if any(item["id"] == reference_id for item in payload["references"]):
                raise ValueError(f"duplicate reference ID: {reference_id}")
            payload["references"].append(
                {
                    "id": reference_id,
                    "structure": {
                        "format": "smiles" if smiles is not None else "smarts",
                        "value": smiles if smiles is not None else smarts,
                    },
                }
            )

        return self._changed(update)

    def ranked(self) -> Selection:
        return self._changed(lambda payload: payload.update(strategy={"type": "ranked"}))

    def calculation_mode(self, mode: Literal["original", "deprotected"]) -> Selection:
        """Choose similarity representation; source structures and constraints stay intact."""
        return self._changed(lambda payload: payload.update(calculation_mode=mode))

    def sample(
        self,
        *,
        distribution: str = "route_product_tuple",
        seed: int | None = None,
    ) -> Selection:
        def update(payload):
            payload["strategy"] = {"type": "sample", "distribution": distribution}
            payload["objectives"] = []
            if seed is not None:
                payload["execution"]["seed"] = seed

        return self._changed(update)

    def require_preset(self, preset: str) -> Selection:
        if "/v" not in preset:
            raise ValueError("preset must include a version, for example lipinski-ro5/v1")
        preset_id, version = preset.rsplit("/v", 1)

        def update(payload):
            constraints = payload["constraints"]
            constraints.setdefault("presets", []).append(
                {"preset_id": preset_id, "version": version}
            )

        return self._changed(update)

    def maximize_similarity(self, metric: str, *, reference: str) -> Selection:
        def update(payload):
            payload["objectives"].append(
                {
                    "type": "similarity",
                    "reference_id": reference,
                    "metric_id": metric,
                    "direction": "maximize",
                }
            )

        return self._changed(update)

    def acquire_predicted_property(
        self,
        endpoint_id: str,
        *,
        direction: Literal["minimize", "maximize"],
        keep_fraction: float,
    ) -> Selection:
        """Trim candidates with CP16, then predict unique products with the teacher."""

        acquisition = PredictedPropertyRankQuantileAcquisition(
            endpoint_id=endpoint_id,
            direction=direction,
            keep_fraction=keep_fraction,
        )
        if self._model.strategy.get("type") != "ranked":
            raise ValueError("predicted-property acquisition requires ranked strategy")
        if not any(item.get("type") == "similarity" for item in self._model.objectives):
            raise ValueError("predicted-property acquisition requires a similarity objective")
        if "acquisition" in self._model.execution:
            raise ValueError("selection already has an acquisition directive")

        return self._changed(
            lambda payload: payload["execution"].update(
                acquisition=acquisition.model_dump(mode="json")
            )
        )

    def require_different_scaffold(self, method: str, *, reference: str) -> Selection:
        def update(payload):
            payload["constraints"].setdefault("relationships", []).append(
                {
                    "type": "scaffold_relation",
                    "reference_id": reference,
                    "method_id": method,
                    "operator": "different",
                }
            )

        return self._changed(update)

    def require_pattern(self, pattern_id: str, *, min_count: int = 1) -> Selection:
        def update(payload):
            structures = payload["constraints"].setdefault("structures", {})
            structures.setdefault("all", []).append(
                {
                    "type": "structure",
                    "id": pattern_id.rsplit("/", 1)[0].replace("/", "-"),
                    "relation": "contains",
                    "pattern_id": pattern_id,
                    "count": {"min": min_count},
                }
            )
            structures.setdefault("any", {"minimum": 0, "conditions": []})
            structures.setdefault("none", [])

        return self._changed(update)

    def _structures(self, payload) -> dict[str, Any]:
        structures = payload["constraints"].setdefault("structures", {})
        structures.setdefault("all", [])
        structures.setdefault("any", {"minimum": 0, "conditions": []})
        structures.setdefault("none", [])
        return structures

    @staticmethod
    def _structure_id(structures: dict[str, Any], wanted: str) -> str:
        used = {
            item.get("id")
            for item in (
                *structures["all"],
                *structures["any"]["conditions"],
                *structures["none"],
            )
        }
        candidate, index = wanted, 2
        while candidate in used:
            candidate, index = f"{wanted}-{index}", index + 1
        return candidate

    @staticmethod
    def _structure_condition(
        query: str, *, format: str, condition_id: str, min_count: int, max_count: int | None
    ) -> dict[str, Any]:
        if format not in {"smarts", "smiles"}:
            raise ValueError("format must be 'smarts' or 'smiles'")
        if min_count < 0 or (max_count is not None and max_count < min_count):
            raise ValueError("structure counts need 0 <= min_count <= max_count")
        count: dict[str, int] = {"min": min_count}
        if max_count is not None:
            count["max"] = max_count
        return {
            "type": "structure",
            "id": condition_id,
            "relation": "contains",
            "query": {"format": format, "value": query},
            "count": count,
        }

    def require_structure(
        self,
        query: str,
        *,
        min_count: int = 1,
        max_count: int | None = None,
        format: str = "smarts",
        id: str = "require",
    ) -> Selection:
        """Every result must contain ``query`` between ``min_count`` and ``max_count`` times."""

        def update(payload):
            structures = self._structures(payload)
            structures["all"].append(
                self._structure_condition(
                    query,
                    format=format,
                    condition_id=self._structure_id(structures, id),
                    min_count=min_count,
                    max_count=max_count,
                )
            )

        return self._changed(update)

    def exclude_structure(
        self, query: str, *, format: str = "smarts", id: str = "exclude"
    ) -> Selection:
        """No result may contain ``query``."""

        def update(payload):
            structures = self._structures(payload)
            structures["none"].append(
                self._structure_condition(
                    query,
                    format=format,
                    condition_id=self._structure_id(structures, id),
                    min_count=1,
                    max_count=None,
                )
            )

        return self._changed(update)

    def require_any_structure(
        self,
        queries: list[str],
        *,
        minimum: int = 1,
        format: str = "smarts",
        id: str = "any",
    ) -> Selection:
        """Every result must contain at least ``minimum`` of ``queries``."""

        if not queries or not 1 <= minimum <= len(queries):
            raise ValueError("require_any_structure needs 1 <= minimum <= len(queries)")

        def update(payload):
            structures = self._structures(payload)
            group = structures["any"]
            if group["conditions"]:
                raise ValueError("a selection has one require-any group")
            for query in queries:
                group["conditions"].append(
                    self._structure_condition(
                        query,
                        format=format,
                        condition_id=self._structure_id(structures, id),
                        min_count=1,
                        max_count=None,
                    )
                )
            group["minimum"] = minimum

        return self._changed(update)

    def subset(self, name: str) -> Selection:
        """Restrict results to a named virtual subset, see ``deepmedchem.subsets``."""

        from .subsets import get_subset

        subset = get_subset(name)
        selection = self
        for preset in subset.presets:
            selection = selection.require_preset(preset)
        for condition in subset.properties:
            selection = selection.where(condition)
        for pattern in subset.require:
            selection = selection.require_structure(
                pattern.smarts,
                min_count=pattern.min_count,
                max_count=pattern.max_count,
                format=pattern.format,
                id=subset.name,
            )
        if subset.require_any:
            selection = selection.require_any_structure(
                [pattern.smarts for pattern in subset.require_any], id=subset.name
            )
        for pattern in subset.exclude:
            selection = selection.exclude_structure(
                pattern.smarts, format=pattern.format, id=f"{subset.name}-exclude"
            )
        return selection

    def where(
        self,
        property_id,
        *,
        units: str | None = None,
        fidelity: str = "exact_product",
        missing: str = "reject",
        **operator_value: Any,
    ) -> Selection:
        """Add an exact RDKit property condition.

        ``property_id`` is a server ID (``rdkit.mol_wt``) or a short name (``mw``), in
        which case ``units`` may be omitted. A text filter such as ``"mw<=450"`` or a
        :class:`deepmedchem.subsets.PropertyCondition` may be passed on its own.
        """

        from .subsets import PropertyCondition, parse_condition, property_definition

        if not isinstance(property_id, PropertyCondition) and not operator_value:
            property_id = parse_condition(str(property_id))
        if isinstance(property_id, PropertyCondition):
            if operator_value or units is not None:
                raise ValueError("pass either a condition or property_id with an operator")
            condition = property_id
            return self.where(
                condition.property_id,
                fidelity=fidelity,
                missing=missing,
                **condition.to_where(),
            )
        if units is None:
            property_id, units = property_definition(property_id)
        allowed = {"gt", "gte", "lt", "lte", "range"}
        supplied = allowed & set(operator_value)
        if len(supplied) != 1 or set(operator_value) - allowed:
            raise ValueError("provide exactly one of gt, gte, lt, lte, or range")
        operator = supplied.pop()
        value = operator_value[operator]
        if operator == "range" and isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError("range requires two endpoints")
            value = {"lower": value[0], "upper": value[1]}

        def update(payload):
            payload["constraints"].setdefault("properties", []).append(
                {
                    "type": "property",
                    "property_id": property_id,
                    "operator": operator,
                    "value": value,
                    "units": units,
                    "required_fidelity": fidelity,
                    "missing": missing,
                }
            )

        return self._changed(update)

    def where_predicted_property(
        self,
        endpoint_id: str,
        *,
        units: str,
        **operator_value: Any,
    ) -> Selection:
        """Require a range using the pinned assembled-product prediction model."""

        allowed = {"gt", "gte", "lt", "lte", "range"}
        supplied = allowed & set(operator_value)
        if len(supplied) != 1 or set(operator_value) - allowed:
            raise ValueError("provide exactly one of gt, gte, lt, lte, or range")
        operator = supplied.pop()
        value = operator_value[operator]
        if operator == "range" and isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError("range requires two endpoints")
            value = {"lower": value[0], "upper": value[1]}

        def update(payload):
            payload["constraints"].setdefault("predicted_properties", []).append(
                {
                    "type": "predicted_property",
                    "endpoint_id": endpoint_id,
                    "operator": operator,
                    "value": value,
                    "units": units,
                    "required_fidelity": "pinned_assembled_product_teacher",
                    "missing": "reject",
                }
            )

        return self._changed(update)

    def max_price(self, usd: float, *, keep_unpriced: bool = False) -> Selection:
        """Keep only products priced at most ``usd`` (whole USD, 1 mg, US delivery).

        The server applies the limit while it searches and prices every returned
        product exactly. Products with an unknown price are dropped unless
        ``keep_unpriced`` is true; they are then returned with ``price=None``.
        """

        value = float(usd)
        if not math.isfinite(value) or value < 0:
            raise ValueError("max_price must be a non-negative number of USD")

        def update(payload):
            payload["constraints"]["price"] = {
                "max": int(value) if value.is_integer() else value,
                "currency": "USD",
                "amount_mg": 1,
                "ship_to": "US",
                "missing": "keep" if keep_unpriced else "reject",
            }

        return self._changed(update)

    def limit(self, value: int) -> Selection:
        if not 1 <= value <= 1_000:
            raise ValueError("selection limit must be between 1 and 1,000")
        return self._changed(lambda payload: payload["portfolio"].update(limit=value))

    def max_per_scaffold(self, value: int) -> Selection:
        if value < 1:
            raise ValueError("max_per_scaffold must be positive")
        return self._changed(
            lambda payload: payload["portfolio"].update(max_per_scaffold=value)
        )

    def include(self, *values: str) -> Selection:
        def update(payload):
            payload["include"] = list(dict.fromkeys([*payload["include"], *values]))

        return self._changed(update)

    def to_dict(self) -> dict[str, Any]:
        payload = deepcopy(self._model.model_dump(mode="json", exclude_none=True))
        if payload.get("calculation_mode") == "original":
            payload.pop("calculation_mode", None)
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)


class RunModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["run/1"] = "run/1"
    kind: Literal["selection", "selection_batch"]
    selection: dict[str, Any] | None = None
    selection_template: dict[str, Any] | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    failure_policy: Literal["continue"] = "continue"
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "selection" and self.selection is None:
            raise ValueError("selection run requires selection")
        if self.kind == "selection_batch" and (
            self.selection_template is None or not self.items
        ):
            raise ValueError("selection_batch requires a template and items")
        ids = [item.get("id") for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("run item IDs must be unique")
        return self


class Run:
    def __init__(self, model: RunModel):
        self._model = model

    @classmethod
    def selection(cls, selection: Selection | dict[str, Any]) -> Run:
        value = Selection.model_validate(selection)
        return cls(RunModel(kind="selection", selection=value.to_dict()))

    @classmethod
    def selection_batch(
        cls,
        *,
        template: Selection | dict[str, Any],
        items: dict[str, dict[str, str]],
        metadata: dict[str, str] | None = None,
    ) -> Run:
        selection = Selection.model_validate(template).to_dict()
        selection["references"] = []
        item_values = []
        for item_id, bindings in items.items():
            item_values.append(
                {
                    "id": item_id,
                    "references": [
                        {
                            "id": reference_id,
                            "structure": {"format": "smiles", "value": structure},
                        }
                        for reference_id, structure in bindings.items()
                    ],
                }
            )
        return cls(
            RunModel(
                kind="selection_batch",
                selection_template=selection,
                items=item_values,
                metadata=metadata or {},
            )
        )

    @classmethod
    def model_validate(cls, value: Run | dict[str, Any]) -> Run:
        if isinstance(value, cls):
            return value
        return cls(RunModel.model_validate(value))

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._model.model_dump(mode="json", exclude_none=True))

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)
