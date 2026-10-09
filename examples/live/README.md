# Live Enamine examples

These examples use query molecules from
`navigator-cheese-search-experiments/benchmark` and call the deployed DeepMedChem API. They need
only the `deepmedchem` package and an API key; RDKit is not required.

Production uses filtered Enamine release `2026-09-06.2`, approximately 93.41B source
reagent combinations. The same population applies to similarity, sampling, and exact
substructure search, with prices preserved. The outputs below were captured on
September 11, 2026, using SDK `0.3.0b4`.

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
database=enamine-real-v5a release=2026-09-06.2
   1  score=0.7037  product=d72ff65eda257ec576fbbefd
      O=C(O)Oc1ccccc1C(=O)O
   2  score=0.6667  product=edd180638b6c99968e5e6ec8
      COC(=O)Oc1ccccc1C(=O)O
   3  score=0.6061  product=326b09cf92a37112432f0845
      CC(C)(C)OC(=O)Oc1ccccc1C(=O)O

Caffeine: 3 hits
database=enamine-real-v5a release=2026-09-06.2
   1  score=0.6944  product=882e6902c3d3b97f47695e2b
      Cn1c(=O)c2c(ncn2CCn2c(=O)c3c(ncn3C)n(C)c2=O)n(C)c1=O
   2  score=0.6579  product=cc798914a90cb2acad31bf72
      Cn1c(=O)c2c(ncn2CCCn2c(=O)c3c(ncn3C)n(C)c2=O)n(C)c1=O
   3  score=0.6562  product=b84100ca5ab0eb98b55374f2
      Cn1c(=O)c2c(ncn2Cn2cnc3c2c(=O)n(C)c(=O)n3C)n(C)c1=O

Olanzapine: 3 hits
database=enamine-real-v5a release=2026-09-06.2
   1  score=0.4902  product=1a0e844a37dcd35c0cea4442
      CN1CCN(C2=Nc3ccccc3Sc3ccccc32)CC1
   2  score=0.4107  product=0192ea7bbf51d0077f342668
      Cc1cc(-c2cccc(N3CCN(C)CC3)n2)c(C)s1
   3  score=0.4000  product=a95a715b0df26e3e447c879a
      Cc1cc(-c2cccc(N3CCN(C)CC3)c2)c(C)s1
```

Example known-product output:

```text
query=q01 database=enamine-real-v5a release=2026-09-06.2
 1  score=1.0000  exact_query=True  reaction=rt_01e183270ffcf658bb435941  synthons=3
    product=057e7d6c6ab7fe33ab7a4c77  CCC1(CNC(=O)C(c2n[nH]c(C)c2C)N2CCN(Cc3ccc(F)cc3)C[C@@H]2C)OCCO1
 2  score=1.0000  exact_query=False  reaction=rt_01e183270ffcf658bb435941  synthons=3
    product=d4fabd46e4d011d390c45148  CCC1(CNC(=O)C(c2n[nH]c(C)c2C)N2CCN(Cc3ccc(F)cc3)CC2C)OCCO1
 3  score=0.8571  exact_query=False  reaction=rt_01e183270ffcf658bb435941  synthons=3
    product=48d996b53f8c9e730043d27b  CCC1(CNC(=O)C(c2n[nH]c(C)c2C)N2CCN(Cc3ccccc3)CC2C)OCCO1
 4  score=0.8571  exact_query=False  reaction=rt_01e183270ffcf658bb435941  synthons=3
    product=7d28affd61b1166e24a43625  CCC1(CNC(=O)C(c2n[nH]c(C)c2C)N2CCN(Cc3ccccc3)C[C@H]2C)OCCO1
 5  score=0.8333  exact_query=False  reaction=rt_01e183270ffcf658bb435941  synthons=3
    product=472d72a0f40356005b786d65  CCC1(CNC(=O)C(c2n[nH]cc2C)N2CCN(Cc3ccc(F)cc3)CC2C)OCCO1
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

Run `python examples/docs/substructure_search.py` for junction-spanning SMILES and
SMARTS examples. Complex motifs can need tens of seconds: the example leaves an
HTTP timeout margin above the server search budget.
