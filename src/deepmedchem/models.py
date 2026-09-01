"""Forward-compatible, chemistry-first API response models."""

from __future__ import annotations

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


class Hit(APIModel):
    """One typed, immutable row in a molecular result."""

    model_config = ConfigDict(extra="allow", frozen=True)

    smiles: str
    rank: int
    score: float | None = None
    product_id: str | None = None
    reaction_id: str | None = None
    metric: str | None = None

    @property
    def extra(self) -> Mapping[str, Any]:
        common = {"smiles", "rank", "score", "product_id", "reaction_id", "metric"}
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
