import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import deepmedchem
import deepmedchem.facade

EXAMPLES = Path(__file__).parents[1] / "examples" / "docs"
README = Path(__file__).parents[1] / "README.md"


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
    if path == "/api/v2/selections":
        return httpx.Response(
            200,
            json={
                "id": "sel_docs_example",
                "object": "selection",
                "status": "completed",
                "selection_hash": _hash(body),
                "normalized_selection": body,
                "results": [],
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
    monkeypatch.setattr(deepmedchem.facade, "Client", docs_client)

    executed = []
    for path in sorted(EXAMPLES.glob("*.py")):
        runpy.run_path(path, run_name=f"docs_example_{path.stem}")
        executed.append(path.name)

    assert executed == [
        "durable_runs.py",
        "property_filtered_sampling.py",
        "python_quickstart.py",
        "selection_builder.py",
        "substructure_search.py",
    ]


def _fenced_python_block(markdown: str, heading: str) -> str:
    """The first ``python`` block under ``heading``, searched no further than its section."""

    lines = markdown.splitlines(keepends=True)
    if f"{heading}\n" not in lines:
        raise AssertionError(f"{heading} is no longer a heading in README.md")
    start = lines.index(f"{heading}\n")
    for index in range(start + 1, len(lines)):
        # Stopping at the next section matters: without it, retagging this block as
        # ```py would silently compare the example against a later section's snippet.
        if lines[index].startswith("## "):
            break
        if lines[index] == "```python\n":
            end = lines.index("```\n", index + 1)
            return "".join(lines[index + 1 : end])
    raise AssertionError(f"no python block under {heading}")


def test_the_quickstart_example_is_the_readme_quickstart():
    """The runnable example is the README snippet, so neither can drift alone."""

    # The docs are UTF-8; read_text() would otherwise follow a contributor's locale.
    documented = _fenced_python_block(README.read_text(encoding="utf-8"), "## Quickstart")
    assert documented == (EXAMPLES / "python_quickstart.py").read_text(encoding="utf-8")


def test_the_block_helper_stops_at_the_end_of_its_section():
    """A retagged fence must fail loudly, not silently match a later section's block."""

    markdown = (
        "## Quickstart\n\n```py\nnot python-tagged\n```\n\n"
        "## Something else\n\n```python\nwrong_section = True\n```\n"
    )
    with pytest.raises(AssertionError, match="no python block"):
        _fenced_python_block(markdown, "## Quickstart")


def test_the_block_helper_reports_a_renamed_heading():
    with pytest.raises(AssertionError, match="no longer a heading"):
        _fenced_python_block("## Quick start\n\n```python\nx = 1\n```\n", "## Quickstart")


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="EncodingWarning arrived in 3.10"
)
def test_the_parity_check_does_not_depend_on_the_ambient_locale():
    """`read_text()` without an encoding follows the contributor's locale.

    README.md is UTF-8 and contains non-ASCII bytes, so on a cp932 or C locale an
    implicit read raises UnicodeDecodeError. Running the check with
    PYTHONWARNDEFAULTENCODING turns any implicit read into an error.
    """

    root = Path(__file__).parents[1]
    probe = (
        "import tests.test_documented_examples as m; "
        "m.test_the_quickstart_example_is_the_readme_quickstart()"
    )
    completed = subprocess.run(
        [sys.executable, "-W", "error::EncodingWarning", "-c", probe],
        capture_output=True,
        text=True,
        cwd=root,
        env={
            **os.environ,
            "PYTHONWARNDEFAULTENCODING": "1",
            # Both entries are needed and the ambient value is dropped on purpose:
            # root to import tests, root/src for an uninstalled checkout, and an
            # inherited path could let a stale install shadow the tree under test.
            "PYTHONPATH": os.pathsep.join([str(root), str(root / "src")]),
        },
    )
    assert completed.returncode == 0, completed.stderr
