"""Navigator optimizations: ask for molecules, score them with your own scorer, tell the scores.

Navigator proposes a batch of molecules from a synthon space on DeepMedChem's servers. Your
scorer (docking, an ML model, anything that maps a molecule to one number) scores the batch
locally, and the scores go back to the server, which learns from them and proposes the next
batch until the budget is spent.

The simplest entry point is :func:`optimize`; :class:`Optimization` gives manual ask/tell control,
and ``client.optimizations`` exposes the individual endpoints.
"""

from __future__ import annotations

import asyncio
import json
import math
import numbers
import os
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .client import AsyncClient, Client, DeepMedChemError
from .databases import resolve_database
from .models import (
    Batch,
    Observation,
    OptimizationResource,
    OptimizationResult,
    Page,
    SubmitReceipt,
)

__all__ = [
    "AsyncOptimization",
    "AsyncOptimizations",
    "Optimization",
    "Optimizations",
    "normalize_scores",
    "optimize",
]

OPTIMIZATIONS_PATH = "/api/v2/optimizations"
DIRECTIONS = ("minimize", "maximize")
FILTERS = ("druglike",)
ROW_STATUSES = ("valid", "failed", "timeout", "filtered", "cancelled")
RESULT_ORDERS = ("best", "round")
MAX_WAIT_SECONDS = 25
MAX_METRICS = 32
MAX_ERROR_LENGTH = 500
MAX_JSON_OBJECT_BYTES = 4096
MAX_RESULTS_PAGE = 1000
MAX_LIST_PAGE = 200
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
_TRANSIENT_STATUS = frozenset({500, 502, 503, 504})
_ASK_MAX_TRANSIENT_ERRORS = 5
_SUBMIT_EXTRA_ATTEMPTS = 3

# Indirections that tests replace to avoid real waiting.
_sleep = time.sleep
_async_sleep = asyncio.sleep
_monotonic = time.monotonic


# --- Specification ------------------------------------------------------------


def _check_direction(direction: Any) -> str:
    if direction in DIRECTIONS:
        return direction
    hint = ""
    if isinstance(direction, str):
        lowered = direction.strip().lower()
        if lowered.startswith("min"):
            hint = ' Did you mean "minimize"?'
        elif lowered.startswith("max"):
            hint = ' Did you mean "maximize"?'
    raise ValueError(
        f'direction must be "minimize" or "maximize", got {direction!r}.{hint} '
        "Docking scores are usually minimized and predicted activities maximized; scores "
        "are never negated for you."
    )


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or int(value) < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return int(value)


def _number(value: Any, where: str) -> int | float:
    if isinstance(value, bool) or isinstance(value, (str, bytes)):
        raise ValueError(f"{where} must be a number, got {value!r}")
    if isinstance(value, numbers.Integral):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{where} must be a number, got {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"{where} must be finite, got {value!r}")
    return number


def _properties(properties: Any) -> dict[str, dict[str, int | float]] | None:
    """Accept ``{"MolWt": (None, 500)}`` or ``{"MolWt": {"max": 500}}``; return the wire shape."""

    if properties is None:
        return None
    if not isinstance(properties, Mapping):
        raise ValueError(
            'properties must be a mapping such as {"MolWt": (None, 500)} or {"MolWt": {"max": 500}}'
        )
    normalized: dict[str, dict[str, int | float]] = {}
    for name, bounds in properties.items():
        if isinstance(bounds, Mapping):
            unknown = sorted(set(bounds) - {"min", "max"})
            if unknown:
                raise ValueError(
                    f"properties[{name!r}] accepts only 'min' and 'max', not {unknown}"
                )
            low, high = bounds.get("min"), bounds.get("max")
        elif isinstance(bounds, (tuple, list)) and len(bounds) == 2:
            low, high = bounds
        else:
            raise ValueError(
                f"properties[{name!r}] must be a (min, max) pair or a mapping with 'min' and/or "
                f"'max', got {bounds!r}; use None for an open end"
            )
        entry: dict[str, int | float] = {}
        if low is not None:
            entry["min"] = _number(low, f"properties[{name!r}] min")
        if high is not None:
            entry["max"] = _number(high, f"properties[{name!r}] max")
        if not entry:
            raise ValueError(f"properties[{name!r}] needs a min, a max, or both")
        if "min" in entry and "max" in entry and entry["min"] > entry["max"]:
            raise ValueError(f"properties[{name!r}] has min greater than max")
        normalized[str(name)] = entry
    return normalized


def _json_object(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping (a JSON object), got {type(value).__name__}")
    data = dict(value)
    try:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain only JSON values: {error}") from error
    if len(encoded.encode("utf-8")) > MAX_JSON_OBJECT_BYTES:
        raise ValueError(f"{name} must be at most {MAX_JSON_OBJECT_BYTES} bytes of JSON")
    return data


def _check_name(name: Any) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise ValueError(
            f"name must be 1-128 characters of letters, digits, '.', '_' or '-', got {name!r}"
        )
    if name.startswith("opt_"):
        raise ValueError("name must not start with 'opt_', which is reserved for optimization ids")
    return name


def build_specification(
    *,
    database: str,
    direction: str,
    budget: int,
    batch_size: int,
    name: str | None = None,
    strategy: str | None = None,
    filters: str | None = None,
    properties: Mapping[str, Any] | None = None,
    seed: int | None = None,
    scorer: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    objective_name: str | None = None,
    units: str | None = None,
) -> dict[str, Any]:
    """Validate the arguments of ``create`` and return the JSON body sent to the API."""

    if not isinstance(database, str) or not database.strip():
        raise ValueError("database is required, for example 'enamine' or 'freedom'")
    budget = _positive_int(budget, "budget")
    batch_size = _positive_int(batch_size, "batch_size")
    if batch_size > budget:
        raise ValueError(f"batch_size ({batch_size}) must not exceed budget ({budget})")
    objective: dict[str, Any] = {"direction": _check_direction(direction)}
    for key, label in (("name", objective_name), ("units", units)):
        if label is not None:
            if not isinstance(label, str) or not 1 <= len(label) <= 64:
                raise ValueError(f"objective {key} must be a string of 1-64 characters")
            objective[key] = label
    spec: dict[str, Any] = {
        "database": resolve_database(database.strip()),
        "objective": objective,
        "budget": budget,
        "batch_size": batch_size,
    }
    if _check_name(name) is not None:
        spec["name"] = name
    if strategy is not None:
        if not isinstance(strategy, str) or not strategy:
            raise ValueError("strategy must be a strategy name such as 'gamma_diversity_screening'")
        spec["strategy"] = strategy
    if filters is not None:
        if filters not in FILTERS:
            raise ValueError(f"filters must be None or 'druglike', got {filters!r}")
        spec["filters"] = filters
    normalized_properties = _properties(properties)
    if normalized_properties:
        spec["properties"] = normalized_properties
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, numbers.Integral) or int(seed) < 0:
            raise ValueError(f"seed must be a non-negative integer, got {seed!r}")
        spec["seed"] = int(seed)
    scorer_object = _json_object(scorer, "scorer")
    if scorer_object is not None:
        spec["scorer"] = scorer_object
    metadata_object = _json_object(metadata, "metadata")
    if metadata_object is not None:
        spec["metadata"] = metadata_object
    return spec


def _create_headers(name: str | None, idempotency_key: str | None) -> dict[str, str]:
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            raise ValueError("idempotency_key must be a string of 1-256 characters")
        return {"Idempotency-Key": idempotency_key}
    if name is None:
        # Without a name the server has no idempotency key, so a create retried after a lost
        # response would start a second optimization. A random key makes retries safe.
        return {"Idempotency-Key": f"sdk-{uuid.uuid4().hex}"}
    return {}


# --- Score normalization ---------------------------------------------------------


def _is_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    kind = type(value)
    return kind.__module__ == "numpy" and kind.__name__ in {"bool_", "bool"}


def _score_value(value: Any, molecule_id: str) -> tuple[float | None, str | None]:
    """Return ``(finite score or None, reason it is unusable)`` for one scorer value."""

    if value is None:
        return None, None
    if _is_bool(value):
        raise TypeError(
            f"The score for {molecule_id!r} is a bool ({value!r}); a score must be a number, "
            "or None for a molecule that could not be scored."
        )
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"The score for {molecule_id!r} is a string ({value!r}); convert it with float(), "
            "or use None for a molecule that could not be scored."
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TypeError(
            f"The score for {molecule_id!r} must be a number or None, got {type(value).__name__}."
        ) from error
    if not math.isfinite(number):
        return None, f"non-finite score ({number})"
    return number, None


def _metric_value(value: Any, key: str, molecule_id: str) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if _is_bool(value):
        return bool(value)
    if isinstance(value, numbers.Integral):
        return int(value)
    if not isinstance(value, (bytes, Mapping, list, tuple, set, frozenset)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            pass
        else:
            return number if math.isfinite(number) else None
    raise TypeError(
        f"Metric {key!r} for molecule {molecule_id!r} must be a number, string, bool or None, "
        f"got {type(value).__name__}. Metrics are stored flat; keep structured data (poses, "
        "arrays) in your own files."
    )


def _metrics(metrics: Mapping[Any, Any], molecule_id: str) -> dict[str, Any]:
    if len(metrics) > MAX_METRICS:
        raise ValueError(
            f"Molecule {molecule_id!r} has {len(metrics)} metrics; "
            f"at most {MAX_METRICS} are stored."
        )
    return {str(key): _metric_value(value, str(key), molecule_id) for key, value in metrics.items()}


def _row(molecule_id: str, value: Any) -> dict[str, Any]:
    """Turn one scorer value (number, None, NaN or dict) into one submission row."""

    if not isinstance(value, Mapping):
        score, reason = _score_value(value, molecule_id)
        if score is not None:
            return {"id": molecule_id, "score": score, "status": "valid"}
        row: dict[str, Any] = {"id": molecule_id, "score": None, "status": "failed"}
        if reason:
            row["error"] = reason
        return row

    data = dict(value)
    claimed = data.pop("id", None)
    if claimed is not None and str(claimed) != molecule_id:
        raise ValueError(
            f"The scorer row for {molecule_id!r} carries a different id ({claimed!r}); "
            "return rows in batch order or as a mapping of id to score."
        )
    data.pop("smiles", None)
    score, reason = _score_value(data.pop("score", None), molecule_id)
    status = data.pop("status", None)
    message = data.pop("error", None)
    nested = data.pop("metrics", None)
    metrics: dict[Any, Any] = {}
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise TypeError(f"'metrics' for molecule {molecule_id!r} must be a mapping")
        metrics.update(nested)
    metrics.update(data)

    if status is None:
        status = "valid" if score is not None else "failed"
    else:
        status = str(status).strip().lower()
        if status not in ROW_STATUSES:
            raise ValueError(
                f"Status {status!r} for molecule {molecule_id!r} is not one of {list(ROW_STATUSES)}"
            )
        if status == "valid" and score is None:
            status = "failed"
            reason = reason or "status 'valid' without a finite score"
    if status != "valid":
        score = None
    row = {"id": molecule_id, "score": score, "status": status}
    if message is None:
        message = reason
    if message is not None and str(message):
        row["error"] = str(message)[:MAX_ERROR_LENGTH]
    if metrics:
        row["metrics"] = _metrics(metrics, molecule_id)
    return row


def _batch_ids(batch: Any) -> list[str]:
    molecules = getattr(batch, "molecules", None)
    if molecules is None:
        raise TypeError(f"batch must be a Batch returned by ask(), got {type(batch).__name__}")
    return [str(molecule.id) for molecule in molecules]


def _rows_from_mapping(scores: Mapping[Any, Any]) -> list[dict[str, Any]]:
    return [_row(str(key), value) for key, value in scores.items()]


def normalize_scores(batch: Batch, scores: Any) -> list[dict[str, Any]]:
    """Turn scorer output into exactly one submission row per molecule of ``batch``.

    ``scores`` is either a sequence aligned with ``batch.molecules`` (same length, same order)
    or a mapping of molecule id to value. Each value is

    * a finite number: a valid score;
    * ``None``, NaN or infinity: a failed molecule;
    * a dict with ``score`` and optionally ``status``, ``error`` and ``metrics``; every other key
      becomes a metric (numbers, strings, bools or None, at most 32 per molecule).

    Ids missing from a mapping become ``failed`` with ``error="not returned by scorer"``.
    NumPy scalars work without importing NumPy. A bool is not a score.
    """

    ids = _batch_ids(batch)
    batch_label = getattr(batch, "id", "the batch")
    if isinstance(scores, Mapping):
        by_id = {str(key): value for key, value in scores.items()}
        known = set(ids)
        unknown = [key for key in by_id if key not in known]
        if unknown:
            raise ValueError(
                f"scores contains {len(unknown)} id(s) that are not in batch {batch_label}: "
                f"{unknown[:5]}"
            )
        return [
            _row(molecule_id, by_id[molecule_id])
            if molecule_id in by_id
            else {
                "id": molecule_id,
                "score": None,
                "status": "failed",
                "error": "not returned by scorer",
            }
            for molecule_id in ids
        ]
    if scores is None or isinstance(scores, (str, bytes)):
        raise TypeError(
            "scores must be a sequence aligned with batch.smiles or a mapping of molecule id to "
            f"score, got {type(scores).__name__}"
        )
    try:
        values = list(scores)
    except TypeError as error:
        raise TypeError(
            "scores must be a sequence aligned with batch.smiles or a mapping of molecule id to "
            f"score, got {type(scores).__name__}"
        ) from error
    if len(values) != len(ids):
        raise ValueError(
            f"The scorer returned {len(values)} values for the {len(ids)} molecules of batch "
            f"{batch_label}. Return one value per SMILES, in order, using None for failures."
        )
    return [_row(molecule_id, value) for molecule_id, value in zip(ids, values)]


# --- Wire helpers ----------------------------------------------------------------


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected a non-empty id, got {value!r}")
    return quote(value, safe="-_.~")


def _path(optimization_id: str, suffix: str = "") -> str:
    return f"{OPTIMIZATIONS_PATH}/{_segment(optimization_id)}{suffix}"


def _wait_seconds(wait: Any) -> int:
    try:
        seconds = int(wait)
    except (TypeError, ValueError) as error:
        raise ValueError(f"wait must be a number of seconds, got {wait!r}") from error
    return max(0, min(MAX_WAIT_SECONDS, seconds))


def _long_poll_timeout(http_client: Any, wait: int) -> httpx.Timeout | None:
    """Make sure the HTTP read timeout outlasts a server-side long poll of ``wait`` seconds."""

    if wait <= 0:
        return None
    current = getattr(http_client, "timeout", None)
    read = getattr(current, "read", None)
    if read is None or read >= wait + 10:
        return None
    connect = getattr(current, "connect", None)
    return httpx.Timeout(wait + 15.0, connect=connect)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(30.0, max(0.0, float(value)))
    except ValueError:
        return None


def _batch_payload(
    payload: Any, response: httpx.Response
) -> tuple[OptimizationResource, Batch | None, float | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("optimization"), dict):
        raise DeepMedChemError(
            "The batch endpoint returned no optimization resource.", code="invalid_response"
        )
    resource = OptimizationResource.model_validate(payload["optimization"])
    batch_data = payload.get("batch")
    batch = Batch.model_validate(batch_data) if batch_data else None
    return resource, batch, _retry_after(response)


def _results_params(order: str, limit: int, cursor: str | None) -> dict[str, Any]:
    if order not in RESULT_ORDERS:
        raise ValueError(f"order must be 'best' or 'round', got {order!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS_PAGE:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS_PAGE}, got {limit!r}")
    params: dict[str, Any] = {"order": order, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    return params


def _list_params(
    status: str | None, name: str | None, cursor: str | None, page_limit: int
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": page_limit}
    if name is not None:
        params["name"] = name
    if status is not None:
        params["status_filter"] = status
    if cursor:
        params["cursor"] = cursor
    return params


def _check_limit(limit: Any) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, numbers.Integral) or int(limit) < 1:
        raise ValueError(f"limit must be a positive integer or None, got {limit!r}")
    return int(limit)


def _not_found(id_or_name: str) -> DeepMedChemError:
    return DeepMedChemError(
        f"No optimization named {id_or_name!r}. List yours with `dmc optimize status` or "
        "client.optimizations.list().",
        code="not_found",
        status_code=404,
    )


def _paused_error(resource: OptimizationResource) -> DeepMedChemError:
    reason = f" ({resource.status_reason})" if resource.status_reason else ""
    return DeepMedChemError(
        f"Optimization {resource.name or resource.id} is paused{reason}. Top up your credits, "
        f"then call resume() (CLI: `dmc optimize resume {resource.name or resource.id}`) and "
        "ask again.",
        code="optimization_paused",
        status_code=409,
    )


def _transient(error: DeepMedChemError) -> bool:
    return error.code == "transport_error" or error.status_code in _TRANSIENT_STATUS


def _not_ready_timeout(resource: OptimizationResource, timeout: float) -> DeepMedChemError:
    return DeepMedChemError(
        f"No batch was ready within {timeout:g} s (status: {resource.status}). Ask again later.",
        code="client_timeout",
        retryable=True,
    )


def _ask_delays(deadline: float | None) -> tuple[int, float | None]:
    """Return the long-poll ``wait`` for the next request and the seconds left to the deadline."""

    if deadline is None:
        return MAX_WAIT_SECONDS, None
    remaining = deadline - _monotonic()
    return max(0, min(MAX_WAIT_SECONDS, int(remaining))), remaining


# --- Sync API ----------------------------------------------------------------------


class Optimizations:
    """``client.optimizations``: the optimization endpoints, one method per request."""

    def __init__(self, client: Client):
        self._client = client

    def create(
        self,
        *,
        database: str,
        direction: str,
        budget: int,
        batch_size: int,
        name: str | None = None,
        strategy: str | None = None,
        filters: str | None = None,
        properties: Mapping[str, Any] | None = None,
        seed: int | None = None,
        scorer: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        objective_name: str | None = None,
        units: str | None = None,
        idempotency_key: str | None = None,
    ) -> Optimization:
        """Create an optimization, or return the existing one with the same name and spec."""

        spec = build_specification(
            database=database,
            direction=direction,
            budget=budget,
            batch_size=batch_size,
            name=name,
            strategy=strategy,
            filters=filters,
            properties=properties,
            seed=seed,
            scorer=scorer,
            metadata=metadata,
            objective_name=objective_name,
            units=units,
        )
        payload = self._client._request(
            "POST",
            OPTIMIZATIONS_PATH,
            json=spec,
            headers=_create_headers(name, idempotency_key),
        )
        return Optimization(self._client, OptimizationResource.model_validate(payload))

    def get(self, id_or_name: str) -> Optimization:
        """Return a handle by id (``opt_…``) or by name."""

        if not isinstance(id_or_name, str) or not id_or_name:
            raise ValueError("pass an optimization id (opt_…) or name")
        if id_or_name.startswith("opt_"):
            return Optimization(self._client, self.retrieve(id_or_name))
        for resource in self.list(name=id_or_name):
            if resource.name == id_or_name:
                return Optimization(self._client, resource)
        raise _not_found(id_or_name)

    def list(
        self, *, status: str | None = None, name: str | None = None, limit: int | None = None
    ) -> list[OptimizationResource]:
        """Your optimizations, newest first, following cursors up to ``limit``."""

        limit = _check_limit(limit)
        resources: list[OptimizationResource] = []
        cursor = None
        while True:
            page_limit = (
                MAX_LIST_PAGE if limit is None else min(MAX_LIST_PAGE, limit - len(resources))
            )
            page = Page.model_validate(
                self._client._request(
                    "GET",
                    OPTIMIZATIONS_PATH,
                    params=_list_params(status, name, cursor, page_limit),
                )
            )
            resources.extend(OptimizationResource.model_validate(item) for item in page.data)
            if not page.next_cursor or not page.data:
                break
            if limit is not None and len(resources) >= limit:
                break
            cursor = page.next_cursor
        return resources if limit is None else resources[:limit]

    def retrieve(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            self._client._request("GET", _path(optimization_id))
        )

    def _poll(
        self, optimization_id: str, wait: Any = 0
    ) -> tuple[OptimizationResource, Batch | None, float | None]:
        seconds = _wait_seconds(wait)
        kwargs: dict[str, Any] = {"params": {"wait": seconds}}
        timeout = _long_poll_timeout(self._client._client, seconds)
        if timeout is not None:
            kwargs["timeout"] = timeout
        payload, response = self._client._request_with_response(
            "GET", _path(optimization_id, "/batch"), **kwargs
        )
        return _batch_payload(payload, response)

    def next_batch(
        self, optimization_id: str, *, wait: int = 0
    ) -> tuple[OptimizationResource, Batch | None]:
        """One ``GET …/batch?wait=``: the resource and the pending batch, or ``None``."""

        resource, batch, _ = self._poll(optimization_id, wait)
        return resource, batch

    def submit(
        self,
        optimization_id: str,
        batch_id: str,
        rows: list[dict[str, Any]],
        *,
        scorer: Mapping[str, Any] | None = None,
    ) -> SubmitReceipt:
        """Submit already-normalized rows (see :func:`normalize_scores`) for one batch."""

        body: dict[str, Any] = {"scores": list(rows)}
        scorer_object = _json_object(scorer, "scorer")
        if scorer_object is not None:
            body["scorer"] = scorer_object
        return SubmitReceipt.model_validate(
            self._client._request(
                "POST",
                _path(optimization_id, f"/batches/{_segment(batch_id)}:submit"),
                json=body,
            )
        )

    def results_page(
        self,
        optimization_id: str,
        *,
        order: str = "best",
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Observation], str | None]:
        page = Page.model_validate(
            self._client._request(
                "GET",
                _path(optimization_id, "/results"),
                params=_results_params(order, limit, cursor),
            )
        )
        return [Observation.model_validate(item) for item in page.data], page.next_cursor

    def cancel(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            self._client._request("POST", _path(optimization_id, ":cancel"))
        )

    def resume(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            self._client._request("POST", _path(optimization_id, ":resume"))
        )


class Optimization:
    """A handle on one optimization: ``ask()`` for molecules, ``tell()`` their scores."""

    def __init__(self, client: Client, resource: OptimizationResource):
        self._client = client
        self.resource = resource

    @property
    def _api(self) -> Optimizations:
        return self._client.optimizations

    @property
    def id(self) -> str:
        return self.resource.id

    @property
    def name(self) -> str | None:
        return self.resource.name

    @property
    def status(self) -> str:
        return self.resource.status

    def __repr__(self) -> str:
        progress = self.resource.progress
        return (
            f"Optimization(id={self.id!r}, name={self.name!r}, status={self.status!r}, "
            f"round={self.resource.round}, scored={progress.scored}/{progress.budget})"
        )

    def refresh(self) -> Optimization:
        self.resource = self._api.retrieve(self.id)
        return self

    def ask(self, *, timeout: float | None = None) -> Batch | None:
        """Wait for the next batch to score; ``None`` once the optimization has finished.

        Raises ``DeepMedChemError(code="optimization_paused")`` when proposals are on hold and
        ``code="client_timeout"`` when ``timeout`` seconds pass without a batch.
        """

        deadline = None if timeout is None else _monotonic() + max(0.0, float(timeout))
        failures = 0
        while True:
            wait, _ = _ask_delays(deadline)
            try:
                resource, batch, retry_after = self._api._poll(self.id, wait)
            except DeepMedChemError as error:
                failures += 1
                if not _transient(error) or failures > _ASK_MAX_TRANSIENT_ERRORS:
                    raise
                _sleep(min(30.0, 2.0**failures))
                continue
            failures = 0
            self.resource = resource
            if batch is not None:
                return batch
            if resource.terminal:
                return None
            if resource.status == "paused":
                raise _paused_error(resource)
            _, remaining = _ask_delays(deadline)
            if remaining is not None and remaining <= 0:
                raise _not_ready_timeout(resource, float(timeout or 0))
            delay = 1.0 if retry_after is None else retry_after
            if remaining is not None:
                delay = min(delay, remaining)
            _sleep(delay)

    def tell(
        self,
        batch: Batch | str,
        scores: Any,
        *,
        scorer: Mapping[str, Any] | None = None,
    ) -> SubmitReceipt:
        """Submit the scores of ``batch`` (a :class:`Batch`, or a batch id)."""

        if isinstance(batch, Batch):
            batch_id, rows = batch.id, normalize_scores(batch, scores)
        elif isinstance(batch, str):
            batch_id = batch
            resource, pending = self._api.next_batch(self.id, wait=0)
            self.resource = resource
            if pending is not None and pending.id == batch_id:
                rows = normalize_scores(pending, scores)
            elif isinstance(scores, Mapping):
                rows = _rows_from_mapping(scores)
            else:
                raise ValueError(
                    f"Batch {batch_id} is not the pending batch, so its molecule order is unknown; "
                    "pass the Batch object or scores as a mapping of molecule id to score."
                )
        else:
            raise TypeError(f"batch must be a Batch or a batch id, got {type(batch).__name__}")
        receipt = self._api.submit(self.id, batch_id, rows, scorer=scorer)
        if receipt.optimization is not None:
            self.resource = receipt.optimization
        return receipt

    def results(self, *, order: str = "best", limit: int | None = None) -> OptimizationResult:
        """Every observation (or the first ``limit``), following cursors."""

        limit = _check_limit(limit)
        observations: list[Observation] = []
        cursor = None
        while True:
            size = (
                MAX_RESULTS_PAGE
                if limit is None
                else min(MAX_RESULTS_PAGE, limit - len(observations))
            )
            rows, cursor = self._api.results_page(self.id, order=order, limit=size, cursor=cursor)
            observations.extend(rows)
            if not cursor or not rows or (limit is not None and len(observations) >= limit):
                break
        self.refresh()
        if limit is not None:
            observations = observations[:limit]
        return OptimizationResult(observations=observations, optimization=self.resource)

    def cancel(self) -> Optimization:
        self.resource = self._api.cancel(self.id)
        return self

    def resume(self) -> Optimization:
        self.resource = self._api.resume(self.id)
        return self


# --- Async API -----------------------------------------------------------------------


class AsyncOptimizations:
    """``AsyncClient.optimizations``: the same endpoints as :class:`Optimizations`, awaited."""

    def __init__(self, client: AsyncClient):
        self._client = client

    async def create(
        self,
        *,
        database: str,
        direction: str,
        budget: int,
        batch_size: int,
        name: str | None = None,
        strategy: str | None = None,
        filters: str | None = None,
        properties: Mapping[str, Any] | None = None,
        seed: int | None = None,
        scorer: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        objective_name: str | None = None,
        units: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncOptimization:
        spec = build_specification(
            database=database,
            direction=direction,
            budget=budget,
            batch_size=batch_size,
            name=name,
            strategy=strategy,
            filters=filters,
            properties=properties,
            seed=seed,
            scorer=scorer,
            metadata=metadata,
            objective_name=objective_name,
            units=units,
        )
        payload = await self._client._request(
            "POST",
            OPTIMIZATIONS_PATH,
            json=spec,
            headers=_create_headers(name, idempotency_key),
        )
        return AsyncOptimization(self._client, OptimizationResource.model_validate(payload))

    async def get(self, id_or_name: str) -> AsyncOptimization:
        if not isinstance(id_or_name, str) or not id_or_name:
            raise ValueError("pass an optimization id (opt_…) or name")
        if id_or_name.startswith("opt_"):
            return AsyncOptimization(self._client, await self.retrieve(id_or_name))
        for resource in await self.list(name=id_or_name):
            if resource.name == id_or_name:
                return AsyncOptimization(self._client, resource)
        raise _not_found(id_or_name)

    async def list(
        self, *, status: str | None = None, name: str | None = None, limit: int | None = None
    ) -> list[OptimizationResource]:
        limit = _check_limit(limit)
        resources: list[OptimizationResource] = []
        cursor = None
        while True:
            page_limit = (
                MAX_LIST_PAGE if limit is None else min(MAX_LIST_PAGE, limit - len(resources))
            )
            page = Page.model_validate(
                await self._client._request(
                    "GET",
                    OPTIMIZATIONS_PATH,
                    params=_list_params(status, name, cursor, page_limit),
                )
            )
            resources.extend(OptimizationResource.model_validate(item) for item in page.data)
            if not page.next_cursor or not page.data:
                break
            if limit is not None and len(resources) >= limit:
                break
            cursor = page.next_cursor
        return resources if limit is None else resources[:limit]

    async def retrieve(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            await self._client._request("GET", _path(optimization_id))
        )

    async def _poll(
        self, optimization_id: str, wait: Any = 0
    ) -> tuple[OptimizationResource, Batch | None, float | None]:
        seconds = _wait_seconds(wait)
        kwargs: dict[str, Any] = {"params": {"wait": seconds}}
        timeout = _long_poll_timeout(self._client._client, seconds)
        if timeout is not None:
            kwargs["timeout"] = timeout
        payload, response = await self._client._request_with_response(
            "GET", _path(optimization_id, "/batch"), **kwargs
        )
        return _batch_payload(payload, response)

    async def next_batch(
        self, optimization_id: str, *, wait: int = 0
    ) -> tuple[OptimizationResource, Batch | None]:
        resource, batch, _ = await self._poll(optimization_id, wait)
        return resource, batch

    async def submit(
        self,
        optimization_id: str,
        batch_id: str,
        rows: list[dict[str, Any]],
        *,
        scorer: Mapping[str, Any] | None = None,
    ) -> SubmitReceipt:
        body: dict[str, Any] = {"scores": list(rows)}
        scorer_object = _json_object(scorer, "scorer")
        if scorer_object is not None:
            body["scorer"] = scorer_object
        return SubmitReceipt.model_validate(
            await self._client._request(
                "POST",
                _path(optimization_id, f"/batches/{_segment(batch_id)}:submit"),
                json=body,
            )
        )

    async def results_page(
        self,
        optimization_id: str,
        *,
        order: str = "best",
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Observation], str | None]:
        page = Page.model_validate(
            await self._client._request(
                "GET",
                _path(optimization_id, "/results"),
                params=_results_params(order, limit, cursor),
            )
        )
        return [Observation.model_validate(item) for item in page.data], page.next_cursor

    async def cancel(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            await self._client._request("POST", _path(optimization_id, ":cancel"))
        )

    async def resume(self, optimization_id: str) -> OptimizationResource:
        return OptimizationResource.model_validate(
            await self._client._request("POST", _path(optimization_id, ":resume"))
        )


class AsyncOptimization:
    """The asynchronous twin of :class:`Optimization`."""

    def __init__(self, client: AsyncClient, resource: OptimizationResource):
        self._client = client
        self.resource = resource

    @property
    def _api(self) -> AsyncOptimizations:
        return self._client.optimizations

    @property
    def id(self) -> str:
        return self.resource.id

    @property
    def name(self) -> str | None:
        return self.resource.name

    @property
    def status(self) -> str:
        return self.resource.status

    def __repr__(self) -> str:
        progress = self.resource.progress
        return (
            f"AsyncOptimization(id={self.id!r}, name={self.name!r}, status={self.status!r}, "
            f"round={self.resource.round}, scored={progress.scored}/{progress.budget})"
        )

    async def refresh(self) -> AsyncOptimization:
        self.resource = await self._api.retrieve(self.id)
        return self

    async def ask(self, *, timeout: float | None = None) -> Batch | None:
        deadline = None if timeout is None else _monotonic() + max(0.0, float(timeout))
        failures = 0
        while True:
            wait, _ = _ask_delays(deadline)
            try:
                resource, batch, retry_after = await self._api._poll(self.id, wait)
            except DeepMedChemError as error:
                failures += 1
                if not _transient(error) or failures > _ASK_MAX_TRANSIENT_ERRORS:
                    raise
                await _async_sleep(min(30.0, 2.0**failures))
                continue
            failures = 0
            self.resource = resource
            if batch is not None:
                return batch
            if resource.terminal:
                return None
            if resource.status == "paused":
                raise _paused_error(resource)
            _, remaining = _ask_delays(deadline)
            if remaining is not None and remaining <= 0:
                raise _not_ready_timeout(resource, float(timeout or 0))
            delay = 1.0 if retry_after is None else retry_after
            if remaining is not None:
                delay = min(delay, remaining)
            await _async_sleep(delay)

    async def tell(
        self,
        batch: Batch | str,
        scores: Any,
        *,
        scorer: Mapping[str, Any] | None = None,
    ) -> SubmitReceipt:
        if isinstance(batch, Batch):
            batch_id, rows = batch.id, normalize_scores(batch, scores)
        elif isinstance(batch, str):
            batch_id = batch
            resource, pending = await self._api.next_batch(self.id, wait=0)
            self.resource = resource
            if pending is not None and pending.id == batch_id:
                rows = normalize_scores(pending, scores)
            elif isinstance(scores, Mapping):
                rows = _rows_from_mapping(scores)
            else:
                raise ValueError(
                    f"Batch {batch_id} is not the pending batch, so its molecule order is unknown; "
                    "pass the Batch object or scores as a mapping of molecule id to score."
                )
        else:
            raise TypeError(f"batch must be a Batch or a batch id, got {type(batch).__name__}")
        receipt = await self._api.submit(self.id, batch_id, rows, scorer=scorer)
        if receipt.optimization is not None:
            self.resource = receipt.optimization
        return receipt

    async def results(self, *, order: str = "best", limit: int | None = None) -> OptimizationResult:
        limit = _check_limit(limit)
        observations: list[Observation] = []
        cursor = None
        while True:
            size = (
                MAX_RESULTS_PAGE
                if limit is None
                else min(MAX_RESULTS_PAGE, limit - len(observations))
            )
            rows, cursor = await self._api.results_page(
                self.id, order=order, limit=size, cursor=cursor
            )
            observations.extend(rows)
            if not cursor or not rows or (limit is not None and len(observations) >= limit):
                break
        await self.refresh()
        if limit is not None:
            observations = observations[:limit]
        return OptimizationResult(observations=observations, optimization=self.resource)

    async def cancel(self) -> AsyncOptimization:
        self.resource = await self._api.cancel(self.id)
        return self

    async def resume(self) -> AsyncOptimization:
        self.resource = await self._api.resume(self.id)
        return self


# --- Local score journal -------------------------------------------------------------


def journal_root() -> Path:
    """Where scored-but-unsubmitted batches are kept (``DEEPMEDCHEM_CACHE_DIR`` overrides)."""

    override = os.environ.get("DEEPMEDCHEM_CACHE_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        from platformdirs import user_cache_dir

        base = Path(user_cache_dir("deepmedchem"))
    return base / "optimizations"


def _filename(value: str) -> str:
    return _SAFE_FILENAME.sub("_", value) or "_"


class _Journal:
    """Scores are written here before upload, so a lost upload never costs a re-score."""

    def __init__(self, optimization_id: str):
        self.directory = journal_root() / _filename(optimization_id)
        self.optimization_id = optimization_id

    def path(self, batch_id: str) -> Path:
        return self.directory / f"{_filename(batch_id)}.json"

    def load(self, batch: Batch) -> list[dict[str, Any]] | None:
        path = self.path(batch.id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if (
            not isinstance(rows, list)
            or payload.get("batch_id") != batch.id
            or sorted(str(row.get("id")) for row in rows if isinstance(row, dict))
            != sorted(batch.ids)
        ):
            return None
        return rows

    def save(self, batch: Batch, rows: list[dict[str, Any]], scorer: Any) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(batch.id)
        payload = {
            "optimization_id": self.optimization_id,
            "batch_id": batch.id,
            "round": batch.round,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scorer": scorer,
            "rows": rows,
        }
        handle = tempfile.NamedTemporaryFile(
            "w", dir=self.directory, prefix=".tmp-", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            with handle:
                json.dump(payload, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        return path

    def discard(self, batch_id: str) -> None:
        try:
            self.path(batch_id).unlink()
        except FileNotFoundError:
            pass

    def discard_others(self, keep_batch_id: str) -> None:
        """Remove files of batches that are no longer pending; the server accepted them."""

        if not self.directory.is_dir():
            return
        keep = self.path(keep_batch_id).name
        for path in self.directory.glob("*.json"):
            if path.name != keep:
                try:
                    path.unlink()
                except OSError:
                    pass

    def cleanup(self) -> None:
        try:
            self.directory.rmdir()
        except OSError:
            pass


# --- optimize() ------------------------------------------------------------------------


def _say(enabled: bool, message: str) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"


def _better(direction: str | None, candidate: float, incumbent: float | None) -> bool:
    if incumbent is None:
        return True
    return candidate < incumbent if direction == "minimize" else candidate > incumbent


def _submit_with_retries(
    optimization: Optimization,
    batch_id: str,
    rows: list[dict[str, Any]],
    scorer: Mapping[str, Any] | None,
) -> SubmitReceipt:
    for attempt in range(_SUBMIT_EXTRA_ATTEMPTS + 1):
        try:
            receipt = optimization._api.submit(optimization.id, batch_id, rows, scorer=scorer)
        except DeepMedChemError as error:
            if not _transient(error) or attempt == _SUBMIT_EXTRA_ATTEMPTS:
                raise
            _sleep(2.0 * 2**attempt)
            continue
        if receipt.optimization is not None:
            optimization.resource = receipt.optimization
        return receipt
    raise AssertionError("unreachable")


def _drive(
    optimization: Optimization,
    score_batch: Callable[[Batch], list[dict[str, Any]]],
    *,
    scorer: Mapping[str, Any] | None,
    progress: bool,
    named: bool,
    resume_hint: str,
) -> OptimizationResult:
    """Run the ask → score → journal → submit loop until the optimization finishes."""

    resource = optimization.resource
    label = optimization.name or optimization.id
    if resource.terminal:
        reason = f" ({resource.status_reason})" if resource.status_reason else ""
        if resource.status == "completed":
            _say(progress, f"Optimization {label} has already completed{reason}.")
            return optimization.results()
        raise DeepMedChemError(
            f"Optimization {label} ({optimization.id}) is {resource.status}{reason}. Its results "
            f"stay readable with client.optimizations.get({optimization.id!r}).results(); "
            "choose a new name to start a new optimization.",
            code="optimization_not_active",
        )
    if not named:
        _say(
            True,
            f"Started optimization {optimization.id}. To continue it after an interruption, use "
            f"client.optimizations.get({optimization.id!r}) with ask()/tell(); pass name=... to "
            "make re-running this call resume automatically.",
        )
    elif resource.round > 0 or resource.progress.scored > 0:
        _say(
            progress,
            f"Resuming optimization {label} ({optimization.id}) at round {resource.round}: "
            f"{resource.progress.scored}/{resource.progress.budget} molecules scored.",
        )
    else:
        _say(progress, f"Started optimization {label} ({optimization.id}).")

    direction = resource.direction
    best: float | None = resource.best.score if resource.best is not None else None
    journal = _Journal(optimization.id)
    batch: Batch | None = None
    saved = False
    try:
        while True:
            batch, saved = optimization.ask(), False
            if batch is None:
                break
            journal.discard_others(batch.id)
            rows = journal.load(batch)
            if rows is None:
                rows = score_batch(batch)
                journal.save(batch, rows, scorer)
            else:
                _say(
                    progress,
                    f"Re-submitting the saved scores of batch {batch.id} (scored before an "
                    "interruption).",
                )
            saved = True
            receipt = _submit_with_retries(optimization, batch.id, rows, scorer)
            journal.discard(batch.id)
            saved = False
            for row in rows:
                if row.get("status") == "valid" and row.get("score") is not None:
                    if _better(direction, float(row["score"]), best):
                        best = float(row["score"])
            current = optimization.resource
            if current.best is not None and current.best.score is not None:
                if _better(direction, current.best.score, best):
                    best = current.best.score
            valid = sum(1 for row in rows if row.get("status") == "valid")
            note = " (already accepted)" if receipt.duplicate else ""
            # The receipt is issued before the engine ingests the batch, so the server's
            # count does not include it yet.
            scored = current.progress.scored + (0 if receipt.duplicate else len(rows))
            _say(
                progress,
                f"{label} round {batch.round}: {valid} valid, {len(rows) - valid} failed{note} | "
                f"best so far {_format_score(best)} | "
                f"{scored}/{current.progress.budget} scored",
            )
    except KeyboardInterrupt:
        detail = ""
        if batch is not None and saved:
            detail = f" The scores of batch {batch.id} were saved and will be re-submitted."
        print(
            f"\nInterrupted. Optimization {label} ({optimization.id}) keeps its finished rounds; "
            f"{resume_hint}.{detail}",
            file=sys.stderr,
            flush=True,
        )
        raise
    journal.cleanup()
    result = optimization.results()
    final = optimization.resource
    reason = f" ({final.status_reason})" if final.status_reason else ""
    best_row = result.best
    best_score = best_row.score if best_row is not None else None
    _say(
        progress,
        f"Optimization {label} {final.status}{reason}: {final.progress.scored} scored, "
        f"{final.progress.valid} valid, best {_format_score(best_score)}.",
    )
    return result


def optimize(
    score: Callable[[list[str]], Any],
    *,
    direction: str,
    database: str = "enamine",
    budget: int = 1000,
    batch_size: int = 100,
    name: str | None = None,
    strategy: str | None = None,
    filters: str | None = None,
    properties: Mapping[str, Any] | None = None,
    seed: int | None = None,
    scorer: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    progress: bool = True,
    client: Client | None = None,
) -> OptimizationResult:
    """Optimize ``score`` over a chemical space with Navigator and return every observation.

    ``score`` takes a list of SMILES and returns one value per SMILES, in order: a number, None
    (or NaN) for a molecule it could not score, or a dict ``{"score": x, **metrics}``. If it
    raises, nothing is submitted and the batch stays pending.

    ``name`` makes the call resumable: re-running it after Ctrl-C, a crash or a reboot
    continues the same optimization, and scores that were computed but not uploaded are
    re-submitted from a local journal instead of being recomputed.
    """

    if not callable(score):
        raise TypeError(
            "score must be a function that takes a list of SMILES and returns one score per SMILES"
        )
    _check_direction(direction)
    _check_name(name)
    owned = client is None
    active = Client() if client is None else client
    try:
        optimization = active.optimizations.create(
            database=database,
            direction=direction,
            budget=budget,
            batch_size=batch_size,
            name=name,
            strategy=strategy,
            filters=filters,
            properties=properties,
            seed=seed,
            scorer=scorer,
            metadata=metadata,
        )
        if name is not None:
            hint = f"re-run with name={name!r} to resume"
        else:
            hint = (
                f"continue it with client.optimizations.get({optimization.id!r}) and ask()/tell(), "
                f"or cancel it with `dmc optimize cancel {optimization.id}`"
            )
        return _drive(
            optimization,
            lambda batch: normalize_scores(batch, score(list(batch.smiles))),
            scorer=scorer,
            progress=progress,
            named=name is not None,
            resume_hint=hint,
        )
    finally:
        if owned:
            active.close()
