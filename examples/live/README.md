# Live Enamine examples

These examples use query molecules from
`navigator-cheese-search-experiments/benchmark` and call the deployed DeepMedChem API. They need
only the `deepmedchem` package and an API key; RDKit is not required.

Set the key without putting it in source code or shell arguments:

```bash
export DEEPMEDCHEM_API_KEY="..."
python examples/live/enamine_named_drugs.py
python examples/live/enamine_known_product.py
```

`DMC_API_KEY` and the experiment workspace's existing `CHEESE_API_KEY` are also supported. The
result molecules, scores, release identity, reaction identity, and synthons come from the API
response rather than being computed locally.

The named-drug panel uses Aspirin, Caffeine, and Olanzapine from
`benchmark/named_drug_queries.csv`. The known-product example uses `q01` from
`benchmark/queries.csv`, a frozen molecule sampled from Enamine v5a during the retrieval
experiments.
