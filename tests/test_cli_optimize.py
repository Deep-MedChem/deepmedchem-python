import csv
import functools
import json
import shlex
import sys

import httpx
import pytest
from fake_optimizations import FakeOptimizationServer

import deepmedchem.cli as cli
from deepmedchem import Client
from deepmedchem import optimization as optimization_module

SCORER = """
import csv, sys

source, target = sys.argv[1], sys.argv[2]
with open(source, newline="") as handle:
    rows = list(csv.DictReader(handle))
with open(target, "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["id", "score", "status", "error", "heavy_atoms", "label"])
    for index, row in enumerate(rows):
        if index == 1:
            writer.writerow([row["id"], "", "failed", "no pose", "", ""])
        else:
            writer.writerow([row["id"], -len(row["smiles"]), "", "", len(row["smiles"]), "ok"])
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPMEDCHEM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(optimization_module, "_sleep", lambda seconds: None)


@pytest.fixture
def server(monkeypatch):
    server = FakeOptimizationServer()
    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            transport=httpx.MockTransport(server),
        ),
    )
    return server


def _command(tmp_path, body=SCORER):
    script = tmp_path / "score.py"
    script.write_text(body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{input}} {{output}}"


def test_run_scores_every_batch_with_the_command(server, tmp_path, capsys) -> None:
    work = tmp_path / "work"
    code = cli.main(
        [
            "optimize",
            "run",
            "cli-demo",
            "-d",
            "cheminfinita",
            "--minimize",
            "--budget",
            "6",
            "--batch-size",
            "3",
            "--druglike",
            "--property",
            "MolWt=:500",
            "--property",
            "TPSA=20:140",
            "--seed",
            "7",
            "--scorer",
            '{"name": "toy", "version": "1"}',
            "--units",
            "kcal/mol",
            "--score-cmd",
            _command(tmp_path),
            "--work-dir",
            str(work),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    spec = json.loads(server.requests[0].content)
    assert spec["database"] == "cheminfinita-2026-02"
    assert spec["objective"] == {"direction": "minimize", "units": "kcal/mol"}
    assert spec["filters"] == "druglike" and spec["seed"] == 7
    assert spec["properties"] == {"MolWt": {"max": 500.0}, "TPSA": {"min": 20.0, "max": 140.0}}
    assert len(server.submissions) == 2
    first = server.submissions[0]
    assert first["scorer"] == {"name": "toy", "version": "1"}
    assert first["scores"][1] == {
        "id": first["scores"][1]["id"],
        "score": None,
        "status": "failed",
        "error": "no pose",
    }
    assert first["scores"][0]["metrics"]["label"] == "ok"
    assert isinstance(first["scores"][0]["metrics"]["heavy_atoms"], int)
    assert sorted(path.name for path in work.iterdir())[0].endswith(".input.csv")
    assert "cli-demo round 0" in captured.err
    assert "rank" in captured.out and "dmc optimize results cli-demo" in captured.out


def test_run_aborts_without_submitting_when_the_command_fails(server, tmp_path, capsys) -> None:
    command = _command(tmp_path, "import sys\nsys.exit(3)\n")
    code = cli.main(
        [
            "optimize",
            "run",
            "broken",
            "-d",
            "enamine",
            "--maximize",
            "--budget",
            "4",
            "--batch-size",
            "2",
            "--score-cmd",
            command,
        ]
    )
    assert code == 1
    assert "exited with status 3" in capsys.readouterr().err
    assert server.submissions == []
    assert server.optimizations["opt_test0001"]["resource"]["status"] == "awaiting_scores"


def test_run_requires_placeholders(server, capsys) -> None:
    code = cli.main(
        ["optimize", "run", "x", "-d", "enamine", "--maximize", "--score-cmd", "./dock.sh"]
    )
    assert code == 1
    assert "{input} and {output}" in capsys.readouterr().err
    assert server.requests == []


def _create(server, name="hpc", budget=4, batch_size=2):
    with Client(
        api_key="token",
        api_url="https://api.example.test",
        transport=httpx.MockTransport(server),
    ) as client:
        return client.optimizations.create(
            database="enamine",
            direction="minimize",
            budget=budget,
            batch_size=batch_size,
            name=name,
        )


def test_ask_tell_round_trip_until_finished(server, tmp_path, capsys) -> None:
    _create(server)
    for round_index in range(2):
        batch_path = tmp_path / f"batch{round_index}.csv"
        assert cli.main(["optimize", "ask", "hpc", "-o", str(batch_path)]) == 0
        assert "dmc optimize tell hpc" in capsys.readouterr().out
        with open(batch_path, newline="") as handle:
            molecules = list(csv.DictReader(handle))
        assert len(molecules) == 2 and set(molecules[0]) == {"id", "smiles"}
        scores_path = tmp_path / f"scores{round_index}.csv"
        with open(scores_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "score"])
            writer.writerow([molecules[0]["id"], "-8.5"])  # the second id is missing
        assert cli.main(["optimize", "tell", "hpc", str(scores_path)]) == 0
        out = capsys.readouterr().out
        assert "Submitted 2 scores" in out and "1 failed, 1 valid" in out
    assert cli.main(["optimize", "ask", "hpc", "-o", str(tmp_path / "none.csv")]) == 4
    assert "nothing left to score" in capsys.readouterr().err
    rows = server.submissions[0]["scores"]
    assert rows[1]["error"] == "not returned by scorer"


def test_tell_resend_with_batch_id_is_a_duplicate(server, tmp_path, capsys) -> None:
    _create(server)
    assert cli.main(["optimize", "ask", "hpc", "--json", "-o", str(tmp_path / "b.csv")]) == 0
    asked = json.loads(capsys.readouterr().out)
    with open(tmp_path / "b.csv", newline="") as handle:
        ids = [row["id"] for row in csv.DictReader(handle)]
    scores = tmp_path / "s.csv"
    scores.write_text("id,score,status\n" + "\n".join(f"{i},-1.0," for i in ids) + "\n")
    assert cli.main(["optimize", "tell", "hpc", str(scores)]) == 0
    capsys.readouterr()
    code = cli.main(["optimize", "tell", "hpc", str(scores), "--batch", asked["batch_id"]])
    assert code == 0
    assert "already accepted" in capsys.readouterr().out


def test_tell_rejects_scores_for_another_batch(server, tmp_path, capsys) -> None:
    _create(server)
    scores = tmp_path / "s.csv"
    scores.write_text("id,score\nnot-in-batch,1.0\n")
    assert cli.main(["optimize", "tell", "hpc", str(scores)]) == 1
    assert "not in batch" in capsys.readouterr().err


def test_tell_requires_id_and_score_columns(server, tmp_path, capsys) -> None:
    _create(server)
    scores = tmp_path / "s.csv"
    scores.write_text("smiles,value\nCCO,1.0\n")
    assert cli.main(["optimize", "tell", "hpc", str(scores)]) == 1
    assert "must have 'id' and 'score' columns" in capsys.readouterr().err


def test_ask_reports_not_ready(monkeypatch, tmp_path, capsys) -> None:
    server = FakeOptimizationServer(polls_before_batch=100)
    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            transport=httpx.MockTransport(server),
        ),
    )
    _create(server)
    assert cli.main(["optimize", "ask", "hpc", "--wait", "0"]) == 3
    assert "No batch is ready yet" in capsys.readouterr().err


def test_status_results_cancel_and_resume(server, tmp_path, capsys) -> None:
    opt = _create(server, budget=4, batch_size=4)
    with Client(
        api_key="token",
        api_url="https://api.example.test",
        transport=httpx.MockTransport(server),
    ) as client:
        handle = client.optimizations.get(opt.id)
        handle.tell(handle.ask(), [-9.0, -3.0, None, {"score": -5.0, "pose": 1}])
    _create(server, name="second")

    assert cli.main(["optimize", "status"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["name", "id", "status", "round", "scored", "best"]
    assert "hpc" in out and "second" in out and "2 optimizations." in out

    assert cli.main(["optimize", "list", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2

    assert cli.main(["optimize", "status", "hpc"]) == 0
    out = capsys.readouterr().out
    assert "completed (budget_exhausted)" in out and "-9.0000" in out
    assert "4/4 (3 valid, 1 failed)" in out

    assert cli.main(["optimize", "status", opt.id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == opt.id

    output = tmp_path / "best.csv"
    assert cli.main(["optimize", "results", "hpc", "--top", "2", "-o", str(output)]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].split() == ["rank", "score", "status", "round", "id", "smiles"]
    assert lines[2].split()[:2] == ["1", "-9.0000"] and lines[3].split()[:2] == ["2", "-5.0000"]
    with open(output, newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2

    assert cli.main(["optimize", "results", "hpc", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 4 and records[-1]["status"] == "failed"

    assert cli.main(["optimize", "resume", "second"]) == 1
    assert "optimization_not_paused" in capsys.readouterr().err
    assert cli.main(["optimize", "cancel", "second"]) == 0
    assert "second: cancelled" in capsys.readouterr().out

    assert cli.main(["optimize", "status", "missing"]) == 1
    assert "No optimization named 'missing'" in capsys.readouterr().err


def test_read_scores_csv_parses_cells(tmp_path) -> None:
    path = tmp_path / "s.csv"
    path.write_text(
        "﻿id,score,status,error,smiles,cnn,pose,extra\n"
        "a,-7.5,,,CCO,0.9,2,\n"
        "b,NaN,,,CCN,,,\n"
        "c,oops,,,CCC,,,\n"
        "d,,timeout,took too long,CCCC,,,x\n"
    )
    rows = cli.read_scores_csv(path)
    assert rows["a"] == {"score": -7.5, "cnn": 0.9, "pose": 2}
    assert rows["b"] == {"score": None}
    assert rows["c"] == {"score": None, "error": "unparseable score 'oops'"}
    assert rows["d"] == {"score": None, "status": "timeout", "error": "took too long", "extra": "x"}
    path.write_text("id,score\na,1\na,2\n")
    with pytest.raises(ValueError, match="duplicate id"):
        cli.read_scores_csv(path)
