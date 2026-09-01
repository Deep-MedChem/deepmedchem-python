import hashlib
import json
import runpy
from pathlib import Path

import httpx

import deepmedchem

EXAMPLES = Path(__file__).parents[1] / "examples" / "docs"


def _hash(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _run_resource():
    return {
        "id": "run_docs_example",
        "object": "run",
        "kind": "selection_batch",
        "status": "completed",
        "progress": {
            "total": 2,
            "pending": 0,
            "running": 0,
            "succeeded": 2,
            "failed": 0,
            "cancelled": 0,
        },
        "last_event_sequence": 1,
        "links": {},
    }


def _handler(request):
    path = request.url.path
    body = json.loads(request.content or b"{}")
    if path in {
        "/api/v2/search",
        "/api/v2/search_cheese",
        "/api/v2/search_substructure",
        "/api/v2/sample",
    }:
        return httpx.Response(200, json={"results": [], "warnings": []})
    if path == "/api/v2/selections:validate":
        return httpx.Response(
            200,
            json={
                "valid": True,
                "normalized_selection": body,
                "selection_hash": _hash(body),
            },
        )
    if path == "/api/v2/selections:estimate":
        return httpx.Response(
            200,
            json={
                "normalized_selection": body,
                "selection_hash": _hash(body),
                "execution_tier": "synchronous",
                "work": {"items": 1},
            },
        )
    if path == "/api/v2/runs:estimate":
        return httpx.Response(200, json={"admissible": True})
    if path == "/api/v2/runs":
        return httpx.Response(202, json=_run_resource())
    if path.endswith("/events"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "sequence": 1,
                        "type": "run.completed",
                        "run_id": "run_docs_example",
                        "status": "completed",
                    }
                ]
            },
        )
    if path.endswith("/results"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "lead-001",
                        "input_index": 0,
                        "status": "succeeded",
                        "attempt_count": 1,
                        "result": {"results": []},
                    }
                ]
            },
        )
    if path == "/api/v2/runs/run_docs_example":
        return httpx.Response(200, json=_run_resource())
    return httpx.Response(404, json={"error": {"message": f"unstubbed route: {path}"}})


def test_every_published_sdk_example_executes(monkeypatch):
    real_client = deepmedchem.Client

    def docs_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        kwargs.setdefault("api_url", "https://docs-contract.invalid")
        return real_client(*args, **kwargs)

    monkeypatch.setenv("DMC_API_KEY", "docs-contract-key")
    monkeypatch.setattr(deepmedchem, "Client", docs_client)

    executed = []
    for path in sorted(EXAMPLES.glob("*.py")):
        runpy.run_path(path, run_name=f"docs_example_{path.stem}")
        executed.append(path.name)

    assert executed == ["durable_runs.py", "python_quickstart.py", "selection_builder.py"]
