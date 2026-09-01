"""Chemistry-thin builders for the public selection and run documents."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["molecule-selection/1"] = "molecule-selection/1"
    database: dict[str, str | None]
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
        database_value = {"database_id": database}
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

    def where(
        self,
        property_id: str,
        *,
        units: str,
        fidelity: str = "exact_product",
        missing: str = "reject",
        **operator_value: Any,
    ) -> Selection:
        allowed = {"gt", "gte", "lt", "lte", "range"}
        supplied = allowed & set(operator_value)
        if len(supplied) != 1 or set(operator_value) - allowed:
            raise ValueError("provide exactly one of gt, gte, lt, lte, or range")
        operator = supplied.pop()
        value = operator_value[operator]
        if operator == "range" and isinstance(value, list | tuple):
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

    def limit(self, value: int) -> Selection:
        if not 1 <= value <= 1_000:
            raise ValueError("selection limit must be between 1 and 1,000")
        return self._changed(lambda payload: payload["portfolio"].update(limit=value))

    def shortlist_multiplier(self, value: int) -> Selection:
        if not 0 <= value <= 200:
            raise ValueError("shortlist multiplier must be between 0 and 200")
        return self._changed(
            lambda payload: payload["execution"].update(shortlist_multiplier=value)
        )

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
        return deepcopy(self._model.model_dump(mode="json", exclude_none=True))

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
