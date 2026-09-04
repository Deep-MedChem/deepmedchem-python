# Live Enamine examples

These examples use query molecules from
`navigator-cheese-search-experiments/benchmark` and call the deployed DeepMedChem API. They need
only the `deepmedchem` package and an API key; RDKit is not required.

Set the key without putting it in source code or shell arguments:

```bash
export DEEPMEDCHEM_API_KEY="..."
python examples/live/enamine_named_drugs.py
python examples/live/enamine_known_product.py
python examples/live/property_and_admet_filters.py
```

`DMC_API_KEY` and the experiment workspace's existing `CHEESE_API_KEY` are also supported. The
result molecules, scores, release identity, reaction identity, and synthons come from the API
response rather than being computed locally.

The named-drug panel uses Aspirin, Caffeine, and Olanzapine from
`benchmark/named_drug_queries.csv`. The known-product example uses `q01` from
`benchmark/queries.csv`, a frozen molecule sampled from Enamine v5a during the retrieval
experiments.

Example named-drug output:

```text
Aspirin: 3 hits
database=enamine-real-v5a release=2026-08-29.1
 1  score=0.9726  product=1522fdaeade8c82a11b56b1c
     O=C(O)Oc1ccccc1C(=O)O
 2  score=0.9719  product=ed4fbbbb70795dd28f1a6189
     COC(=O)Oc1ccccc1C(=O)O
 3  score=0.8713  product=a66b5f29d23ac2a7da671553
     O=C(O)COc1ccccc1C(=O)O
```

Example known-product output:

```text
query=q01 database=enamine-real-v5a release=2026-08-29.1
 1  score=1.0000  exact_query=False  reaction=m_282490  synthons=3
 2  score=1.0000  exact_query=True   reaction=m_282490  synthons=3
 3  score=1.0000  exact_query=False  reaction=m_282490  synthons=3
 4  score=1.0000  exact_query=False  reaction=m_282490  synthons=3
 5  score=0.9608  exact_query=False  reaction=m_282490  synthons=3
```

Morgan fingerprints can give stereochemical variants the same score, so the known-product
example reports exact SMILES equality separately from similarity.

`property_and_admet_filters.py` contains three checked examples:

- seeded sampling with exact assembled-product `lipinski-ro5/v1` enforcement;
- soft hERG acquisition using factorized CP16 before assembly and the pinned
  OpenADMET teacher afterward;
- a hard `pChEMBL <= 5.0` hERG range enforced by that exact product teacher.

The ADMET examples use the durable Runs API. This makes them reliable even on
the first request after a worker deployment, when the teacher model is loaded
into memory, and shows the approximate and exact predictions separately.
