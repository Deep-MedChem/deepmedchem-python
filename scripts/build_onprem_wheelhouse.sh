#!/usr/bin/env bash
# Build the on-prem pilot wheel and an offline, hash-locked wheelhouse for
# CPython 3.11 on linux x86_64 (glibc >= 2.28). Downloads from PyPI; uploads nothing.
#
#   scripts/build_onprem_wheelhouse.sh OUT_DIR [PYTHON3.11]
#
# Install offline with:
#   python3.11 -m venv venv
#   venv/bin/pip install --no-index --find-links OUT_DIR --require-hashes -r OUT_DIR/requirements.lock
set -euo pipefail

out=${1:?usage: $0 OUT_DIR [PYTHON3.11]}
python=${2:-python3.11}
root=$(cd "$(dirname "$0")/.." && pwd)
platform=x86_64-manylinux_2_28
pip_platform=(--platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64
  --platform manylinux2014_x86_64 --platform linux_x86_64)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

mkdir -p "$out"
rm -f "$out"/*.whl "$out"/requirements.lock "$out"/SHA256SUMS
uv build --wheel --out-dir "$build/dist" "$root"
wheel=$(ls "$build"/dist/deepmedchem-*.whl)
cp "$wheel" "$out/"

uv pip compile "$root/pyproject.toml" --quiet --no-header --generate-hashes \
  --python-version 3.11 --python-platform "$platform" -o "$build/deps.lock"
"$python" -m pip download --quiet --no-deps --only-binary=:all: --require-hashes \
  --python-version 3.11 --implementation cp --abi cp311 --abi abi3 --abi none \
  "${pip_platform[@]}" -r "$build/deps.lock" -d "$out"

version=$(basename "$wheel" | cut -d- -f2)
{
  echo "# deepmedchem $version + runtime dependencies; CPython 3.11, linux x86_64."
  echo "deepmedchem==$version \\"
  echo "    --hash=sha256:$(sha256sum "$wheel" | cut -d' ' -f1)"
  cat "$build/deps.lock"
} >"$out/requirements.lock"
(cd "$out" && sha256sum ./*.whl requirements.lock >SHA256SUMS)
echo "wheelhouse: $out ($(ls "$out"/*.whl | wc -l) wheels)"
