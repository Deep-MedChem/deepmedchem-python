"""Exercise the SDK against a local dmc-platform-backend checkout.

This is a release-time cross-repository check, not part of the standalone unit suite.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    args = parser.parse_args()

    sdk_root = Path(__file__).resolve().parents[1]
    backend_root = args.backend.resolve()
    sys.path[:0] = [str(sdk_root / "src"), str(backend_root), str(backend_root / "src")]
    os.environ["AUTH_DISABLED"] = "true"
    os.environ["PLATFORM_ENGINE_FACTORY"] = "tests.fakes:build_engine"

    from dmc_platform_backend.api import app
    from dmc_platform_backend.settings import get_settings
    from fastapi.testclient import TestClient

    from deepmedchem import Client, Run, Selection

    get_settings.cache_clear()
    with TestClient(app) as backend:

        def handler(request: httpx.Request) -> httpx.Response:
            response = backend.request(
                request.method,
                request.url.path,
                params=request.url.params,
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code,
                headers=dict(response.headers),
                content=response.content,
            )

        with Client(
            api_key="contract-test-key",
            api_url="https://contract.test",
            transport=httpx.MockTransport(handler),
        ) as dmc:
            assert dmc.catalog()["libraries"]
            assert dmc.search("CCO", database="enamine-real-v5a", limit=1).results
            assert dmc.search_cheese(
                "CCO", database="enamine-real-v5a", scorer="shape", limit=1
            ).results
            assert dmc.search_substructure(
                "[#6]", database="enamine-real-v5a", limit=1
            ).results
            assert dmc.sample(database="enamine-real-v5a", count=1, seed=42).results

            selection = (
                Selection.from_database("enamine-real-v5a")
                .sample(seed=42)
                .limit(1)
            )
            validation = dmc.selections.validate(selection)
            assert validation.valid
            estimate = dmc.selections.estimate(validation.normalized_selection)
            assert estimate.execution_tier == "synchronous"
            assert dmc.selections.create(estimate.normalized_selection).results

            template = (
                Selection.from_database("enamine-real-v5a")
                .ranked()
                .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
                .limit(1)
            )
            specification = Run.selection_batch(
                template=template,
                items={"lead-001": {"query": "CCO"}},
            )
            assert dmc.runs.estimate(specification)["admissible"] is True
            run = dmc.runs.create(specification, idempotency_key="sdk-contract-v1")
            terminal = dmc.runs.wait(run.id, timeout=10, poll_interval=0.01)
            assert terminal.status == "completed"
            assert next(dmc.runs.iter_results(run.id)).ok

    get_settings.cache_clear()
    print("SDK/backend API v2 contract verification passed")


if __name__ == "__main__":
    main()
