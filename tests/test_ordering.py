import csv
import json

import pytest

import deepmedchem.cli as cli
from deepmedchem.ordering import (
    CURRENT_DATABASE_IDS,
    prepare_order,
    procurement_contacts,
    read_order_csv,
)


def _write_csv(path, rows, fieldnames=None):
    fields = fieldnames or ["database_id", "product_id", "smiles", "price", "score"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_all_current_databases_have_real_procurement_contacts() -> None:
    contacts = procurement_contacts()
    for database_id in CURRENT_DATABASE_IDS:
        contact = contacts[database_id]
        assert contact.name
        assert contact.email.casefold() != "n/a"
        assert "@" in contact.email


def test_prepare_quote_groups_by_email_and_writes_only_safe_columns(tmp_path) -> None:
    source = tmp_path / "results.csv"
    _write_csv(
        source,
        [
            {
                "database_id": "freedom-space-5",
                "product_id": "freedom-1",
                "smiles": "CCO",
                "price": "250",
                "score": "0.95",
            },
            {
                "database_id": "synple-synple-2025-10",
                "product_id": "synple-1-DMCH",
                "smiles": "CCN",
                "price": "163",
                "score": "0.80",
            },
            {
                "database_id": "synple-explore-2025-10",
                "product_id": "synple-2",
                "smiles": "CCC",
                "price": "245",
                "score": "0.75",
            },
        ],
    )
    target = tmp_path / "quote-request"
    bundle = prepare_order(source, get_quote=True, output_dir=target, name="Ada")

    assert bundle.mode == "quote"
    assert bundle.molecule_count == 3
    assert len(bundle.drafts) == 2
    by_email = {draft.email: draft for draft in bundle.drafts}
    assert set(by_email) == {"sales@chem-space.com", "sales@emolecules.com"}
    assert len(by_email["sales@emolecules.com"].molecules) == 2

    for draft in bundle.drafts:
        with draft.csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert list(rows[0]) == ["database_id", "deepmedchem_id", "smiles"]
        csv_text = draft.csv_path.read_text()
        assert "price" not in csv_text.casefold()
        assert "score" not in csv_text.casefold()
        assert "250" not in csv_text
        assert "163" not in csv_text
        assert "245" not in csv_text
        assert "DeepMedChem Python SDK" in draft.body
        assert "not binding" in draft.body
        assert draft.email in draft.email_path.read_text()

    emolecules_ids = [
        molecule.deepmedchem_id for molecule in by_email["sales@emolecules.com"].molecules
    ]
    assert emolecules_ids == ["synple-1-DMCH", "synple-2-DMCH"]
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["schema"] == "deepmedchem-order-request/1"
    assert manifest["molecule_count"] == 3


def test_direct_order_mentions_confirmation_and_amount(tmp_path) -> None:
    source = tmp_path / "results.csv"
    _write_csv(
        source,
        [
            {
                "database_id": "enamine-real-v5a",
                "product_id": "p1",
                "smiles": "CCO",
                "price": "245",
                "score": "1",
            }
        ],
    )
    bundle = prepare_order(source, output_dir=tmp_path / "order", amount_mg=2.5)
    draft = bundle.drafts[0]
    assert bundle.mode == "order"
    assert draft.subject.startswith("Order request")
    assert "initiate an order" in draft.body
    assert "before processing" in draft.body
    assert "Requested amount: 2.5 mg per molecule." in draft.body
    assert "p1-DMCH\tCCO" in draft.body


def test_legacy_query_row_is_skipped_and_database_override_is_supported(tmp_path) -> None:
    source = tmp_path / "legacy.csv"
    _write_csv(
        source,
        [
            {"id": "Query Molecule", "smiles": "CC", "price": ""},
            {"id": "vendor-1", "smiles": "CCC", "price": "99"},
        ],
        fieldnames=["id", "smiles", "price"],
    )
    molecules = read_order_csv(source, database="MCULE-FULL")
    assert [(item.deepmedchem_id, item.smiles) for item in molecules] == [("vendor-1-DMCH", "CCC")]


def test_unknown_database_fails_unless_recipient_is_overridden(tmp_path) -> None:
    source = tmp_path / "unknown.csv"
    _write_csv(
        source,
        [
            {
                "database_id": "private-space",
                "product_id": "p1",
                "smiles": "CCO",
                "price": "1",
                "score": "1",
            }
        ],
    )
    with pytest.raises(ValueError, match="No procurement contact"):
        prepare_order(source, output_dir=tmp_path / "failed")
    assert not (tmp_path / "failed").exists()

    bundle = prepare_order(source, output_dir=tmp_path / "override", to="purchasing@example.com")
    assert [draft.email for draft in bundle.drafts] == ["purchasing@example.com"]


def test_cli_no_open_creates_copyable_fallback(tmp_path, capsys) -> None:
    source = tmp_path / "results.csv"
    _write_csv(
        source,
        [
            {
                "database_id": "vast-2026-h2",
                "product_id": "p1",
                "smiles": "CCO",
                "price": "118",
                "score": "0.9",
            }
        ],
    )
    target = tmp_path / "request"
    assert (
        cli.main(
            [
                "order",
                str(source),
                "--get-quote",
                "--no-open",
                "--output-dir",
                str(target),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "XtalPi: 1 molecules -> contact@xtalpi.com" in out
    assert "--no-open" in out
    assert (target / "xtalpi" / "email.txt").exists()
    assert (target / "xtalpi" / "molecules.csv").exists()


def test_cli_headless_keeps_artifacts_instead_of_opening_mail(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "results.csv"
    _write_csv(
        source,
        [
            {
                "database_id": "d2b-spacem1",
                "product_id": "p1",
                "smiles": "CCO",
                "price": "",
                "score": "0.9",
            }
        ],
    )
    monkeypatch.setattr(cli, "can_open_browser", lambda: False)
    monkeypatch.setattr(
        cli,
        "open_order_drafts",
        lambda bundle: pytest.fail("must not attempt mailto on a headless host"),
    )
    target = tmp_path / "request"
    assert cli.main(["order", str(source), "--output-dir", str(target)]) == 0
    assert "No graphical mail client detected" in capsys.readouterr().out
    assert (target / "molecule-one" / "email.txt").exists()
