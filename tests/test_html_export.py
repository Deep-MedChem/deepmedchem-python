import base64
import builtins
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

import pytest

import deepmedchem.cli as cli
import deepmedchem.html_export as html_export
from deepmedchem.export import infer_format
from deepmedchem.models import SampleResult, SearchResult, SubstructureResult
from tests.test_cli_commands import _install_client


def _result(results, cls=SearchResult, **fields):
    return cls.model_validate(
        {
            "database_id": "enamine-real-v5a",
            "database_release": "2026-09-06.2",
            "scorer": "morgan",
            "metric": "ECFP4 Tanimoto",
            "results": results,
            **fields,
        }
    )


RESULT = _result(
    [
        {
            "rank": 1,
            "smiles": "CC(=O)Oc1ccccc1C(=O)O",
            "score": 0.70372,
            "price": 245,
            "product_id": "p1",
        },
        {"rank": 2, "smiles": "CCO", "score": None, "price": None, "product_id": None},
    ]
)


def _images(page):
    return [
        base64.b64decode(uri).decode("utf-8")
        for uri in re.findall(r'src="data:image/svg\+xml;base64,([^"]+)"', page)
    ]


def _block_rdkit(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("rdkit"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_importing_the_sdk_does_not_load_rdkit() -> None:
    code = (
        "import sys, deepmedchem, deepmedchem.models, deepmedchem.export; "
        "print('rdkit' in sys.modules)"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert output.stdout.strip() == "False"


def test_html_is_an_export_format() -> None:
    assert infer_format("hits.html") == "html"
    assert infer_format("hits.HTM") == "html"


def test_html_without_rdkit_explains_the_install(monkeypatch, tmp_path) -> None:
    _block_rdkit(monkeypatch)
    with pytest.raises(ImportError, match=r"pip install 'deepmedchem\[rdkit\]'"):
        RESULT.to_html(tmp_path / "hits.html")
    with pytest.raises(ImportError, match="RDKit"):
        RESULT.to_file(tmp_path / "hits.html")
    assert not (tmp_path / "hits.html").exists()


@pytest.mark.parametrize("limit", [0, -1, True, "5", 2.5])
def test_invalid_limit_is_rejected(limit, tmp_path) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        RESULT.to_html(tmp_path / "hits.html", limit=limit)


def test_html_cards_show_structures_scores_and_prices(tmp_path) -> None:
    pytest.importorskip("rdkit")
    target = RESULT.to_html(tmp_path / "hits.html", show=False)
    page = target.read_text(encoding="utf-8")
    assert target == tmp_path / "hits.html"
    assert page.startswith("<!doctype html>")
    assert "enamine-real-v5a · release 2026-09-06.2 · morgan · ECFP4 Tanimoto" in page
    assert page.count('<article class="card">') == 2
    score = '<span class="badge score" title="ECFP4 Tanimoto"><span class="label">Score</span>'
    assert score + "0.7037</span>" in page
    assert '<span class="badge price" title="Estimated price">$245</span>' in page
    # Missing values: neutral badges that still say "–".
    assert (
        '<span class="badge empty" title="ECFP4 Tanimoto"><span class="label">Score</span>–' in page
    )
    assert (
        '<span class="badge empty" title="Estimated price"><span class="label">Price</span>–'
        in page
    )
    assert '<p class="id" title="Product ID: –">–</p>' in page
    assert '<p class="id" title="Product ID: p1">p1</p>' in page
    assert "Prices are estimates" in page
    assert "<script" not in page and "http://" not in page.replace(html_export._SVG_NAMESPACE, "")
    images = _images(page)
    assert len(images) == 2
    for svg in images:
        ElementTree.fromstring(svg)  # valid XML
        assert "style=" not in svg.split("</style>")[-1]  # repeated inline styles compacted


def test_card_layout_order_and_responsive_grid(tmp_path) -> None:
    pytest.importorskip("rdkit")
    page = RESULT.to_html(tmp_path / "hits.html", show=False).read_text()
    card = page[page.index('<article class="card">') : page.index("</article>")]
    order = ['class="rank">#1<', 'class="structure"><img', "badge score", "badge price"]
    order += ['class="id"', 'class="smiles" title="CC(=O)Oc1ccccc1C(=O)O">']
    positions = [card.index(marker) for marker in order]
    assert positions == sorted(positions)
    # 4 columns on desktop, 2 on medium screens, 1 on narrow ones; no sideways scrolling.
    css = page.split('<div class="dmc-results"><style>')[1].split("</style>")[0]
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in css
    assert re.search(
        r"max-width: 1023px\) \{\s*\.dmc-results \.grid \{ grid-template-columns: repeat\(2,", css
    )
    assert re.search(
        r"max-width: 599px\) \{ \.dmc-results \.grid \{ grid-template-columns: minmax\(0, 1fr\)",
        css,
    )
    assert "max-width: 1200px" in page
    assert "<table" not in page and "overflow-x" not in page


def test_limit_draws_only_the_first_molecules_and_says_so(tmp_path) -> None:
    pytest.importorskip("rdkit")
    result = _result([{"rank": i, "smiles": "CCO", "score": 0.5} for i in range(1, 4)])
    page = result.to_html(tmp_path / "hits.html", limit=2, show=False).read_text()
    assert len(_images(page)) == 2
    assert "Showing 2 of 3 molecules." in page
    assert "Showing" not in result.to_html(tmp_path / "all.html", show=False).read_text()


def test_values_are_escaped_and_bad_smiles_do_not_break_the_page(tmp_path) -> None:
    pytest.importorskip("rdkit")
    result = _result(
        [
            {
                "rank": 1,
                "smiles": 'C1CC"><script>alert(1)</script>',
                "score": 0.5,
                "product_id": "<img src=x onerror=alert(1)>",
            }
        ],
        database_id="db<script>",
    )
    page = result.to_html(tmp_path / "hits.html", show=False).read_text()
    assert "<script>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "structure not available" in page


def test_default_file_goes_to_the_current_directory_with_a_timestamp(monkeypatch, tmp_path) -> None:
    pytest.importorskip("rdkit")
    monkeypatch.chdir(tmp_path)
    target = RESULT.to_html(show=False)
    assert re.fullmatch(r"deepmedchem-enamine-real-v5a-morgan-\d{8}T\d{6}Z\.html", target.name)
    assert (tmp_path / target).is_file()


def test_rendering_many_molecules_warns(tmp_path) -> None:
    pytest.importorskip("rdkit")
    result = _result([{"rank": i, "smiles": "CCO"} for i in range(1, 502)])
    with pytest.warns(UserWarning, match="Rendering 501 molecules may take a while"):
        result.to_html(tmp_path / "hits.html", limit=501, show=False)


def test_no_warning_up_to_the_threshold(tmp_path, recwarn) -> None:
    pytest.importorskip("rdkit")
    result = _result([{"rank": i, "smiles": "CCO"} for i in range(1, 502)])
    result.to_html(tmp_path / "hits.html", limit=500, show=False)
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


def test_substructure_and_sample_columns_match_the_cli(tmp_path) -> None:
    pytest.importorskip("rdkit")
    rows = [{"rank": 1, "smiles": "CCO", "price": 163, "product_id": "p1"}]
    substructure = (
        _result(rows, SubstructureResult).to_html(tmp_path / "s.html", show=False).read_text()
    )
    sample = _result(rows, SampleResult).to_html(tmp_path / "r.html", show=False).read_text()
    assert '<span class="badge score">Exact match</span>' in substructure
    assert "Score" not in sample and "Exact match" not in sample
    assert '<span class="badge price" title="Estimated price">$163</span>' in sample


def test_table_is_shown_in_notebooks_only(monkeypatch, tmp_path) -> None:
    pytest.importorskip("rdkit")
    shown = []
    monkeypatch.setattr(html_export, "show_in_notebook", shown.append)

    monkeypatch.setattr(html_export, "in_notebook", lambda: False)
    RESULT.to_html(tmp_path / "a.html")
    assert shown == []

    monkeypatch.setattr(html_export, "in_notebook", lambda: True)
    RESULT.to_html(tmp_path / "b.html")
    assert len(shown) == 1 and shown[0].startswith('<div class="dmc-results">')
    RESULT.to_html(tmp_path / "c.html", show=False)
    assert len(shown) == 1


def test_notebook_detection_outside_jupyter() -> None:
    assert html_export.in_notebook() is False


def test_cli_saves_html_and_reports_the_limit(monkeypatch, capsys, tmp_path) -> None:
    pytest.importorskip("rdkit")
    _install_client(monkeypatch)
    target = tmp_path / "hits.html"
    argv = [
        "search",
        "CC(=O)Oc1ccccc1C(=O)O",
        "-d",
        "enamine-real-v5a",
        "-n",
        "2",
        "-o",
        str(target),
    ]
    assert cli.main([*argv, "--html-limit", "1"]) == 0
    assert (
        f"Saved 1 of 2 molecules to {target} (html). Use --html-limit to draw more."
        in capsys.readouterr().out
    )
    assert "Showing 1 of 2 molecules." in target.read_text()

    assert cli.main(argv) == 0
    assert f"Saved 2 molecules to {target} (html)." in capsys.readouterr().out


def test_cli_without_rdkit_prints_the_install_hint(monkeypatch, capsys, tmp_path) -> None:
    _install_client(monkeypatch)
    _block_rdkit(monkeypatch)
    argv = ["search", "CCO", "-d", "enamine-real-v5a", "-o", str(tmp_path / "hits.html")]
    assert cli.main(argv) == 1
    assert "pip install 'deepmedchem[rdkit]'" in capsys.readouterr().err
