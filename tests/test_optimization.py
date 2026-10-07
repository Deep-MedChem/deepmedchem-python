import asyncio
import json
import math
import re

import httpx
import pytest
from fake_optimizations import FakeOptimizationServer

import deepmedchem as dmc
from deepmedchem import AsyncClient, Batch, Client, DeepMedChemError, normalize_scores
from deepmedchem import optimization as optimization_module
from deepmedchem.models import OptimizationResource, OptimizationResult, SubmitReceipt


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch, tmp_path):
    sleeps = []

    async def no_async_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setenv("DEEPMEDCHEM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(optimization_module, "_sleep", sleeps.append)
    monkeypatch.setattr(optimization_module, "_async_sleep", no_async_sleep)
    return sleeps


@pytest.fixture
def sleeps(_fast_and_isolated):
    return _fast_and_isolated


@pytest.fixture
def server():
    return FakeOptimizationServer()


def _client(handler, **kwargs):
    return Client(
        api_key="token",
        api_url="https://api.example.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _async_client(handler, **kwargs):
    return AsyncClient(
        api_key="token",
        api_url="https://api.example.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _batch(n=3, batch_id="opt_x-r0001"):
    return Batch.model_validate(
        {
            "id": batch_id,
            "round": 0,
            "molecules": [{"id": f"m{i}", "smiles": "C" * (i + 1)} for i in range(n)],
        }
    )


class NumpyLikeFloat:
    """Stands in for numpy.float32: not a float subclass, but convertible."""

    def __init__(self, value):
        self.value = value

    def __float__(self):
        return float(self.value)


# --- create / specification --------------------------------------------------------


def test_create_sends_contract_specification(server) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine",
            direction="minimize",
            budget=20,
            batch_size=5,
            name="kif11-gnina",
            strategy="ga_dcso_v14_screening",
            filters="druglike",
            properties={"MolWt": (None, 500), "TPSA": {"min": 20, "max": 140}},
            seed=3,
            scorer={"name": "gnina", "version": "1.3.1"},
            metadata={"project": "kif11"},
            objective_name="gnina_affinity",
            units="kcal/mol",
        )
    request = server.requests[0]
    body = json.loads(request.content)
    assert request.method == "POST" and request.url.path == "/api/v2/optimizations"
    assert "idempotency-key" not in request.headers  # the name is the key
    assert body == {
        "name": "kif11-gnina",
        "database": "enamine-real-v5a",
        "objective": {"direction": "minimize", "name": "gnina_affinity", "units": "kcal/mol"},
        "budget": 20,
        "batch_size": 5,
        "strategy": "ga_dcso_v14_screening",
        "filters": "druglike",
        "properties": {"MolWt": {"max": 500}, "TPSA": {"min": 20, "max": 140}},
        "seed": 3,
        "scorer": {"name": "gnina", "version": "1.3.1"},
        "metadata": {"project": "kif11"},
    }
    assert opt.id.startswith("opt_") and opt.name == "kif11-gnina"
    assert opt.status == "initializing" and opt.resource.direction == "minimize"
    assert repr(opt).startswith("Optimization(id='opt_test0001'")


@pytest.mark.parametrize(
    ("alias", "database_id"),
    [
        ("enamine", "enamine-real-v5a"),
        ("freedom", "freedom-space-5"),
        ("cheminfinita", "cheminfinita-2026-02"),
        ("private-space-1", "private-space-1"),
    ],
)
def test_create_resolves_database_aliases(server, alias, database_id) -> None:
    with _client(server) as client:
        client.optimizations.create(database=alias, direction="maximize", budget=4, batch_size=2)
    assert json.loads(server.requests[0].content)["database"] == database_id


def test_create_without_name_sends_random_idempotency_key(server) -> None:
    with _client(server) as client:
        client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        client.optimizations.create(
            database="enamine",
            direction="maximize",
            budget=4,
            batch_size=2,
            idempotency_key="my-key",
        )
    first, second = (request.headers.get("idempotency-key") for request in server.requests)
    assert first.startswith("sdk-") and len(first) > 20
    assert second == "my-key"


def test_create_with_same_name_returns_existing_and_conflicts_on_new_spec(server) -> None:
    with _client(server) as client:
        first = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, name="same"
        )
        again = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, name="same"
        )
        with pytest.raises(DeepMedChemError) as error:
            client.optimizations.create(
                database="enamine", direction="maximize", budget=6, batch_size=2, name="same"
            )
    assert again.id == first.id
    assert error.value.code == "idempotency_conflict" and error.value.status_code == 409


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"direction": "min"}, 'Did you mean "minimize"'),
        ({"direction": "MAXIMISE"}, 'Did you mean "maximize"'),
        ({"direction": None}, "direction must be"),
        ({"budget": 0}, "budget must be a positive integer"),
        ({"budget": True}, "budget must be a positive integer"),
        ({"batch_size": 50}, "must not exceed budget"),
        ({"filters": "lipinski"}, "filters must be None or 'druglike'"),
        ({"properties": {"MolWt": 500}}, "(min, max) pair"),
        ({"properties": {"MolWt": (None, None)}}, "needs a min, a max"),
        ({"properties": {"MolWt": {"maximum": 5}}}, "accepts only 'min' and 'max'"),
        ({"properties": {"MolWt": (600, 500)}}, "min greater than max"),
        ({"seed": -1}, "seed must be a non-negative integer"),
        ({"name": "has space"}, "name must be 1-128 characters"),
        ({"name": "opt_abc"}, "reserved for optimization ids"),
        ({"scorer": {"blob": "x" * 5000}}, "at most 4096 bytes"),
        ({"scorer": "gnina"}, "scorer must be a mapping"),
        ({"metadata": {"x": float("nan")}}, "only JSON values"),
    ],
)
def test_create_validates_arguments_before_any_request(server, kwargs, message) -> None:
    arguments = {"database": "enamine", "direction": "maximize", "budget": 10, "batch_size": 5}
    arguments.update(kwargs)
    with _client(server) as client, pytest.raises(ValueError, match=re.escape(message)):
        client.optimizations.create(**arguments)
    assert server.requests == []


def test_api_error_envelope_is_exposed(server) -> None:
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "capability_unavailable",
                    "message": "strategy not offered",
                    "field": "strategy",
                    "retryable": False,
                    "request_id": "req_1",
                }
            },
        )

    with _client(handler) as client, pytest.raises(DeepMedChemError) as error:
        client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, strategy="nope"
        )
    assert error.value.code == "capability_unavailable"
    assert error.value.field == "strategy" and error.value.request_id == "req_1"


# --- get / list / retrieve / cancel / resume ---------------------------------------------


def test_get_by_id_and_by_name(server) -> None:
    with _client(server) as client:
        created = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, name="by-name"
        )
        by_id = client.optimizations.get(created.id)
        by_name = client.optimizations.get("by-name")
        with pytest.raises(DeepMedChemError) as error:
            client.optimizations.get("missing")
    assert by_id.id == by_name.id == created.id
    assert server.requests[1].url.path == f"/api/v2/optimizations/{created.id}"
    assert server.requests[2].url.params["name"] == "by-name"
    assert error.value.code == "not_found"


def test_list_follows_cursors_and_respects_limit_and_filters() -> None:
    server = FakeOptimizationServer(page_size=2)
    with _client(server) as client:
        for index in range(5):
            client.optimizations.create(
                database="enamine",
                direction="maximize",
                budget=4,
                batch_size=2,
                name=f"run-{index}",
            )
        server.requests.clear()
        everything = client.optimizations.list()
        three = client.optimizations.list(limit=3)
        initializing = client.optimizations.list(status="initializing", limit=1)
    assert [resource.name for resource in everything] == [f"run-{i}" for i in range(4, -1, -1)]
    assert [resource.name for resource in three] == ["run-4", "run-3", "run-2"]
    assert all(isinstance(resource, OptimizationResource) for resource in everything)
    assert initializing[0].status == "initializing"
    params = [dict(request.url.params) for request in server.requests]
    assert params[0] == {"limit": "200"}
    assert params[1] == {"limit": "200", "cursor": "2"}
    assert params[-1] == {"limit": "1", "status_filter": "initializing"}


def test_cancel_resume_and_refresh(server) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        with pytest.raises(DeepMedChemError) as error:
            opt.resume()
        assert error.value.code == "optimization_not_paused"
        server.pause(opt.id)
        assert opt.refresh().status == "paused"
        assert opt.resume().status == "proposing"
        assert opt.cancel().status == "cancelled"
        assert client.optimizations.retrieve(opt.id).terminal
        assert client.optimizations.cancel(opt.id).status == "cancelled"  # idempotent
    paths = [(request.method, request.url.path) for request in server.requests]
    assert ("POST", f"/api/v2/optimizations/{opt.id}:resume") in paths
    assert ("POST", f"/api/v2/optimizations/{opt.id}:cancel") in paths


# --- ask / next_batch -------------------------------------------------------------------


def test_next_batch_is_one_request_and_none_until_proposed(sleeps) -> None:
    server = FakeOptimizationServer(polls_before_batch=1)
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        resource, batch = client.optimizations.next_batch(opt.id, wait=99)
        assert batch is None and resource.status == "initializing"
        resource, batch = client.optimizations.next_batch(opt.id)
    assert batch is not None and len(batch) == 2 and batch.id == resource.pending_batch_id
    assert server.requests[1].url.params["wait"] == "25"  # clamped to the contract maximum
    assert server.requests[2].url.params["wait"] == "0"
    assert sleeps == []


def test_ask_keeps_polling_and_honours_retry_after(sleeps) -> None:
    server = FakeOptimizationServer(polls_before_batch=3)
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        batch = opt.ask()
    assert isinstance(batch, Batch) and batch.round == 0 and len(batch.smiles) == 2
    assert batch.ids == [molecule.id for molecule in batch]
    assert opt.status == "awaiting_scores"
    assert sleeps == [2.0, 2.0, 2.0]
    assert [request.url.params["wait"] for request in server.requests[1:]] == ["25"] * 4


def test_ask_extends_read_timeout_for_long_poll(server) -> None:
    with _client(server, timeout=5.0) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        opt.ask()
    timeout = server.requests[-1].extensions["timeout"]
    assert timeout["read"] >= 40 and timeout["connect"] == 5.0


def test_ask_returns_none_when_terminal_and_raises_when_paused(server) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        server.pause(opt.id)
        with pytest.raises(DeepMedChemError) as error:
            opt.ask()
        assert error.value.code == "optimization_paused"
        assert "resume" in str(error.value)
        opt.cancel()
        assert opt.ask() is None


def test_ask_timeout_raises_client_timeout(monkeypatch, sleeps) -> None:
    clock = [0.0]
    monkeypatch.setattr(optimization_module, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(optimization_module, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    server = FakeOptimizationServer(polls_before_batch=100)
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        with pytest.raises(DeepMedChemError) as error:
            opt.ask(timeout=5)
    assert error.value.code == "client_timeout"
    waits = [request.url.params["wait"] for request in server.requests[1:]]
    assert waits[0] == "5" and waits[-1] == "0"


def test_ask_survives_transient_errors(sleeps) -> None:
    server = FakeOptimizationServer()
    failures = [2]

    def flaky(request):
        if request.url.path.endswith("/batch") and failures[0]:
            failures[0] -= 1
            raise httpx.ConnectError("boom", request=request)
        return server(request)

    with _client(flaky, max_retries=0) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2
        )
        assert opt.ask() is not None
    assert sleeps == [2.0, 4.0]


# --- tell / submit -----------------------------------------------------------------------


def test_tell_normalizes_and_submits_one_row_per_molecule(server) -> None:
    scorer = {"name": "toy", "version": "1"}
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine",
            direction="maximize",
            budget=6,
            batch_size=3,
            scorer=scorer,
        )
        batch = opt.ask()
        receipt = opt.tell(batch, [1.5, None, {"score": 2, "cnn": 0.8}], scorer=scorer)
        again = client.optimizations.submit(
            opt.id, batch.id, server.submissions[0]["scores"], scorer=scorer
        )
    assert isinstance(receipt, SubmitReceipt)
    assert (
        receipt.accepted and not receipt.duplicate and receipt.counts == {"valid": 2, "failed": 1}
    )
    assert again.duplicate
    assert opt.resource.round == 1
    submitted = server.submissions[0]
    assert submitted["scorer"] == scorer
    assert submitted["scores"] == [
        {"id": batch.ids[0], "score": 1.5, "status": "valid"},
        {"id": batch.ids[1], "score": None, "status": "failed"},
        {"id": batch.ids[2], "score": 2.0, "status": "valid", "metrics": {"cnn": 0.8}},
    ]
    path = server.requests[-1].url.path
    assert path == f"/api/v2/optimizations/{opt.id}/batches/{batch.id}:submit"


def test_tell_with_mapping_and_batch_id(server) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="minimize", budget=4, batch_size=2
        )
        batch = opt.ask()
        receipt = opt.tell(batch.id, {batch.ids[1]: -7.0})
        # Retrying the same partial mapping after the batch left "pending" (a lost
        # response) rebuilds the same rows from the issued batch: a duplicate, not a conflict.
        retried = opt.tell(batch.id, {batch.ids[1]: -7.0})
        assert retried.duplicate
        with pytest.raises(DeepMedChemError) as conflict:
            opt.tell(batch.id, [1.0, 2.0])
        assert conflict.value.code == "submission_conflict"
        duplicate = opt.tell(batch.id, {batch.ids[0]: None, batch.ids[1]: -7.0})
    rows = server.submissions[0]["scores"]
    assert rows[0] == {
        "id": batch.ids[0],
        "score": None,
        "status": "failed",
        "error": "not returned by scorer",
    }
    assert rows[1]["score"] == -7.0
    assert receipt.counts == {"failed": 1, "valid": 1}
    assert duplicate.duplicate


def test_scorer_change_is_refused(server) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine",
            direction="minimize",
            budget=4,
            batch_size=2,
            scorer={"name": "gnina", "version": "1.3"},
        )
        batch = opt.ask()
        with pytest.raises(DeepMedChemError) as error:
            opt.tell(batch, [1, 2], scorer={"name": "gnina", "version": "1.4"})
    assert error.value.code == "scorer_changed"


# --- results ---------------------------------------------------------------------------


def test_results_follow_cursors_and_rank_by_direction(tmp_path) -> None:
    server = FakeOptimizationServer(page_size=3)
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="minimize", budget=8, batch_size=4
        )
        opt.tell(opt.ask(), [-5.0, None, -9.0, {"score": -1, "pose": 2, "ok": True}])
        opt.tell(opt.ask(), [float("nan"), -3.0, -11.0, 0.0])
        result = opt.results()
        limited = opt.results(limit=4, order="round")
        page, cursor = client.optimizations.results_page(opt.id, limit=2)
    assert isinstance(result, OptimizationResult) and len(result) == 8
    assert [row.score for row in result.top(3)] == [-11.0, -9.0, -5.0]
    assert result.best.score == -11.0 and result.direction == "minimize"
    assert [row.status for row in result][-2:] == ["failed", "failed"]
    assert len(limited) == 4 and limited[0].round == 0
    assert len(page) == 2 and cursor == "2"
    records = result.to_records()
    with_metrics = next(record for record in records if record["score"] == -1.0)
    assert with_metrics["pose"] == 2 and with_metrics["ok"] is True
    path = tmp_path / "results.csv"
    assert result.to_csv(path) == 8
    header = path.read_text().splitlines()[0].split(",")
    assert header[:6] == ["id", "smiles", "score", "status", "round", "error"]
    assert "pose" in header
    assert "best=-11" in repr(result)
    with pytest.raises(ValueError):
        opt.results(order="score")


def test_result_top_without_resource_keeps_server_order() -> None:
    result = OptimizationResult.model_validate(
        {"observations": [{"id": "a", "score": 1.0}, {"id": "b", "score": 3.0}]}
    )
    assert [row.id for row in result.top()] == ["a", "b"]


# --- normalize_scores ------------------------------------------------------------------------


def test_normalize_scores_numbers_none_nan_and_numpy_like() -> None:
    rows = normalize_scores(_batch(5), [1, NumpyLikeFloat(2.5), None, float("nan"), float("-inf")])
    assert rows[0] == {"id": "m0", "score": 1.0, "status": "valid"}
    assert rows[1] == {"id": "m1", "score": 2.5, "status": "valid"}
    assert rows[2] == {"id": "m2", "score": None, "status": "failed"}
    assert rows[3] == {
        "id": "m3",
        "score": None,
        "status": "failed",
        "error": "non-finite score (nan)",
    }
    assert rows[4]["status"] == "failed" and "inf" in rows[4]["error"]
    assert all(isinstance(row["score"], (float, type(None))) for row in rows)


def test_normalize_scores_accepts_any_iterable_of_matching_length() -> None:
    rows = normalize_scores(_batch(3), (value for value in (1.0, 2.0, 3.0)))
    assert [row["score"] for row in rows] == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("scores", "error", "message"),
    [
        ([1.0, 2.0], ValueError, "returned 2 values for the 3 molecules"),
        ([True, 1.0, 2.0], TypeError, "is a bool"),
        (["1.0", 1.0, 2.0], TypeError, "is a string"),
        ([[1.0], 1.0, 2.0], TypeError, "must be a number or None"),
        ("123", TypeError, "must be a sequence"),
        (None, TypeError, "must be a sequence"),
        (5, TypeError, "must be a sequence"),
        ({"m0": 1.0, "zzz": 2.0}, ValueError, "not in batch"),
        ([{"score": 1.0, "status": "great"}, 1.0, 2.0], ValueError, "is not one of"),
        ([{"score": 1.0, "poses": [1, 2]}, 1.0, 2.0], TypeError, "Metric 'poses'"),
        ([{"score": 1.0, "id": "m9"}, 1.0, 2.0], ValueError, "different id"),
        ([{"score": 1.0, "metrics": [1]}, 1.0, 2.0], TypeError, "must be a mapping"),
        ([{"score": 1.0, **{f"k{i}": i for i in range(33)}}, 1.0, 2.0], ValueError, "at most 32"),
    ],
)
def test_normalize_scores_rejects_bad_scorer_output(scores, error, message) -> None:
    with pytest.raises(error, match=message):
        normalize_scores(_batch(3), scores)


def test_normalize_scores_mapping_fills_missing_ids() -> None:
    rows = normalize_scores(_batch(3), {"m2": 3.0, "m0": NumpyLikeFloat(1)})
    assert [row["id"] for row in rows] == ["m0", "m1", "m2"]
    assert rows[1] == {
        "id": "m1",
        "score": None,
        "status": "failed",
        "error": "not returned by scorer",
    }
    assert rows[0]["score"] == 1.0 and rows[2]["score"] == 3.0


def test_normalize_scores_dict_rows_status_error_and_metrics() -> None:
    rows = normalize_scores(
        _batch(4),
        [
            {"score": -9.1, "cnn_score": 0.82, "pose": 3, "note": "ok", "flag": None},
            {"score": None, "error": "no pose" * 100},
            {"score": -5.0, "status": "TIMEOUT"},
            {"score": float("nan"), "status": "valid", "metrics": {"m": NumpyLikeFloat(1)}},
        ],
    )
    assert rows[0] == {
        "id": "m0",
        "score": -9.1,
        "status": "valid",
        "metrics": {"cnn_score": 0.82, "pose": 3, "note": "ok", "flag": None},
    }
    assert rows[1]["status"] == "failed" and len(rows[1]["error"]) == 500
    assert rows[2] == {"id": "m2", "score": None, "status": "timeout"}
    assert rows[3]["status"] == "failed" and rows[3]["metrics"] == {"m": 1.0}
    assert "non-finite" in rows[3]["error"]


def test_normalize_scores_converts_nonfinite_metrics_to_null() -> None:
    rows = normalize_scores(_batch(1), [{"score": 1.0, "x": float("inf"), "y": NumpyLikeFloat(2)}])
    assert rows[0]["metrics"] == {"x": None, "y": 2.0}
    json.dumps(rows, allow_nan=False)


# --- optimize() --------------------------------------------------------------------------


def _toy(smiles):
    return [float(len(value)) for value in smiles]


def test_optimize_runs_until_budget_and_reports_progress(server, capsys) -> None:
    with _client(server) as client:
        result = dmc.optimize(
            _toy,
            direction="maximize",
            database="cheminfinita",
            budget=10,
            batch_size=4,
            name="toy",
            client=client,
        )
    assert len(result) == 10 and result.optimization.status == "completed"
    assert result.best.score == max(len(row.smiles) for row in result)
    assert [submission["batch_id"][-5:] for submission in server.submissions] == [
        "r0001",
        "r0002",
        "r0003",
    ]
    err = capsys.readouterr().err
    assert "Started optimization toy" in err
    assert err.count("toy round") == 3
    assert "completed (budget_exhausted)" in err


def test_optimize_quiet_and_unnamed_prints_resume_hint_once(server, capsys) -> None:
    with _client(server) as client:
        dmc.optimize(
            _toy, direction="maximize", budget=4, batch_size=2, progress=False, client=client
        )
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "Started optimization opt_test0001" in err[0]
    assert "client.optimizations.get('opt_test0001')" in err[0]


def test_optimize_scorer_exception_submits_nothing(server) -> None:
    def broken(smiles):
        raise RuntimeError("docking crashed")

    with _client(server) as client, pytest.raises(RuntimeError, match="docking crashed"):
        dmc.optimize(broken, direction="minimize", budget=4, batch_size=2, name="b", client=client)
    assert server.submissions == []


def test_optimize_bad_scorer_output_submits_nothing(server) -> None:
    with _client(server) as client, pytest.raises(ValueError, match="returned 1 values"):
        dmc.optimize(
            lambda smiles: [1.0], direction="minimize", budget=4, batch_size=2, client=client
        )
    assert server.submissions == []


def test_optimize_resumes_from_journal_without_rescoring(server, tmp_path, capsys) -> None:
    calls = []

    def scorer(smiles):
        calls.append(list(smiles))
        return _toy(smiles)

    crash = [True]

    def crashing(request):
        if request.url.path.endswith(":submit") and crash[0]:
            crash[0] = False
            raise KeyboardInterrupt  # dies after the journal write, before the upload
        return server(request)

    with _client(crashing) as client, pytest.raises(KeyboardInterrupt):
        dmc.optimize(scorer, direction="maximize", budget=6, batch_size=3, name="j", client=client)
    err = capsys.readouterr().err
    assert "Interrupted" in err and "re-run with name='j' to resume" in err
    assert "were saved and will be re-submitted" in err
    journal = list((tmp_path / "cache" / "optimizations" / "opt_test0001").glob("*.json"))
    assert len(journal) == 1
    saved = json.loads(journal[0].read_text())
    assert saved["batch_id"] == "opt_test0001-r0001" and len(saved["rows"]) == 3
    assert len(calls) == 1 and server.submissions == []

    with _client(server) as client:
        result = dmc.optimize(
            scorer, direction="maximize", budget=6, batch_size=3, name="j", client=client
        )
    assert len(calls) == 2  # only the second batch was scored
    assert len(result) == 6
    assert server.submissions[0]["scores"] == saved["rows"]
    assert "Re-submitting the saved scores" in capsys.readouterr().err
    assert not (tmp_path / "cache" / "optimizations" / "opt_test0001").exists()


def test_optimize_retries_lost_submit_response_and_accepts_duplicate(server, sleeps) -> None:
    lost = [True]

    def lossy(request):
        response = server(request)
        if request.url.path.endswith(":submit") and lost[0]:
            lost[0] = False
            raise httpx.ReadError("connection reset", request=request)
        return response

    with _client(lossy, max_retries=0) as client:
        result = dmc.optimize(
            _toy,
            direction="maximize",
            budget=4,
            batch_size=2,
            name="lossy",
            progress=False,
            client=client,
        )
    assert len(result) == 4
    assert len(server.submissions) == 3  # the lost one, its duplicate, then round 2
    assert server.submissions[0]["scores"] == server.submissions[1]["scores"]
    assert sleeps == [2.0]


def test_optimize_after_accepted_submit_discards_stale_journal(server, tmp_path) -> None:
    with _client(server) as client:
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, name="stale"
        )
        batch = opt.ask()
        rows = normalize_scores(batch, _toy(batch.smiles))
        optimization_module._Journal(opt.id).save(batch, rows, None)
        opt.tell(batch, _toy(batch.smiles))  # accepted, but the process "died" before cleanup
        result = dmc.optimize(
            _toy,
            direction="maximize",
            budget=4,
            batch_size=2,
            name="stale",
            progress=False,
            client=client,
        )
    assert len(result) == 4 and len(server.submissions) == 2
    assert not (tmp_path / "cache" / "optimizations" / opt.id).exists()


def test_optimize_on_completed_returns_results_and_on_cancelled_raises(server) -> None:
    with _client(server) as client:
        first = dmc.optimize(
            _toy, direction="maximize", budget=2, batch_size=2, name="done", client=client
        )
        again = dmc.optimize(
            _toy, direction="maximize", budget=2, batch_size=2, name="done", client=client
        )
        assert [row.id for row in again] == [row.id for row in first]
        opt = client.optimizations.create(
            database="enamine", direction="maximize", budget=4, batch_size=2, name="gone"
        )
        opt.cancel()
        with pytest.raises(DeepMedChemError) as error:
            dmc.optimize(
                _toy, direction="maximize", budget=4, batch_size=2, name="gone", client=client
            )
    assert error.value.code == "optimization_not_active"
    assert "choose a new name" in str(error.value)
    assert len(server.submissions) == 1


def test_optimize_raises_when_paused(server) -> None:
    def pausing(smiles):
        server.pause("opt_test0001")
        return _toy(smiles)

    with _client(server) as client, pytest.raises(DeepMedChemError) as error:
        dmc.optimize(pausing, direction="maximize", budget=4, batch_size=2, name="p", client=client)
    assert error.value.code in {"optimization_not_active", "optimization_paused"}


def test_optimize_validates_before_creating_a_client(monkeypatch) -> None:
    def no_client(*args, **kwargs):
        raise AssertionError("no client should be created")

    monkeypatch.setattr(optimization_module, "Client", no_client)
    with pytest.raises(ValueError, match="direction"):
        dmc.optimize(_toy, direction="up")
    with pytest.raises(TypeError, match="score must be a function"):
        dmc.optimize([1, 2], direction="maximize")


def test_optimize_creates_and_closes_its_own_client(monkeypatch, server) -> None:
    created = []

    def factory():
        client = _client(server)
        created.append(client)
        return client

    monkeypatch.setattr(optimization_module, "Client", factory)
    dmc.optimize(_toy, direction="maximize", budget=2, batch_size=2, name="own", progress=False)
    assert created and created[0]._client.is_closed


# --- async ---------------------------------------------------------------------------------


def test_async_client_mirrors_every_operation(sleeps) -> None:
    server = FakeOptimizationServer(polls_before_batch=1, page_size=2)

    async def scenario():
        async with _async_client(server) as client:
            opt = await client.optimizations.create(
                database="freedom", direction="minimize", budget=4, batch_size=2, name="async"
            )
            assert (await client.optimizations.get("async")).id == opt.id
            assert (await client.optimizations.get(opt.id)).name == "async"
            listed = await client.optimizations.list(name="async", limit=5)
            resource, pending = await client.optimizations.next_batch(opt.id)
            assert pending is None and resource.status == "initializing"
            batch = await opt.ask()
            receipt = await opt.tell(batch, [-1.0, {"score": -2.0, "pose": 1}])
            second = await opt.ask()
            rows = normalize_scores(second, {second.ids[0]: -3.0})
            raw = await client.optimizations.submit(opt.id, second.id, rows)
            assert await opt.ask() is None
            page, cursor = await client.optimizations.results_page(opt.id, limit=1)
            result = await opt.results()
            retrieved = await client.optimizations.retrieve(opt.id)
            refreshed = await opt.refresh()
            with pytest.raises(DeepMedChemError):
                await opt.resume()
            cancelled = await client.optimizations.cancel(opt.id)
            again = await opt.cancel()
            return (
                opt,
                listed,
                receipt,
                raw,
                page,
                cursor,
                result,
                retrieved,
                refreshed,
                cancelled,
                again,
            )

    (opt, listed, receipt, raw, page, cursor, result, retrieved, refreshed, cancelled, again) = (
        asyncio.run(scenario())
    )
    assert json.loads(server.requests[0].content)["database"] == "freedom-space-5"
    assert [resource.id for resource in listed] == [opt.id]
    assert receipt.counts == {"valid": 2} and raw.counts == {"valid": 1, "failed": 1}
    assert len(page) == 1 and cursor == "1"
    assert result.best.score == -3.0 and len(result) == 4
    assert retrieved.status == refreshed.status == "completed"
    assert cancelled.status == again.status == "completed"  # cancel is a no-op once terminal
    assert repr(opt).startswith("AsyncOptimization(")
    assert sleeps == [2.0]  # the first batch was already polled once by next_batch()


def test_async_ask_raises_when_paused() -> None:
    server = FakeOptimizationServer()

    async def scenario():
        async with _async_client(server) as client:
            opt = await client.optimizations.create(
                database="enamine", direction="minimize", budget=4, batch_size=2
            )
            server.pause(opt.id)
            await opt.ask()

    with pytest.raises(DeepMedChemError) as error:
        asyncio.run(scenario())
    assert error.value.code == "optimization_paused"


def test_async_tell_by_batch_id_with_mapping() -> None:
    server = FakeOptimizationServer()

    async def scenario():
        async with _async_client(server) as client:
            opt = await client.optimizations.create(
                database="enamine", direction="minimize", budget=4, batch_size=2
            )
            batch = await opt.ask()
            first = await opt.tell(batch.id, {batch.ids[0]: 1.0})
            assert (await opt.tell(batch.id, {batch.ids[0]: 1.0})).duplicate
            with pytest.raises(DeepMedChemError):
                await opt.tell(batch.id, [1.0, 2.0])
            return first

    receipt = asyncio.run(scenario())
    assert receipt.counts == {"valid": 1, "failed": 1}
    assert math.isclose(server.submissions[0]["scores"][0]["score"], 1.0)


def test_resource_accepts_server_nulls_before_the_engine_starts():
    from deepmedchem.models import Observation, OptimizationResource, SubmitReceipt

    resource = OptimizationResource.model_validate(
        {"id": "opt_1", "status": "initializing", "engine": None, "progress": None,
         "links": None, "specification": None, "best": None}
    )
    assert resource.engine == {} and resource.links == {} and resource.direction is None
    assert Observation.model_validate({"id": "a", "metrics": None}).metrics == {}
    assert SubmitReceipt.model_validate({"accepted": True, "counts": None}).counts == {}
