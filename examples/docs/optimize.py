"""Optimize your own score over a chemical space with Navigator.

The scorer is any Python function that takes a list of SMILES and returns one number per
SMILES (None for a molecule it cannot score). This toy score needs no chemistry toolkit; swap
in docking, an ML model or an RDKit property (see examples/optimization/).
"""

from __future__ import annotations

import deepmedchem as dmc
from deepmedchem import Client


def toy_score(smiles: list[str]) -> list[float | None]:
    """Reward aromatic atoms and heteroatoms, penalize size. Higher is better."""

    scores: list[float | None] = []
    for value in smiles:
        if not value:
            scores.append(None)  # a failure is reported, never left out
            continue
        aromatic = sum(character in "cnos" for character in value)
        hetero = sum(character in "NOSnos" for character in value)
        scores.append(aromatic + 0.5 * hetero - 0.1 * len(value))
    return scores


# The whole loop in one call. `name` makes it resumable: re-running this script after
# Ctrl-C or a crash continues the same optimization instead of starting over.
result = dmc.optimize(
    toy_score,
    direction="maximize",
    database="cheminfinita",
    budget=30,
    batch_size=10,
    name="docs-toy-score",
)
for observation in result.top(5):
    print(f"{observation.score:6.2f}  round {observation.round}  {observation.smiles}")

# The same loop with manual control (ask/tell), e.g. to score batches on your own schedule.
with Client() as client:
    optimization = client.optimizations.create(
        database="cheminfinita",
        direction="maximize",
        budget=20,
        batch_size=10,
        name="docs-ask-tell",
    )
    while (batch := optimization.ask()) is not None:  # None once the budget is spent
        receipt = optimization.tell(batch, toy_score(batch.smiles))
        print(f"round {batch.round}: {receipt.counts}")
    print(optimization.results().best)
