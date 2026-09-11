# Jupyter notebooks

[`enamine_search.ipynb`](enamine_search.ipynb) is a small end-to-end example that searches
Enamine REAL through the DeepMedChem API and uses RDKit to draw the query and a labeled result
grid. It also includes junction-spanning SMILES and genuine SQC-derived SMARTS substructure
queries. Production uses filtered release `2026-09-06.2` (approximately 93.41B source
combinations), with exact substructure search enabled. The notebook sets a longer HTTP
timeout than its server search budget and displays whether a query timed out.

From the repository root:

```bash
pip install -e .
pip install jupyterlab pandas rdkit
jupyter lab examples/notebooks/enamine_search.ipynb
```

Run `dmc login` first, or provide `DEEPMEDCHEM_API_KEY` through your environment. Never
put a real key in a notebook or commit one.
