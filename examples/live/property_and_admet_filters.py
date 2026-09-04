#!/usr/bin/env python3
"""Three production examples: Lipinski filtering and two exact ADMET workflows.

Set ``DEEPMEDCHEM_API_KEY`` and run:

    python examples/live/property_and_admet_filters.py

The Lipinski example is synchronous. ADMET examples use durable runs so the
first lazy load of the pinned OpenADMET teacher cannot exceed the synchronous
request budget.
"""

from __future__ import annotations

from typing import Any

from deepmedchem import Client, Run, Selection

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
HERG = "openadmet-herg-pchembl"


def _durable_selection(
    dmc: Client,
    selection: Selection,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    """Execute one selection durably and return its selection-result document."""

    run = dmc.runs.create(Run.selection(selection), idempotency_key=idempotency_key)
    terminal = dmc.runs.wait(run.id, timeout=180)
    if terminal.status not in {"completed", "completed_with_errors"}:
        raise RuntimeError(f"run {run.id} ended with status {terminal.status}")

    items = list(dmc.runs.iter_results(run.id, order="input"))
    if len(items) != 1 or not items[0].ok or items[0].result is None:
        error = items[0].error if items else "missing run result"
        raise RuntimeError(f"selection failed: {error}")
    return items[0].result


def lipinski_example(dmc: Client) -> None:
    """Sample ten molecules that pass exact assembled-product Lipinski checks."""

    selection = (
        Selection.from_database("enamine-real-v5a")
        .sample(seed=42)
        .require_preset("lipinski-ro5/v1")
        .include("properties")
        .limit(10)
    )
    result = dmc.selections.create(selection)
    assert result.hits, "the Lipinski example returned no molecules"
    for hit in result.hits:
        properties = hit.properties or {}
        assert properties["MolWt"] <= 500
        assert properties["MolLogP"] <= 5
        assert properties["NumHDonors"] <= 5
        assert properties["NumHAcceptors"] <= 10

    print(f"Lipinski: {len(result.hits)} exact-filtered molecules")
    for hit in result.hits[:3]:
        print(
            f"  MW={hit.properties['MolWt']:.1f}  "
            f"logP={hit.properties['MolLogP']:.2f}  {hit.smiles}"
        )


def admet_acquisition_example(dmc: Client) -> None:
    """Use factorized CP16 before assembly, then exact-score every survivor."""

    selection = (
        Selection.from_database("enamine-real-v5a")
        .reference("aspirin", smiles=ASPIRIN)
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="aspirin")
        .acquire_predicted_property(HERG, direction="minimize", keep_fraction=0.25)
        .include("objective_components")
        .limit(10)
    )
    result = _durable_selection(
        dmc,
        selection,
        idempotency_key="property-admet-examples-v1-acquisition",
    )
    hits = result.get("results", [])
    assert hits, "the ADMET acquisition example returned no molecules"
    assert len({hit["smiles"] for hit in hits}) == len(hits)

    print(f"\nADMET acquisition: {len(hits)} unique aspirin analogues")
    for hit in hits[:3]:
        acquisition = hit["acquisition"]
        print(
            f"  CP16={acquisition['approximate_value']:.3f}  "
            f"exact={acquisition['predicted_value']:.3f} pChEMBL  {hit['smiles']}"
        )
    print(f"  funnel={result.get('counts', {})}")


def admet_range_example(dmc: Client) -> None:
    """Keep only molecules accepted by the exact assembled-product hERG model."""

    selection = (
        Selection.from_database("enamine-real-v5a")
        .reference("aspirin", smiles=ASPIRIN)
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="aspirin")
        .where_predicted_property(HERG, lte=5.0, units="pChEMBL")
        .include("objective_components")
        .limit(10)
    )
    result = _durable_selection(
        dmc,
        selection,
        idempotency_key="property-admet-examples-v1-hard-range",
    )
    hits = result.get("results", [])
    values = [hit["predicted_properties"][HERG] for hit in hits]
    assert values, "the hard ADMET example returned no molecules"
    assert all(value <= 5.0 for value in values)
    assert len({hit["smiles"] for hit in hits}) == len(hits)

    print(f"\nHard hERG range: {len(hits)} unique molecules, all exact predictions <= 5.0")
    for hit, value in zip(hits[:3], values[:3]):
        print(f"  exact={value:.3f} pChEMBL  {hit['smiles']}")
    print(f"  funnel={result.get('counts', {})}")


def main() -> None:
    with Client() as dmc:
        lipinski_example(dmc)
        admet_acquisition_example(dmc)
        admet_range_example(dmc)


if __name__ == "__main__":
    main()
