# Releasing `deepmedchem`

The initial release is deliberately manual. Do not publish until the repository visibility,
documentation, package name, and PyPI ownership have been explicitly approved.

## One-time setup

1. Confirm that `deepmedchem` is still available on PyPI.
2. In PyPI, configure a pending Trusted Publisher with:
   - PyPI project: `deepmedchem`
   - GitHub owner: `Deep-MedChem`
   - GitHub repository: `deepmedchem-python`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. Create the protected `pypi` environment in GitHub and require an appropriate reviewer.
4. Make the GitHub repository public when the source and documentation are ready.

Trusted Publishing uses GitHub OIDC; no PyPI token or 1Password secret is placed in the repository.

## Release gate

From the repository root:

```bash
ruff check .
pytest
python -m build
twine check dist/*
python scripts/verify_backend_contract.py --backend ../dmc-platform-backend
```

Also install the wheel into a clean virtual environment and run one authenticated `catalog` and
`search` call against the deployment being released.

## Publish

1. Ensure `pyproject.toml` and `src/deepmedchem/__init__.py` have the same version.
2. Commit and push the release state; wait for CI to pass.
3. Create and push the signed `vX.Y.Z` tag.
4. Manually dispatch the `release` workflow from that tag with `publish_pypi=true`.
5. Verify the PyPI project metadata, hashes, provenance, and clean-environment installation.
6. Publish the corresponding GitHub release and update the SDK/API compatibility table.

Dispatching with `publish_pypi=false` only builds and retains a short-lived private workflow
artifact; it cannot upload to PyPI.
