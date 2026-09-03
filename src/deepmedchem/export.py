"""Write molecular results to CSV, SDF, SMILES, or JSON files without extra dependencies.

SDF output is the one exception: it needs RDKit to build a molecule block, so
``write_sdf`` imports it lazily and explains how to install it when missing.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import SearchResult

COMMON_COLUMNS: tuple[str, ...] = (
    "rank",
    "smiles",
    "score",
    "price",
    "product_id",
    "database_id",
    "database_release",
    "reaction_id",
    "metric",
)
FORMATS: tuple[str, ...] = ("csv", "sdf", "smi", "json")
_EXTENSIONS = {
    ".csv": "csv",
    ".sdf": "sdf",
    ".sd": "sdf",
    ".mol": "sdf",
    ".smi": "smi",
    ".smiles": "smi",
    ".txt": "smi",
    ".json": "json",
}


def infer_format(path: str | os.PathLike[str]) -> str:
    """Return the export format implied by ``path``'s extension."""

    suffix = Path(path).suffix.lower()
    try:
        return _EXTENSIONS[suffix]
    except KeyError as error:
        supported = ", ".join(sorted(_EXTENSIONS))
        raise ValueError(
            f"Cannot infer an output format from {str(path)!r}; use one of {supported} "
            "or pass an explicit format."
        ) from error


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def result_columns(records: Sequence[dict[str, Any]]) -> list[str]:
    """Common columns first, then every other key that is set on at least one record."""

    extras: list[str] = []
    for record in records:
        for key, value in record.items():
            if key not in COMMON_COLUMNS and key not in extras and value is not None:
                extras.append(key)
    return [*COMMON_COLUMNS, *extras]


def result_rows(result: SearchResult) -> list[dict[str, Any]]:
    """Flatten typed hits to JSON-friendly dictionaries with a stable column order."""

    records = []
    for hit in result.hits:
        record = dict(hit.raw)
        record.setdefault("database_id", result.database_id)
        record.setdefault("database_release", result.database_release)
        records.append(record)
    columns = result_columns(records)
    return [{column: _cell(record.get(column)) for column in columns} for record in records]


def write_csv(result: SearchResult, path: str | os.PathLike[str]) -> int:
    rows = result_rows(result)
    columns = result_columns(rows)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def write_smi(result: SearchResult, path: str | os.PathLike[str]) -> int:
    hits = result.hits
    with open(path, "w", encoding="utf-8") as handle:
        for hit in hits:
            handle.write(f"{hit.smiles} {hit.product_id or hit.rank}\n")
    return len(hits)


def write_json(result: SearchResult, path: str | os.PathLike[str]) -> int:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result.raw, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return len(result)


def write_sdf(result: SearchResult, path: str | os.PathLike[str]) -> int:
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as error:
        raise ImportError(
            "Writing SDF files requires RDKit. Install it with "
            "`python -m pip install rdkit` (or `pip install 'deepmedchem[sdf]'`), "
            "or export CSV, SMILES, or JSON instead."
        ) from error

    written = 0
    with Chem.SDWriter(str(path)) as writer:
        for row in result_rows(result):
            molecule = Chem.MolFromSmiles(row["smiles"])
            if molecule is None:
                continue
            AllChem.Compute2DCoords(molecule)
            molecule.SetProp("_Name", str(row.get("product_id") or row["smiles"]))
            for key, value in row.items():
                if key == "smiles" or value is None:
                    continue
                molecule.SetProp(key, str(value))
            writer.write(molecule)
            written += 1
    return written


_WRITERS = {
    "csv": write_csv,
    "sdf": write_sdf,
    "smi": write_smi,
    "json": write_json,
}


def write_result(
    result: SearchResult, path: str | os.PathLike[str], *, format: str | None = None
) -> int:
    """Write ``result`` to ``path`` and return the number of molecules written."""

    selected = (format or infer_format(path)).lower()
    try:
        writer = _WRITERS[selected]
    except KeyError as error:
        raise ValueError(
            f"Unsupported export format {selected!r}; expected one of {', '.join(FORMATS)}."
        ) from error
    return writer(result, path)
