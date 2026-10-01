"""Search many query molecules in one durable API v2 run.

Each input record becomes its own run item with its own hit list, so results
keep the caller's query identity (``query_id``) and position (``input_index``).
Duplicate SMILES are kept on purpose: two records with the same structure but
different compound IDs are separate queries.

Resuming is explicit. ``search_many`` always starts a new run (a fresh
idempotency key) and can save a small state file; ``resume_search_many`` reads
that file and reconnects to the same run instead of starting and paying again.
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .models import RunProgress, SearchResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import Client

MAX_BATCH_QUERIES = 1000
STATE_SCHEMA_VERSION = "deepmedchem-batch-state/1"
METHOD_METRICS = {
    "morgan": "rdkit.ecfp4_tanimoto",
    "shape": "cheese.shape",
    "esp": "cheese.electrostatic",
}
_SMILES_COLUMNS = ("smiles", "canonical_smiles", "smi", "structure", "query_smiles")
_ID_COLUMNS = ("id", "name", "compound_id", "molecule_id", "mol_id", "product_id", "title")
_TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled"}
HIT_COLUMNS = ("rank", "smiles", "score", "price", "product_id", "reaction_id", "metric")


@dataclass(frozen=True)
class BatchQuery:
    """One input record: the caller's ID (may repeat), its SMILES and its position."""

    query_id: str
    smiles: str
    input_index: int

    @property
    def item_id(self) -> str:
        """Run item ID: always unique and valid, independent of the caller's ID."""

        return f"q{self.input_index + 1:06d}"


def normalize_queries(
    queries: Iterable[str] | Mapping[str, str] | Iterable[tuple[str, str]] | Iterable[BatchQuery],
) -> list[BatchQuery]:
    """Turn a list of SMILES, a ``{id: smiles}`` mapping, or ``(id, smiles)`` pairs into queries."""

    if isinstance(queries, (str, bytes)):
        raise TypeError("pass a list of SMILES (or a mapping), not a single string")
    pairs: list[tuple[str | None, Any]]
    if isinstance(queries, Mapping):
        pairs = [(str(key), value) for key, value in queries.items()]
    else:
        pairs = []
        for value in queries:
            if isinstance(value, BatchQuery):
                pairs.append((value.query_id, value.smiles))
            elif isinstance(value, tuple) and len(value) == 2:
                pairs.append((None if value[0] is None else str(value[0]), value[1]))
            else:
                pairs.append((None, value))
    problems = [f"#{index + 1}" for index, (_, smiles) in enumerate(pairs) if not _clean(smiles)]
    if problems:
        raise ValueError(f"empty SMILES at position(s): {', '.join(problems[:20])}")
    result = [
        BatchQuery(query_id=query_id or f"q{index + 1}", smiles=_clean(smiles), input_index=index)
        for index, (query_id, smiles) in enumerate(pairs)
    ]
    _check_count(len(result))
    return result


def read_queries(
    path: str | os.PathLike[str],
    *,
    smiles_column: str | None = None,
    id_column: str | None = None,
) -> list[BatchQuery]:
    """Read queries from .smi/.smiles/.txt, .csv/.tsv, or .sdf/.sd (SDF needs RDKit)."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        pairs = _read_table(source, "\t" if suffix == ".tsv" else ",", smiles_column, id_column)
    elif suffix in {".sdf", ".sd"}:
        pairs = _read_sdf(source, id_column)
    elif suffix in {".smi", ".smiles", ".txt", ""}:
        pairs = _read_smi(source)
    else:
        raise ValueError(
            f"Cannot read queries from {str(path)!r}; use .smi, .txt, .csv, .tsv or .sdf."
        )
    if not pairs:
        raise ValueError(f"{source} contains no query molecules")
    return normalize_queries(pairs)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _check_count(count: int) -> None:
    if count == 0:
        raise ValueError("no query molecules given")
    if count > MAX_BATCH_QUERIES:
        raise ValueError(
            f"{count} query molecules given; one batch run accepts at most "
            f"{MAX_BATCH_QUERIES}. Split the input into smaller files."
        )


def _read_smi(path: Path) -> list[tuple[str | None, str]]:
    pairs = []
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split(maxsplit=1)
            pairs.append((parts[1].strip() if len(parts) > 1 else None, parts[0]))
    return pairs


def _pick(columns: Sequence[str], explicit: str | None, candidates: Sequence[str], kind: str):
    if explicit is not None:
        if explicit not in columns:
            raise ValueError(f"{kind} column {explicit!r} not found; columns: {', '.join(columns)}")
        return explicit
    lowered = {column.strip().lower(): column for column in columns}
    return next((lowered[name] for name in candidates if name in lowered), None)


def _read_table(path: Path, delimiter: str, smiles_column, id_column):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = reader.fieldnames or []
        smiles_key = _pick(columns, smiles_column, _SMILES_COLUMNS, "SMILES")
        if smiles_key is None:
            raise ValueError(
                f"no SMILES column found in {path.name}; columns: {', '.join(columns)}. "
                "Name it with smiles_column= (CLI: --smiles-column)."
            )
        id_key = _pick(columns, id_column, _ID_COLUMNS, "ID")
        rows = list(reader)
    pairs: list[tuple[str | None, str]] = []
    for row in rows:
        if not any(_clean(value) for value in row.values()):
            continue  # fully blank line
        pairs.append((_clean(row.get(id_key)) or None if id_key else None, row.get(smiles_key)))
    return pairs


def _read_sdf(path: Path, id_column: str | None):
    try:
        from rdkit import Chem, RDLogger
    except ImportError as error:
        raise ImportError(
            "Reading SDF queries requires RDKit. Install it with "
            "`pip install 'deepmedchem[rdkit]'`, or use a .smi or .csv file."
        ) from error
    RDLogger.DisableLog("rdApp.*")
    try:
        pairs, unreadable = [], []
        for number, molecule in enumerate(Chem.SDMolSupplier(str(path)), start=1):
            if molecule is None:
                unreadable.append(f"#{number}")
                continue
            name = molecule.GetProp(id_column) if id_column and molecule.HasProp(id_column) else ""
            if not name and molecule.HasProp("_Name"):
                name = molecule.GetProp("_Name")
            pairs.append((name.strip() or None, Chem.MolToSmiles(molecule)))
    finally:
        RDLogger.EnableLog("rdApp.*")
    if unreadable:
        raise ValueError(f"unreadable SDF record(s) in {path.name}: {', '.join(unreadable[:20])}")
    return pairs


@dataclass
class BatchQueryResult:
    """The outcome for one query: a hit list, or the reason it failed."""

    query: BatchQuery
    status: str
    result: SearchResult | None = None
    error: dict[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def hits(self):
        return self.result.hits if self.result is not None else ()

    @property
    def error_message(self) -> str | None:
        if not self.error:
            return None
        return str(self.error.get("message") or self.error.get("code") or self.error)


@dataclass
class BatchResult:
    """Results of one batch run, in input order."""

    run_id: str
    idempotency_key: str
    status: str
    database: str
    method: str
    limit: int
    queries: list[BatchQueryResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[BatchQueryResult]:
        return [item for item in self.queries if item.succeeded]

    @property
    def failed(self) -> list[BatchQueryResult]:
        return [item for item in self.queries if item.status in {"failed", "cancelled"}]

    @property
    def empty(self) -> list[BatchQueryResult]:
        """Queries that ran successfully but found no hits (not a failure)."""

        return [item for item in self.succeeded if not item.hits]

    def __len__(self) -> int:
        return len(self.queries)

    def __iter__(self) -> Iterator[BatchQueryResult]:
        return iter(self.queries)

    def __repr__(self) -> str:
        return (
            f"BatchResult({len(self.queries)} queries: {len(self.succeeded)} succeeded "
            f"({len(self.empty)} without hits), {len(self.failed)} failed; "
            f"run={self.run_id!r}, database={self.database!r}, method={self.method!r})"
        )

    def to_records(self) -> list[dict[str, Any]]:
        """One row per hit; queries without hits or that failed get one row without a hit."""

        rows = []
        for item in self.queries:
            base = {
                "query_id": item.query.query_id,
                "query_smiles": item.query.smiles,
                "input_index": item.query.input_index,
                "query_status": "no_hits" if item.succeeded and not item.hits else item.status,
                "query_error": item.error_message,
            }
            if not item.hits:
                rows.append({**base, **{column: None for column in HIT_COLUMNS}})
                continue
            for hit in item.hits:
                rows.append({**base, **{column: getattr(hit, column) for column in HIT_COLUMNS}})
        return rows

    def to_csv(self, path: str | os.PathLike[str]) -> int:
        """Write the long table to CSV and return the number of rows."""

        rows = self.to_records()
        columns = list(rows[0]) if rows else ["query_id"]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)

    def to_dict(self) -> dict[str, Any]:
        """Return the run summary and each query's full result."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "database": self.database,
            "method": self.method,
            "limit": self.limit,
            "queries": [
                {
                    "query_id": item.query.query_id,
                    "query_smiles": item.query.smiles,
                    "input_index": item.query.input_index,
                    "status": item.status,
                    "error": item.error,
                    "result": item.result.raw if item.result is not None else None,
                }
                for item in self.queries
            ],
        }

    def to_json(self, path: str | os.PathLike[str]) -> int:
        """Write :meth:`to_dict` to a JSON file and return the number of queries."""

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        return len(self.queries)

    def to_file(self, path: str | os.PathLike[str]) -> int:
        suffix = Path(path).suffix.lower()
        if suffix == ".csv":
            return self.to_csv(path)
        if suffix == ".json":
            return self.to_json(path)
        raise ValueError(f"Cannot write batch results to {str(path)!r}; use .csv or .json.")

    def to_pandas(self):
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError(
                "BatchResult.to_pandas() requires pandas. Install it with "
                "`python -m pip install pandas`."
            ) from error
        return pd.DataFrame.from_records(self.to_records())


def batch_database(database: str) -> str:
    """Resolve ``database`` and reject classic CHEESE Search catalogues with a clear message."""

    from .client import DeepMedChemError
    from .databases import is_classic_database, resolve_database

    database_id = resolve_database(database)
    if is_classic_database(database_id):
        raise DeepMedChemError(
            f"Batch search is not available for {database_id}: batch runs cover the "
            "DeepMedChem chemical spaces (see `dmc databases`). Search this CHEESE "
            "Search catalogue one molecule at a time with search().",
            code="unsupported_operation",
        )
    return database_id


def build_run(queries: Sequence[BatchQuery], *, database: str, method: str, limit: int):
    """Return the ``selection_batch`` run for ``queries`` (one item per query)."""

    from .selection import Run, Selection

    if method not in METHOD_METRICS:
        raise ValueError(f"method must be one of {', '.join(METHOD_METRICS)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit (hits per query) must be a positive integer")
    template = (
        Selection.from_database(database)
        .ranked()
        .maximize_similarity(METHOD_METRICS[method], reference="query")
        .limit(limit)
    )
    return Run.selection_batch(
        template=template,
        items={query.item_id: {"query": query.smiles} for query in queries},
    )


def make_state(run_id, key, database, method, limit, queries) -> dict[str, Any]:
    """Return the reconnect information for a submitted batch run."""

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "run_id": run_id,
        "idempotency_key": key,
        "database": database,
        "method": method,
        "limit": limit,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queries": [[query.query_id, query.smiles] for query in queries],
    }


def save_state(state: dict[str, Any], path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return target


def load_state(path: str | os.PathLike[str]) -> dict[str, Any]:
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if state.get("schema_version") != STATE_SCHEMA_VERSION or not state.get("run_id"):
        raise ValueError(f"{path} is not a deepmedchem batch state file")
    return state


def wait_and_collect(
    client: Client,
    state: dict[str, Any],
    *,
    timeout: float | None = None,
    poll_interval: float = 1.0,
    on_progress: Callable[[RunProgress], None] | None = None,
) -> BatchResult:
    """Wait for the run in ``state`` to finish and return its results in input order."""

    from .client import DeepMedChemError

    run_id = state["run_id"]
    queries = normalize_queries([tuple(pair) for pair in state["queries"]])
    started = time.monotonic()
    last = None
    while True:
        run = client.runs.retrieve(run_id)
        snapshot = run.progress.model_dump()
        if on_progress is not None and snapshot != last:
            on_progress(run.progress)
        last = snapshot
        if run.status in _TERMINAL:
            break
        if timeout is not None and time.monotonic() - started >= timeout:
            raise DeepMedChemError(
                f"Timed out waiting for run {run_id}; it continues on the server. "
                "Reconnect with resume_search_many() and the saved state.",
                code="client_timeout",
            )
        time.sleep(poll_interval)
    if run.progress.total != len(queries):
        raise DeepMedChemError(
            f"run {run_id} has {run.progress.total} items but the state lists "
            f"{len(queries)} queries",
            code="state_mismatch",
        )
    items = {item.id: item for item in client.runs.iter_results(run_id, order="input")}
    results = []
    for query in queries:
        item = items.get(query.item_id)
        if item is None:
            results.append(BatchQueryResult(query=query, status="missing"))
            continue
        result = SearchResult.model_validate(item.result) if item.ok and item.result else None
        results.append(
            BatchQueryResult(query=query, status=item.status, result=result, error=item.error)
        )
    return BatchResult(
        run_id=run_id,
        idempotency_key=state["idempotency_key"],
        status=run.status,
        database=state["database"],
        method=state["method"],
        limit=state["limit"],
        queries=results,
    )
