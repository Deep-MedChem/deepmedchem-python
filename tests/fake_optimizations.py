"""An in-memory emulation of ``/api/v2/optimizations`` (docs/optimizations.md) for MockTransport.

It is deliberately strict where the contract is strict (one row per proposed id, digest-based
duplicates and conflicts, pinned scorer) so the SDK is tested against the same rules as the
platform backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import httpx

TERMINAL = {"completed", "failed", "cancelled"}
_SPEC_FIELDS = {
    "name",
    "database",
    "objective",
    "budget",
    "batch_size",
    "strategy",
    "filters",
    "properties",
    "seed",
    "scorer",
    "metadata",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _error(status: int, code: str, message: str = "", **extra: Any) -> httpx.Response:
    body = {"code": code, "message": message or code, "retryable": False, "request_id": "req_t"}
    body.update(extra)
    return httpx.Response(status, json={"error": body})


class FakeOptimizationServer:
    """Optimizations whose engine proposes instantly (or after ``polls_before_batch`` polls)."""

    def __init__(self, *, polls_before_batch: int = 0, page_size: int | None = None):
        self.polls_before_batch = polls_before_batch
        self.page_size = page_size
        self.optimizations: dict[str, dict[str, Any]] = {}
        self.keys: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.submissions: list[dict[str, Any]] = []
        self.counter = 0

    # --- helpers -----------------------------------------------------------------
    def _molecules(self, state: dict[str, Any], round_index: int) -> list[dict[str, str]]:
        spec = state["spec"]
        remaining = spec["budget"] - state["proposed"]
        count = min(spec["batch_size"], remaining)
        start = state["proposed"]
        molecules = []
        for offset in range(count):
            index = start + offset
            smiles = "C" * (1 + index % 7) + ("O" if index % 2 else "N") + "c1ccccc1"
            molecules.append({"id": f"rxn{round_index}____s{index}____s{offset}", "smiles": smiles})
        return molecules

    def _propose(self, state: dict[str, Any]) -> None:
        resource = state["resource"]
        if state["proposed"] >= state["spec"]["budget"]:
            resource.update(status="completed", status_reason="budget_exhausted")
            resource["pending_batch_id"] = None
            state["batch"] = None
            return
        round_index = resource["round"]
        batch_id = f"{resource['id']}-r{round_index + 1:04d}"
        molecules = self._molecules(state, round_index)
        state["proposed"] += len(molecules)
        state["batch"] = {"id": batch_id, "round": round_index, "molecules": molecules}
        state["polls"] = 0
        resource["progress"]["proposed"] = state["proposed"]
        resource["status"] = "proposing"

    def _best(self, state: dict[str, Any]) -> dict[str, Any] | None:
        valid = [row for row in state["observations"] if row["status"] == "valid"]
        if not valid:
            return None
        minimize = state["spec"]["objective"]["direction"] == "minimize"
        row = (min if minimize else max)(valid, key=lambda item: item["score"])
        return {key: row[key] for key in ("id", "smiles", "score", "round")}

    # --- endpoints ----------------------------------------------------------------
    def create(self, request: httpx.Request) -> httpx.Response:
        spec = json.loads(request.content)
        unknown = set(spec) - _SPEC_FIELDS
        if unknown:
            return _error(422, "invalid_schema", f"unknown fields {sorted(unknown)}")
        if spec.get("objective", {}).get("direction") not in {"minimize", "maximize"}:
            return _error(422, "invalid_schema", "objective.direction")
        normalized = dict(spec)
        normalized.setdefault("strategy", "gamma_diversity_screening")
        normalized.setdefault("seed", 0)
        hashed = {k: v for k, v in normalized.items() if k != "metadata" and v is not None}
        spec_hash = hashlib.sha256(_canonical(hashed).encode()).hexdigest()
        key = request.headers.get("idempotency-key") or spec.get("name")
        if key and key in self.keys:
            existing = self.optimizations[self.keys[key]]
            if existing["resource"]["specification_hash"] != spec_hash:
                return _error(409, "idempotency_conflict", "same key, different specification")
            return httpx.Response(200, json=existing["resource"])
        if spec.get("name") and any(
            state["resource"]["name"] == spec["name"] for state in self.optimizations.values()
        ):
            return _error(409, "idempotency_conflict", "name already used")
        self.counter += 1
        optimization_id = f"opt_test{self.counter:04d}"
        resource = {
            "id": optimization_id,
            "object": "optimization",
            "name": spec.get("name"),
            "status": "initializing",
            "status_reason": None,
            "specification": normalized,
            "specification_hash": spec_hash,
            "round": 0,
            "pending_batch_id": None,
            "progress": {
                "budget": spec["budget"],
                "proposed": 0,
                "scored": 0,
                "valid": 0,
                "failed": 0,
            },
            "best": None,
            "engine": {"version": "0.5.2", "strategy": normalized["strategy"]},
            "created_at": "2026-10-07T12:00:00Z",
            "updated_at": "2026-10-07T12:00:00Z",
            "links": {
                "self": f"/api/v2/optimizations/{optimization_id}",
                "batch": f"/api/v2/optimizations/{optimization_id}/batch",
                "results": f"/api/v2/optimizations/{optimization_id}/results",
            },
        }
        state = {
            "resource": resource,
            "spec": normalized,
            "proposed": 0,
            "batch": None,
            "polls": 0,
            "observations": [],
            "submitted": {},
        }
        self.optimizations[optimization_id] = state
        if key:
            self.keys[key] = optimization_id
        self._propose(state)
        if resource["status"] == "proposing":
            resource["status"] = "initializing"
        return httpx.Response(202, json=resource, headers={"Location": resource["links"]["self"]})

    def list(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        rows = [state["resource"] for state in reversed(list(self.optimizations.values()))]
        if params.get("name"):
            rows = [row for row in rows if row["name"] == params["name"]]
        if params.get("status_filter"):
            rows = [row for row in rows if row["status"] == params["status_filter"]]
        return self._page(rows, params)

    def _page(self, rows: list[dict[str, Any]], params: httpx.QueryParams) -> httpx.Response:
        limit = int(params.get("limit") or 50)
        if self.page_size:
            limit = min(limit, self.page_size)
        start = int(params.get("cursor") or 0)
        page = rows[start : start + limit]
        following = start + limit
        next_cursor = str(following) if following < len(rows) else None
        return httpx.Response(200, json={"data": page, "next_cursor": next_cursor})

    def batch(self, state: dict[str, Any], request: httpx.Request) -> httpx.Response:
        resource = state["resource"]
        busy = {"initializing", "proposing", "ingesting"}
        if resource["status"] in busy and state["batch"] is not None:
            state["polls"] += 1
            if state["polls"] > self.polls_before_batch:
                resource["status"] = "awaiting_scores"
                resource["pending_batch_id"] = state["batch"]["id"]
        status = resource["status"]
        batch = state["batch"] if status == "awaiting_scores" else None
        headers = {}
        if batch is None and status not in TERMINAL:
            headers["Retry-After"] = "2"
        return httpx.Response(
            200,
            json={"status": status, "optimization": resource, "batch": batch},
            headers=headers,
        )

    def submit(self, state: dict[str, Any], batch_id: str, request: httpx.Request):
        body = json.loads(request.content)
        self.submissions.append({"batch_id": batch_id, **body})
        resource = state["resource"]
        pinned = state["spec"].get("scorer")
        if pinned is not None and body.get("scorer") is not None and body["scorer"] != pinned:
            return _error(409, "scorer_changed", "scorer differs from the pinned scorer")
        rows = body.get("scores") or []
        digest = hashlib.sha256(
            _canonical(
                sorted((row["id"], row.get("status", "valid"), row.get("score")) for row in rows)
            ).encode()
        ).hexdigest()
        if batch_id in state["submitted"]:
            if state["submitted"][batch_id] == digest:
                return httpx.Response(
                    200,
                    json={
                        "accepted": True,
                        "duplicate": True,
                        "batch_id": batch_id,
                        "counts": {},
                        "optimization": resource,
                    },
                )
            return _error(409, "submission_conflict", "different scores for this batch")
        if resource["status"] in TERMINAL or resource["status"] == "paused":
            return _error(409, "optimization_not_active", "not active")
        batch = state["batch"]
        if batch is None or batch["id"] != batch_id:
            return _error(409, "batch_not_pending", "not the pending batch")
        expected = {molecule["id"] for molecule in batch["molecules"]}
        ids = [row.get("id") for row in rows]
        missing = sorted(expected - set(ids))
        unknown = sorted(set(ids) - expected)
        duplicate = sorted({i for i in ids if ids.count(i) > 1})
        invalid = []
        for row in rows:
            status = row.get("status") or ("valid" if row.get("score") is not None else "failed")
            score = row.get("score")
            if status == "valid" and (
                not isinstance(score, (int, float)) or not math.isfinite(score)
            ):
                invalid.append(row.get("id"))
            metrics = row.get("metrics") or {}
            if len(metrics) > 32 or any(
                not isinstance(v, (int, float, str, bool, type(None))) for v in metrics.values()
            ):
                invalid.append(row.get("id"))
        if missing or unknown or duplicate or invalid:
            return _error(
                422,
                "invalid_scores",
                "the submission must contain exactly one row per proposed id",
                details={
                    "missing": missing[:20],
                    "unknown": unknown[:20],
                    "duplicate": duplicate[:20],
                    "invalid": invalid[:20],
                },
            )
        smiles = {molecule["id"]: molecule["smiles"] for molecule in batch["molecules"]}
        counts: dict[str, int] = {}
        for row in rows:
            status = row.get("status") or ("valid" if row.get("score") is not None else "failed")
            counts[status] = counts.get(status, 0) + 1
            state["observations"].append(
                {
                    "id": row["id"],
                    "smiles": smiles[row["id"]],
                    "score": row.get("score") if status == "valid" else None,
                    "status": status,
                    "round": batch["round"],
                    "metrics": row.get("metrics") or {},
                }
            )
        state["submitted"][batch_id] = digest
        progress = resource["progress"]
        progress["scored"] += len(rows)
        progress["valid"] += counts.get("valid", 0)
        progress["failed"] += len(rows) - counts.get("valid", 0)
        resource["round"] += 1
        resource["best"] = self._best(state)
        resource["status"] = "ingesting"
        state["batch"] = None
        resource["pending_batch_id"] = None
        self._propose(state)
        if resource["status"] == "proposing":
            resource["status"] = "ingesting"
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "duplicate": False,
                "batch_id": batch_id,
                "counts": counts,
                "optimization": resource,
            },
        )

    def results(self, state: dict[str, Any], request: httpx.Request) -> httpx.Response:
        params = request.url.params
        rows = list(state["observations"])
        if params.get("order", "best") == "best":
            minimize = state["spec"]["objective"]["direction"] == "minimize"
            valid = [row for row in rows if row["status"] == "valid"]
            valid.sort(key=lambda row: row["score"], reverse=not minimize)
            rows = valid + [row for row in rows if row["status"] != "valid"]
        return self._page(rows, params)

    def cancel(self, state: dict[str, Any]) -> httpx.Response:
        resource = state["resource"]
        if resource["status"] not in TERMINAL:
            resource.update(status="cancelled", status_reason="user", pending_batch_id=None)
            state["batch"] = None
        return httpx.Response(200, json=resource)

    def resume(self, state: dict[str, Any]) -> httpx.Response:
        resource = state["resource"]
        if resource["status"] != "paused":
            return _error(409, "optimization_not_paused", "only a paused optimization resumes")
        resource.update(status="proposing", status_reason=None)
        if state["batch"] is None:
            self._propose(state)
        return httpx.Response(200, json=resource)

    def pause(self, optimization_id: str, reason: str = "insufficient_credits") -> None:
        resource = self.optimizations[optimization_id]["resource"]
        resource.update(status="paused", status_reason=reason)

    # --- router -----------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.headers.get("x-api-key") is None:
            return _error(401, "unauthorized")
        if path == "/api/v2/optimizations":
            return self.create(request) if request.method == "POST" else self.list(request)
        match = re.fullmatch(r"/api/v2/optimizations/([^/:]+)(.*)", path)
        if not match:
            return _error(404, "not_found", f"unstubbed route: {path}")
        optimization_id, rest = match.groups()
        state = self.optimizations.get(optimization_id)
        if state is None:
            return _error(404, "not_found", "no such optimization")
        if rest == "" and request.method == "GET":
            return httpx.Response(200, json=state["resource"])
        if rest == "/batch" and request.method == "GET":
            return self.batch(state, request)
        submit = re.fullmatch(r"/batches/([^/:]+):submit", rest)
        if submit and request.method == "POST":
            return self.submit(state, submit.group(1), request)
        if rest == "/results" and request.method == "GET":
            return self.results(state, request)
        if rest == ":cancel" and request.method == "POST":
            return self.cancel(state)
        if rest == ":resume" and request.method == "POST":
            return self.resume(state)
        return _error(404, "not_found", f"unstubbed route: {request.method} {path}")
