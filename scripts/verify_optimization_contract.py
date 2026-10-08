"""Exercise the SDK, backend and actual Navigator engine together on the toy space.

Run with a Python environment containing the backend and SDK dependencies.
The engine runs in a separate process, so its private dependencies stay isolated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--engine-command", required=True)
    parser.add_argument("--engine-source", type=Path)
    args = parser.parse_args()
    backend_root = args.backend.resolve()
    sys.path[:0] = [
        str(Path(__file__).resolve().parents[1] / "src"),
        str(backend_root),
        str(backend_root / "src"),
    ]
    from dmc_platform_backend.api import app
    from dmc_platform_backend.optimizations.engine import SubprocessNavigatorEngine
    from dmc_platform_backend.optimizations.worker import OptimizationWorker
    from dmc_platform_backend.settings import get_settings
    from fastapi.testclient import TestClient

    from deepmedchem import Client

    with tempfile.TemporaryDirectory() as scratch:
        os.environ.update(
            AUTH_DISABLED="true",
            PLATFORM_ENGINE_FACTORY="tests.fakes:build_engine",
            OPTIMIZATIONS_ENABLED="true",
            OPTIMIZATION_ALLOWED_TENANTS="*",
            OPTIMIZATION_PROPERTIES="MolWt",
            OPTIMIZATION_INPROCESS_WORKER="false",
            OPTIMIZATION_ARTIFACT_URI=Path(scratch, "artifacts").as_uri(),
        )
        get_settings.cache_clear()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        if args.engine_source:
            env["PYTHONPATH"] = str(args.engine_source.resolve() / "src")
        env["DMC_NAV_DEPLOYMENT_MODE"] = "internal"
        space = backend_root / "tests/fixtures/navigator_toy_space"
        with TestClient(app) as backend:

            def handler(request):
                response = backend.request(
                    request.method,
                    request.url.path,
                    params=request.url.params,
                    content=request.content,
                    headers=dict(request.headers),
                )
                return httpx.Response(
                    response.status_code, headers=dict(response.headers), content=response.content
                )

            worker = OptimizationWorker(
                app.state.optimization_store,
                app.state.optimization_artifacts,
                SubprocessNavigatorEngine(args.engine_command, env=env),
                work_dir=Path(scratch, "work"),
                space_overrides={
                    "reactions_path": str(space / "reactions.tsv"),
                    "synthons_path": str(space / "synthons.tsv"),
                },
            )
            with Client(
                api_key="local-test",
                api_url="https://contract.test",
                transport=httpx.MockTransport(handler),
            ) as client:
                run = client.optimizations.create(
                    database="freedom",
                    direction="minimize",
                    budget=8,
                    batch_size=4,
                    name="seeded-contract",
                    start_paused=True,
                    hit_threshold=-1,
                    properties={"MolWt": (None, 130)},
                )
                assert asyncio.run(worker.run_once())
                seeds = [{"product_id": "amide_coupling____acyl_001____amine_001", "score": -3}]
                run.add_seeds(seeds, mode="synthon")
                assert asyncio.run(worker.run_once())
                run.add_seeds(seeds, mode="synthon")  # lost-response retry
                assert not asyncio.run(worker.run_once())
                assert run.refresh().resource.progress.scored == 0
                run.resume()
                assert asyncio.run(worker.run_once())
                batch = run.ask(timeout=1)
                assert batch is not None
                run.transition("analog_harvest_fast", idempotency_key="harvest")
                receipt = run.tell(batch, [-2.0] * len(batch.molecules))
                assert not receipt.duplicate
                assert asyncio.run(worker.run_once())
                assert run.tell(batch, [-2.0] * len(batch.molecules)).duplicate
                assert run.refresh().resource.engine["strategy"] == "analog_harvest_fast"
                results = run.results()
                assert results.best.score == -3
                assert len(results) == len(batch.molecules) + 1
                print(
                    "SDK/backend/real-engine contract passed: seeds, budgets, resume, "
                    "properties, transition, duplicate submission and results"
                )


if __name__ == "__main__":
    main()
