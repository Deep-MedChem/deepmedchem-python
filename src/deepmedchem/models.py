"""Forward-compatible, chemistry-first API response models."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, overload

from pydantic import BaseModel, ConfigDict, Field


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
