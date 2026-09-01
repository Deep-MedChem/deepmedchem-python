"""Forward-compatible typed API response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    @property
    def raw(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SearchResult(APIModel):
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str | None = None
    database_id: str | None = None
    database_release: str | None = None


class SampleResult(SearchResult):
    sampling_method: str | None = None
    sampling_version: str | None = None
    seed: int | None = None


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
