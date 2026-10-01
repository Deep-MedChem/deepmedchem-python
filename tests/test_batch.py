import builtins
import csv
import json

import httpx
import pytest

import deepmedchem.cli as cli
from deepmedchem import Client, DeepMedChemError
from deepmedchem.batch import (
    MAX_BATCH_QUERIES,
    build_run,
    load_state,
    normalize_queries,
    read_queries,
)


class FakeRuns:
    """A minimal API v2 Runs server: BAD fails, EMPTY succeeds without hits."""

    def __init__(self, polls_before_done=1):
        self.requests = []
        self.runs = {}
        self.polls_before_done = polls_before_done

    def _resource(self, run_id):
        run = self.runs[run_id]
        done = run["polls"] > self.polls_before_done
        total = len(run["items"])
        failed = sum(item["references"][0]["structure"]["value"] == "BAD" for item in run["items"])
        return {
            "id": run_id,
            "kind": "selection_batch",
            "status": ("completed_with_errors" if failed else "completed") if done else "running",
            "progress": {
                "total": total,
                "pending": 0 if done else total,
                "running": 0,
                "succeeded": total - failed if done else 0,
                "failed": failed if done else 0,
                "cancelled": 0,
            },
        }

    def _item(self, index, item):
        smiles = item["references"][0]["structure"]["value"]
        if smiles == "BAD":
            return {
                "id": item["id"],
                "input_index": index,
                "status": "failed",
                "error": {"code": "invalid_item", "message": "invalid SMILES"},
            }
        hits = (
            []
            if smiles == "EMPTY"
            else [
                {"rank": 1, "smiles": smiles + "C", "score": 0.9, "price": 163, "product_id": "p1"},
                {
                    "rank": 2,
                    "smiles": smiles + "N",
                    "score": 0.5,
                    "price": None,
                    "product_id": "p2",
                },
            ]
        )
        return {
            "id": item["id"],
            "input_index": index,
            "status": "succeeded",
            "result": {"metric": "ECFP4 Tanimoto", "results": hits},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read() or b"{}")
        self.requests.append((request.method, request.url.path, request.headers, body))
        path = request.url.path
        if path == "/api/v2/runs:estimate":
            return httpx.Response(
                200, json={"admissible": True, "work": {"items": len(body["items"])}}
            )
        if path == "/api/v2/runs" and request.method == "POST":
            run_id = f"run_{len(self.runs) + 1}"
            self.runs[run_id] = {
                "items": body["items"],
                "polls": 0,
                "key": request.headers["idempotency-key"],
            }
            return httpx.Response(202, json=self._resource(run_id))
        run_id = path.split("/")[4]
        if path.endswith("/results"):
            assert request.url.params["order"] == "input"
            items = self.runs[run_id]["items"]
            return httpx.Response(
                200, json={"data": [self._item(i, item) for i, item in enumerate(items)]}
            )
        self.runs[run_id]["polls"] += 1
        return httpx.Response(200, json=self._resource(run_id))

    def created(self):
        return [
            entry for entry in self.requests if entry[0] == "POST" and entry[1] == "/api/v2/runs"
        ]


def _client(fake):
    return Client(api_key="k", api_url="https://example.test", transport=httpx.MockTransport(fake))


# --- input -------------------------------------------------------------------


def test_queries_keep_ids_order_and_duplicates() -> None:
    queries = normalize_queries(["CCO", " CCN ", "CCO"])
    assert [(q.query_id, q.smiles, q.input_index, q.item_id) for q in queries] == [
        ("q1", "CCO", 0, "q000001"),
        ("q2", "CCN", 1, "q000002"),
        ("q3", "CCO", 2, "q000003"),
    ]
    named = normalize_queries({"lead A": "CCO", "lead/B": "CCO"})
    assert [q.query_id for q in named] == ["lead A", "lead/B"]  # caller IDs kept verbatim
    assert [q.item_id for q in named] == ["q000001", "q000002"]  # run IDs always valid


@pytest.mark.parametrize(
    ("queries", "message"),
    [
        (["CCO", "", "  "], r"empty SMILES at position\(s\): #2, #3"),
        ([], "no query molecules"),
        (["C"] * (MAX_BATCH_QUERIES + 1), "at most 1000"),
    ],
)
def test_invalid_query_lists_are_rejected(queries, message) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_queries(queries)


def test_single_string_is_not_a_query_list() -> None:
    with pytest.raises(TypeError, match="list of SMILES"):
        normalize_queries("CCO")


def test_read_smi_with_names_comments_and_blank_lines(tmp_path) -> None:
    source = tmp_path / "series.smi"
    source.write_text("# my series\nCCO ethanol\n\nCCN\nCCO ethanol copy\n", encoding="utf-8")
    assert [(q.query_id, q.smiles) for q in read_queries(source)] == [
        ("ethanol", "CCO"),
        ("q2", "CCN"),
        ("ethanol copy", "CCO"),
    ]


def test_read_csv_detects_columns_and_handles_bom(tmp_path) -> None:
    source = tmp_path / "series.csv"
    source.write_text("﻿Name,SMILES,activity\nA-1,CCO,5\n,CCN,6\n,,\n", encoding="utf-8")
    assert [(q.query_id, q.smiles) for q in read_queries(source)] == [("A-1", "CCO"), ("q2", "CCN")]


def test_read_csv_with_explicit_and_missing_columns(tmp_path) -> None:
    source = tmp_path / "series.tsv"
    source.write_text("code\tstructure_x\nX1\tCCO\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no SMILES column found .* columns: code, structure_x"):
        read_queries(source)
    queries = read_queries(source, smiles_column="structure_x", id_column="code")
    assert [(q.query_id, q.smiles) for q in queries] == [("X1", "CCO")]
    with pytest.raises(ValueError, match="ID column 'nope' not found"):
        read_queries(source, smiles_column="structure_x", id_column="nope")


def test_read_csv_with_an_empty_smiles_cell_is_reported(tmp_path) -> None:
    source = tmp_path / "series.csv"
    source.write_text("id,smiles\nA,CCO\nB,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty SMILES at position"):
        read_queries(source)


def test_read_sdf_uses_names(tmp_path) -> None:
    chem = pytest.importorskip("rdkit.Chem")
    source = tmp_path / "series.sdf"
    with chem.SDWriter(str(source)) as writer:
        for name, smiles in (("ethanol", "CCO"), ("", "CCN")):
            molecule = chem.MolFromSmiles(smiles)
            molecule.SetProp("_Name", name)
            writer.write(molecule)
    assert [(q.query_id, q.smiles) for q in read_queries(source)] == [
        ("ethanol", "CCO"),
        ("q2", "CCN"),
    ]


def test_read_sdf_without_rdkit_explains_the_install(monkeypatch, tmp_path) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("rdkit"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    (tmp_path / "series.sdf").write_text("", encoding="utf-8")
    with pytest.raises(ImportError, match=r"deepmedchem\[rdkit\]"):
        read_queries(tmp_path / "series.sdf")


def test_unknown_or_empty_files_are_rejected(tmp_path) -> None:
    (tmp_path / "series.xlsx").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="use .smi, .txt, .csv, .tsv or .sdf"):
        read_queries(tmp_path / "series.xlsx")
    (tmp_path / "empty.smi").write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="contains no query molecules"):
        read_queries(tmp_path / "empty.smi")


def test_run_has_one_item_per_query_and_limit_per_query() -> None:
    run = build_run(
        normalize_queries(["CCO", "CCO"]), database="enamine-real-v5a", method="shape", limit=7
    )
    payload = run.to_dict()
    template = payload["selection_template"]
    assert template["objectives"][0]["metric_id"] == "cheese.shape"
    assert template["portfolio"]["limit"] == 7  # hits per query
    assert [item["id"] for item in payload["items"]] == ["q000001", "q000002"]
    assert [item["references"][0]["structure"]["value"] for item in payload["items"]] == [
        "CCO",
        "CCO",
    ]
    with pytest.raises(ValueError, match="method"):
        build_run(normalize_queries(["C"]), database="db", method="tanimoto", limit=5)
    with pytest.raises(ValueError, match="positive integer"):
        build_run(normalize_queries(["C"]), database="db", method="morgan", limit=0)


# --- SDK ---------------------------------------------------------------------


def test_search_many_returns_per_query_results_in_input_order(tmp_path) -> None:
    fake = FakeRuns()
    queries = {"a": "CCO", "dup": "CCO", "broken": "BAD", "nothing": "EMPTY"}
    with _client(fake) as client:
        result = client.search_many(queries, database="enamine", limit=2, poll_interval=0)
    assert [item.query.query_id for item in result] == ["a", "dup", "broken", "nothing"]
    assert [item.status for item in result] == ["succeeded", "succeeded", "failed", "succeeded"]
    assert [len(item.hits) for item in result] == [2, 2, 0, 0]
    assert [item.query.query_id for item in result.failed] == ["broken"]
    assert [item.query.query_id for item in result.empty] == ["nothing"]  # not a failure
    assert result.failed[0].error_message == "invalid SMILES"
    assert result.status == "completed_with_errors" and result.database == "enamine-real-v5a"
    assert "4 queries: 3 succeeded (1 without hits), 1 failed" in repr(result)
    ((_, _, headers, body),) = fake.created()
    assert headers["idempotency-key"].startswith("sdk-batch-")
    assert body["selection_template"]["portfolio"]["limit"] == 2


def test_long_table_distinguishes_failed_and_empty_queries(tmp_path) -> None:
    with _client(FakeRuns()) as client:
        result = client.search_many(["CCO", "BAD", "EMPTY"], database="enamine", poll_interval=0)
    rows = result.to_records()
    assert [(row["query_id"], row["query_status"], row["rank"]) for row in rows] == [
        ("q1", "succeeded", 1),
        ("q1", "succeeded", 2),
        ("q2", "failed", None),
        ("q3", "no_hits", None),
    ]
    assert rows[2]["query_error"] == "invalid SMILES" and rows[0]["price"] == 163
    assert result.to_csv(tmp_path / "hits.csv") == 4
    with open(tmp_path / "hits.csv", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header[:5] == ["query_id", "query_smiles", "input_index", "query_status", "query_error"]
    assert result.to_file(tmp_path / "hits.json") == 3
    saved = json.loads((tmp_path / "hits.json").read_text())
    assert [query["status"] for query in saved["queries"]] == ["succeeded", "failed", "succeeded"]
    with pytest.raises(ValueError, match="use .csv or .json"):
        result.to_file(tmp_path / "hits.html")


def test_every_call_starts_a_new_run_and_resume_reconnects(tmp_path) -> None:
    fake = FakeRuns()
    state_file = tmp_path / "batch.json"
    with _client(fake) as client:
        first = client.search_many(
            ["CCO"], database="enamine", state_file=state_file, poll_interval=0
        )
        second = client.search_many(["CCO"], database="enamine", poll_interval=0)
        assert first.run_id != second.run_id  # a deliberate repeat is a new search
        assert first.idempotency_key != second.idempotency_key
        state = load_state(state_file)
        assert (state["run_id"], state["idempotency_key"]) == (first.run_id, first.idempotency_key)
        creates_before = len(fake.created())
        resumed = client.resume_search_many(state_file, poll_interval=0)
    assert len(fake.created()) == creates_before  # reconnecting starts nothing new
    assert resumed.run_id == first.run_id and len(resumed.queries[0].hits) == 2


def test_search_many_never_prompts(monkeypatch) -> None:
    monkeypatch.setattr(builtins, "input", lambda *_: pytest.fail("search_many must not prompt"))
    with _client(FakeRuns()) as client:
        result = client.search_many(
            [f"C{'C' * i}O" for i in range(60)], database="enamine", poll_interval=0
        )
    assert len(result) == 60


def test_classic_catalogues_are_rejected_before_any_request() -> None:
    fake = FakeRuns()
    with (
        _client(fake) as client,
        pytest.raises(DeepMedChemError, match="Batch search is not available for MOLPORT"),
    ):
        client.search_many(["CCO"], database="molport")
    assert fake.requests == []


def test_timeout_names_the_run_to_reconnect(tmp_path) -> None:
    fake = FakeRuns(polls_before_done=10_000)
    with (
        _client(fake) as client,
        pytest.raises(DeepMedChemError, match="run_1; it continues on the server"),
    ):
        client.search_many(
            ["CCO"], database="enamine", state_file=tmp_path / "s.json", timeout=0, poll_interval=0
        )
    assert load_state(tmp_path / "s.json")["run_id"] == "run_1"


def test_resume_rejects_a_state_that_does_not_match_the_run(tmp_path) -> None:
    fake = FakeRuns()
    with _client(fake) as client:
        client.search_many(
            ["CCO", "CCN"], database="enamine", state_file=tmp_path / "s.json", poll_interval=0
        )
        state = load_state(tmp_path / "s.json")
        state["queries"] = state["queries"][:1]
        with pytest.raises(DeepMedChemError, match="has 2 items but the state lists 1"):
            client.resume_search_many(state, poll_interval=0)
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not a deepmedchem batch state file"):
        load_state(tmp_path / "other.json")


# --- CLI ---------------------------------------------------------------------


def _install(monkeypatch, fake):
    monkeypatch.setattr(cli, "_open_client", lambda args: _client(fake))
    monkeypatch.setattr("deepmedchem.batch.time.sleep", lambda *_: None)


def test_cli_batch_search_saves_results_and_state(monkeypatch, capsys, tmp_path) -> None:
    fake = FakeRuns()
    _install(monkeypatch, fake)
    source = tmp_path / "series.smi"
    source.write_text("CCO lead-1\nBAD lead-2\nEMPTY lead-3\nCCO lead-4\n", encoding="utf-8")
    output = tmp_path / "hits.csv"
    code = cli.main(["search", "-i", str(source), "-d", "enamine", "-n", "2", "-o", str(output)])
    out = capsys.readouterr().out
    assert code == 3  # finished, but some queries failed
    assert "Read 4 query molecules" in out and "(1 repeat an earlier SMILES and are kept)" in out
    assert "Estimate: 4 queries x 2 hits on enamine-real-v5a; up to 4 credits" in out
    assert f"state saved to {output}.run.json" in out
    assert "3 succeeded (1 without hits), 1 failed" in out
    assert "lead-3  no hits" in out and "invalid SMILES" in out
    assert f"Saved 6 rows to {output}." in out
    assert load_state(f"{output}.run.json")["queries"][1] == ["lead-2", "BAD"]


def test_cli_large_batch_needs_yes_without_a_terminal(monkeypatch, capsys, tmp_path) -> None:
    fake = FakeRuns()
    _install(monkeypatch, fake)
    source = tmp_path / "series.smi"
    source.write_text("\n".join("C" * (i + 1) for i in range(51)), encoding="utf-8")
    argv = ["search", "-i", str(source), "-d", "enamine"]
    assert cli.main(argv) == 1
    assert "add --yes to run 51 queries without a prompt" in capsys.readouterr().err
    assert fake.created() == []  # nothing submitted
    assert cli.main([*argv, "--yes", "--state", str(tmp_path / "s.json")]) == 0
    assert len(fake.created()) == 1


def test_cli_resume_reconnects_without_a_new_run(monkeypatch, capsys, tmp_path) -> None:
    fake = FakeRuns()
    _install(monkeypatch, fake)
    source = tmp_path / "series.smi"
    source.write_text("CCO\nCCN\n", encoding="utf-8")
    assert (
        cli.main(
            ["search", "-i", str(source), "-d", "enamine", "--state", str(tmp_path / "s.json")]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(["search", "--resume", str(tmp_path / "s.json"), "-o", str(tmp_path / "r.json")])
        == 0
    )
    out = capsys.readouterr().out
    assert "Reconnecting to run run_1 (2 queries)." in out and "Saved 2 queries" in out
    assert len(fake.created()) == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["search", "-d", "enamine"], "give exactly one of"),
        (["search", "CCO", "-i", "x.smi", "-d", "enamine"], "give exactly one of"),
        (["search", "CCO"], "-d/--database"),
        (["search", "-i", "x.smi"], "-d/--database"),
        (["search", "-i", "x.smi", "-d", "molport"], "Batch search is not available for MOLPORT"),
        (["search", "-i", "x.smi", "-d", "enamine", "-o", "hits.sdf"], "saved as .csv or .json"),
    ],
)
def test_cli_rejects_invalid_batch_arguments(monkeypatch, capsys, argv, message) -> None:
    _install(monkeypatch, FakeRuns())
    assert cli.main(argv) == 1
    assert message in capsys.readouterr().err
