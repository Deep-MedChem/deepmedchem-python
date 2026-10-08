"""Seed a Navigator run with measured in-space scores, then harvest analogs.

Usage: python 05_seed_and_harvest.py seeds.json --name my-seeded-run --direction minimize
The JSON file is a list of {"product_id": "reaction____synthon...", "score": number}.
Use Navigator IDs from the same database/release, not vendor catalog IDs. The
example pauses after preparing analog harvesting; use the usual ask/tell scorer
for subsequent batches. Supply only scores measured with the declared objective.
"""
import argparse
import json
import time
from pathlib import Path

from deepmedchem import Client


def wait_paused(run):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        run.refresh()
        if run.status == "paused":
            if (run.resource.status_reason or "").startswith("control_failed"):
                raise RuntimeError(run.resource.status_reason)
            return
        if run.status in {"completed", "failed", "cancelled", "awaiting_scores"}:
            raise RuntimeError(f"Run is already {run.status}; continue its existing ask/tell loop")
        time.sleep(1)
    raise TimeoutError("Worker has not finished; inspect the run before resuming")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--direction", choices=("minimize", "maximize"), required=True)
    args = parser.parse_args()
    with Client() as client:
        run = client.optimizations.create(
            database=args.database, direction=args.direction, name=args.name,
            budget=500, batch_size=50, start_paused=True,
        )
        wait_paused(run)
        run.add_seeds(json.loads(args.seeds.read_text()), mode="synthon",
                      idempotency_key="initial-seeds")
        wait_paused(run)
        run.transition("analog_harvest_accurate", idempotency_key="initial-harvest")
        wait_paused(run)
        print(f"Prepared {run.id}. Resume it and score batches using your usual scorer.")


if __name__ == "__main__":
    main()
