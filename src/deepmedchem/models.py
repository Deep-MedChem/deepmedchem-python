"""Forward-compatible, chemistry-first API response models."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    @property
    def raw(self) -> dict[str, Any]:
        """Return the complete JSON-compatible response, including additive fields."""

        return self.model_dump(mode="json")


class WarningMessage(APIModel):
    code: str | None = None
    message: str | None = None


class PredictedPropertyAcquisitionHit(APIModel):
    endpoint_id: str
    approximate_value: float
    predicted_value: float
    applicable: bool


class PredictedPropertyAcquisitionResult(APIModel):
    endpoint_id: str
    approximate_model_version: str
    predicted_model_version: str
    direction: str
    units: str
    qualification: str
    candidates_before: int
    candidates_after: int


class Hit(APIModel):
    """One typed, immutable row in a molecular result."""

    model_config = ConfigDict(extra="allow", frozen=True)

    smiles: str
    rank: int
    score: float | None = None
    product_id: str | None = None
    reaction_id: str | None = None
    metric: str | None = None
    price: int | None = Field(default=None, gt=0)
    properties: dict[str, float] | None = None
    predicted_properties: dict[str, float] | None = None
    acquisition: PredictedPropertyAcquisitionHit | None = None

    @property
    def extra(self) -> Mapping[str, Any]:
        common = {
            "smiles",
            "rank",
            "score",
            "product_id",
            "reaction_id",
            "metric",
            "price",
            "properties",
            "predicted_properties",
            "acquisition",
        }
        return {key: value for key, value in self.raw.items() if key not in common}


class SearchMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str | None = None
    database: str | None = None
    release: str | None = None
    method: str | None = None
    metric: str | None = None
    returned: int = 0
    elapsed_ms: float | None = None


class SearchResult(APIModel, Sequence[str]):
    """An ordered molecule sequence with typed, locally available search details."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: tuple[WarningMessage, ...] = ()
    request_id: str | None = None
    database_id: str | None = None
    database_release: str | None = None
    scorer: str | None = None
    metric: str | None = None
    counts: dict[str, Any] = Field(default_factory=dict)
    timing_ms: dict[str, float] = Field(default_factory=dict)

    @property
    def method(self) -> str | None:
        return self.scorer

    @property
    def hits(self) -> tuple[Hit, ...]:
        return tuple(
            Hit.model_validate(
                {
                    **row,
                    "rank": row.get("rank", row.get("index", index - 1) + 1),
                    "metric": row.get("metric", self.metric),
                }
            )
            for index, row in enumerate(self.results, start=1)
        )

    @property
    def smiles(self) -> list[str]:
        return [hit.smiles for hit in self.hits]

    @property
    def scores(self) -> list[float | None]:
        return [hit.score for hit in self.hits]

    @property
    def ids(self) -> list[str | None]:
        return [hit.product_id for hit in self.hits]

    @property
    def ranks(self) -> list[int]:
        return [hit.rank for hit in self.hits]

    @property
    def prices(self) -> list[int | None]:
        """Return aligned whole-dollar prices already present in the response."""

        return [hit.price for hit in self.hits]

    @property
    def meta(self) -> SearchMeta:
        returned = self.counts.get("returned", len(self.results))
        elapsed = self.timing_ms.get("total")
        return SearchMeta(
            request_id=self.request_id,
            database=self.database_id,
            release=self.database_release,
            method=self.method,
            metric=self.metric,
            returned=int(returned),
            elapsed_ms=float(elapsed) if elapsed is not None else None,
        )

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[str]:
        return iter(self.smiles)

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> list[str]: ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        return self.smiles[index]

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({len(self)} molecules, "
            f"method={self.method!r}, database={self.database_id!r})"
        )

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.results]

    def to_csv(self, path: str | os.PathLike[str]) -> int:
        """Write the hits to a CSV file and return the number of rows written."""

        from .export import write_csv

        return write_csv(self, path)

    def to_sdf(self, path: str | os.PathLike[str]) -> int:
        """Write the hits to an SDF file (requires RDKit) with scores and prices as tags."""

        from .export import write_sdf

        return write_sdf(self, path)

    def to_file(self, path: str | os.PathLike[str], *, format: str | None = None) -> int:
        """Write the hits to ``path`` as CSV, SDF, SMILES, or JSON, inferred from the suffix."""

        from .export import write_result

        return write_result(self, path, format=format)

    def to_html(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        limit: int = 100,
        show: bool | None = None,
    ) -> Path:
        """Save the hits as a standalone HTML table with 2D structures (requires RDKit).

        Without ``path`` the file is saved in the current directory as
        ``deepmedchem-<database>-<method>-<UTC timestamp>.html``. Only the first
        ``limit`` molecules are drawn; the page says when more were returned. In a
        Jupyter notebook the table is also shown in the cell unless ``show=False``.
        Returns the path of the saved file.
        """

        from .html_export import (
            default_html_path,
            html_document,
            in_notebook,
            render_html_fragment,
            show_in_notebook,
        )

        fragment, _ = render_html_fragment(self, limit=limit)
        target = Path(path) if path is not None else default_html_path(self)
        target.write_text(html_document(self, fragment), encoding="utf-8")
        if show if show is not None else in_notebook():
            show_in_notebook(fragment)
        return target

    def to_pandas(self):
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError(
                "SearchResult.to_pandas() requires pandas. Install it with "
                "`python -m pip install pandas`."
            ) from error
        return pd.DataFrame.from_records(self.to_records())


class SubstructureResult(SearchResult):
    @property
    def method(self) -> str:
        return "substructure"


class SampleResult(SearchResult):
    sampling_method: str | None = None
    sampling_version: str | None = None
    seed: int | None = None

    @property
    def method(self) -> str:
        return "sample"


class UsagePromotion(APIModel):
    """A temporary multiplier applied to the daily credit allowance."""

    id: str | None = None
    label: str | None = None
    multiplier: float | None = None
    base_limit: int | None = Field(default=None, alias="baseLimit")
    starts_at: str | None = Field(default=None, alias="startsAt")
    ends_at: str | None = Field(default=None, alias="endsAt")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Usage(APIModel):
    """Daily CHEESE Credit usage for the account that owns the API key."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    plan: str | None = None
    limit: int | None = None
    used: int = 0
    remaining: int | None = None
    unlimited: bool = False
    window: str | None = "day"
    reset_at: str | None = Field(default=None, alias="resetAt")
    seconds_to_reset: int | None = Field(default=None, alias="secondsToReset")
    promo: UsagePromotion | None = None
    user_id: str | None = Field(default=None, alias="userId")

    @property
    def tier(self) -> str | None:
        """Alias for ``plan``: free, registered, private, premium, ..."""

        return self.plan

    def __repr__(self) -> str:
        credits = "unlimited" if self.unlimited else f"{self.remaining}/{self.limit} remaining"
        return f"Usage(plan={self.plan!r}, credits={credits})"


class SelectionValidation(APIModel):
    valid: bool
    normalized_selection: dict[str, Any]
    selection_hash: str
    constraint_execution: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class SelectionEstimate(APIModel):
    normalized_selection: dict[str, Any]
    selection_hash: str
    execution_tier: str
    work: dict[str, Any]
    reusable_run_request: dict[str, Any] | None = None


class SelectionResult(SearchResult):
    id: str
    object: str
    status: str
    selection_hash: str
    normalized_selection: dict[str, Any]
    acquisition: PredictedPropertyAcquisitionResult | None = None


class RunProgress(APIModel):
    total: int
    pending: int
    running: int
    succeeded: int
    failed: int
    cancelled: int


class RunResource(APIModel):
    id: str
    object: str = "run"
    kind: str
    status: str
    progress: RunProgress
    last_event_sequence: int = 0
    links: dict[str, str] = Field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in {
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        }


class RunItem(APIModel):
    id: str
    input_index: int
    status: str
    attempt_count: int = 0
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class RunEvent(APIModel):
    sequence: int
    type: str
    run_id: str
    item_id: str | None = None
    status: str | None = None
    progress: RunProgress | None = None


class Page(APIModel):
    data: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


# --- Optimizations (ask/tell) ---------------------------------------------------

OPTIMIZATION_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class Molecule(APIModel):
    """One proposed molecule: the id to report its score under, and its SMILES."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str
    smiles: str


class Batch(APIModel, Sequence[Molecule]):
    """The molecules Navigator wants scored next, in the order to score them."""

    id: str
    round: int = 0
    molecules: list[Molecule] = Field(default_factory=list)

    @property
    def smiles(self) -> list[str]:
        return [molecule.smiles for molecule in self.molecules]

    @property
    def ids(self) -> list[str]:
        return [molecule.id for molecule in self.molecules]

    def __len__(self) -> int:
        return len(self.molecules)

    def __iter__(self) -> Iterator[Molecule]:  # type: ignore[override]
        return iter(self.molecules)

    @overload
    def __getitem__(self, index: int) -> Molecule: ...

    @overload
    def __getitem__(self, index: slice) -> list[Molecule]: ...

    def __getitem__(self, index: int | slice) -> Molecule | list[Molecule]:
        return self.molecules[index]

    def __repr__(self) -> str:
        return f"Batch(id={self.id!r}, round={self.round}, {len(self)} molecules)"

    __str__ = __repr__

    def to_records(self) -> list[dict[str, Any]]:
        return [{"id": molecule.id, "smiles": molecule.smiles} for molecule in self.molecules]

    def to_csv(self, path: str | os.PathLike[str]) -> int:
        """Write ``id,smiles`` rows to ``path`` and return the number of molecules written."""

        import csv

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "smiles"])
            for molecule in self.molecules:
                writer.writerow([molecule.id, molecule.smiles])
        return len(self.molecules)


class Observation(APIModel):
    """One scored molecule: its score, status, the round it was proposed in, and metrics."""

    id: str
    smiles: str | None = None
    score: float | None = None
    status: str = "valid"
    round: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("metrics", mode="before")
    @classmethod
    def _null_metrics(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def valid(self) -> bool:
        return self.status == "valid" and self.score is not None

    def __repr__(self) -> str:
        score = "None" if self.score is None else f"{self.score:.4g}"
        return (
            f"Observation(score={score}, status={self.status!r}, round={self.round}, "
            f"smiles={self.smiles!r}, id={self.id!r})"
        )

    __str__ = __repr__


class OptimizationProgress(APIModel):
    budget: int = 0
    proposed: int = 0
    scored: int = 0
    valid: int = 0
    failed: int = 0


class OptimizationResource(APIModel):
    """The server-side state of one optimization (``/api/v2/optimizations/{id}``)."""

    id: str
    object: str = "optimization"
    name: str | None = None
    status: str
    status_reason: str | None = None
    specification: dict[str, Any] = Field(default_factory=dict)
    specification_hash: str | None = None
    round: int = 0
    pending_batch_id: str | None = None
    progress: OptimizationProgress = Field(default_factory=OptimizationProgress)
    best: Observation | None = None
    engine: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    links: dict[str, str] = Field(default_factory=dict)

    @field_validator("specification", "progress", "engine", "links", mode="before")
    @classmethod
    def _null_as_empty(cls, value: Any) -> Any:
        # The server sends null for parts that do not exist yet (no engine before the
        # first proposal); an empty value keeps attribute access uniform for callers.
        return {} if value is None else value

    @property
    def terminal(self) -> bool:
        return self.status in OPTIMIZATION_TERMINAL_STATUSES

    @property
    def direction(self) -> str | None:
        objective = self.specification.get("objective") or {}
        direction = objective.get("direction") if isinstance(objective, dict) else None
        return str(direction) if direction else None


class SubmitReceipt(APIModel):
    """The answer to a score submission. ``duplicate`` means it was already accepted."""

    accepted: bool
    duplicate: bool = False
    batch_id: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    optimization: OptimizationResource | None = None

    @field_validator("counts", mode="before")
    @classmethod
    def _null_counts(cls, value: Any) -> Any:
        return {} if value is None else value


class OptimizationResult(APIModel, Sequence[Observation]):
    """Every observation of an optimization, with helpers for the best molecules."""

    observations: list[Observation] = Field(default_factory=list)
    optimization: OptimizationResource | None = None

    @property
    def direction(self) -> str | None:
        return self.optimization.direction if self.optimization is not None else None

    def top(self, n: int = 10) -> list[Observation]:
        """The ``n`` best valid observations, best first, according to the direction."""

        valid = [observation for observation in self.observations if observation.valid]
        if self.direction in {"minimize", "maximize"}:
            valid.sort(key=lambda row: row.score, reverse=self.direction == "maximize")
        return valid[: max(int(n), 0)]

    @property
    def best(self) -> Observation | None:
        top = self.top(1)
        return top[0] if top else None

    def __len__(self) -> int:
        return len(self.observations)

    def __iter__(self) -> Iterator[Observation]:  # type: ignore[override]
        return iter(self.observations)

    @overload
    def __getitem__(self, index: int) -> Observation: ...

    @overload
    def __getitem__(self, index: slice) -> list[Observation]: ...

    def __getitem__(self, index: int | slice) -> Observation | list[Observation]:
        return self.observations[index]

    def __repr__(self) -> str:
        best = self.best
        summary = f", best={best.score:.4g}" if best is not None and best.score is not None else ""
        name = self.optimization.id if self.optimization is not None else None
        return f"OptimizationResult({len(self)} observations{summary}, optimization={name!r})"

    __str__ = __repr__

    def to_records(self) -> list[dict[str, Any]]:
        """Flat rows: ``id, smiles, score, status, round, error`` followed by the metrics."""

        base = ("id", "smiles", "score", "status", "round", "error")
        records = []
        for observation in self.observations:
            record: dict[str, Any] = {key: getattr(observation, key) for key in base}
            for key, value in observation.metrics.items():
                record[f"metric_{key}" if key in record else key] = value
            records.append(record)
        return records

    def to_csv(self, path: str | os.PathLike[str]) -> int:
        """Write every observation to a CSV file and return the number of rows written."""

        import csv

        records = self.to_records()
        columns: list[str] = ["id", "smiles", "score", "status", "round", "error"]
        for record in records:
            columns.extend(key for key in record if key not in columns)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        return len(records)

    def to_pandas(self):
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError(
                "OptimizationResult.to_pandas() requires pandas. Install it with "
                "`python -m pip install pandas`."
            ) from error
        return pd.DataFrame.from_records(self.to_records())
