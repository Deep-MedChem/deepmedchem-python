# Jupyter notebooks

[`enamine_search.ipynb`](enamine_search.ipynb) is a small end-to-end example that searches
Enamine REAL through the DeepMedChem API and uses RDKit to draw the query and a labeled result
grid. It also includes junction-spanning SMILES and genuine SQC-derived SMARTS substructure
queries.

From the repository root:

```bash
pip install -e .
pip install jupyterlab pandas rdkit
jupyter lab examples/notebooks/enamine_search.ipynb
```

Replace the API-key placeholder in the notebook before running it. Do not commit a real key.
