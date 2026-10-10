"""Write results as a self-contained HTML page: a grid of cards with 2D structures.

Structures are drawn with RDKit, which is optional: it is imported only when an
HTML file is written (``pip install 'deepmedchem[rdkit]'``). The page has no
external scripts, fonts or images, so it opens offline and can be shared as one
file. Every value from the API is HTML-escaped, and drawings are embedded as
``data:image/svg+xml`` images, which browsers never execute as scripts.
"""

from __future__ import annotations

import base64
import html
import os
import re
import sys
import warnings
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import SearchResult

DEFAULT_HTML_LIMIT = 100
LARGE_RENDER_WARNING = 500
_IMAGE_SIZE = (220, 160)
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
# Style properties whose value is already the SVG default; dropping them keeps
# each drawing about half the size without changing how it looks.
_DEFAULT_STYLE = {
    "fill-opacity": "1",
    "stroke-opacity": "1",
    "stroke-linecap": "butt",
    "stroke-linejoin": "miter",
    "font-style": "normal",
    "font-weight": "normal",
    "text-anchor": "start",
}
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

_CSS = """
.dmc-results { --page: #f9fafb; --card: #ffffff; --text: #111827; --muted: #64748b;
  --subtle: #94a3b8; --line: #e5e7eb;
  --blue-bg: #eff6ff; --blue-text: #1d4ed8; --blue-ring: #bfdbfe;
  --amber-bg: #fffbeb; --amber-text: #92400e; --amber-ring: #fde68a;
  --sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", ui-sans-serif, system-ui,
    Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  color: var(--text); background: var(--page); font: 14px/1.5 var(--sans);
  -webkit-font-smoothing: antialiased; padding: 4px 0; }
.dmc-results * { box-sizing: border-box; }
.dmc-results header { display: flex; flex-direction: column; gap: 2px; margin-bottom: 16px; }
.dmc-results h1 { font-size: 20px; line-height: 1.3; margin: 0 0 2px; font-weight: 600;
  color: var(--text); letter-spacing: -0.01em; }
.dmc-results .meta { color: var(--muted); font-size: 13px; }
.dmc-results .notice { margin: 0 0 16px; color: #334155; background: var(--blue-bg);
  border: 1px solid #dbeafe; border-radius: 8px; padding: 10px 14px; font-size: 13px; }
.dmc-results .notice code { font-family: var(--mono); font-size: 12px; }
.dmc-results .grid { display: grid; gap: 16px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 1023px) {
  .dmc-results .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 599px) { .dmc-results .grid { grid-template-columns: minmax(0, 1fr); } }
.dmc-results .card { min-width: 0; display: flex; flex-direction: column; gap: 12px;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px;
  box-shadow: 0 1px 2px 0 rgba(16, 24, 40, 0.05); }
.dmc-results .rank { font-size: 12px; font-weight: 500; color: var(--subtle);
  font-variant-numeric: tabular-nums; }
.dmc-results .structure { display: flex; align-items: center; justify-content: center;
  aspect-ratio: 11 / 8; max-height: 200px; width: 100%; margin: -4px 0 0; }
.dmc-results .structure img { display: block; width: 100%; height: 100%; object-fit: contain; }
.dmc-results .unavailable { color: var(--muted); font-size: 12px; text-align: center; }
.dmc-results .badges { display: flex; flex-wrap: wrap; gap: 6px; min-height: 26px; }
.dmc-results .badge { display: inline-flex; align-items: center; gap: 4px; border-radius: 9999px;
  padding: 4px 10px; font-size: 12px; font-weight: 500; line-height: 16px; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.dmc-results .badge .label { font-weight: 400; opacity: 0.8; }
.dmc-results .badge.score { background: var(--blue-bg); color: var(--blue-text);
  box-shadow: inset 0 0 0 1px var(--blue-ring); }
.dmc-results .badge.price { background: var(--amber-bg); color: var(--amber-text);
  box-shadow: inset 0 0 0 1px var(--amber-ring); }
.dmc-results .badge.empty { background: #f3f4f6; color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--line); }
.dmc-results .id, .dmc-results .smiles { margin: 0; font-family: var(--mono); font-size: 11.5px;
  line-height: 1.45; min-width: 0; }
.dmc-results .id { color: #475569; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dmc-results .smiles { color: var(--muted); word-break: break-all; display: -webkit-box;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden;
  min-height: calc(2 * 1.45em); }
.dmc-results .details { display: flex; flex-direction: column; gap: 4px; margin-top: auto;
  padding-top: 10px; border-top: 1px solid #f1f5f9; }
.dmc-results footer { color: var(--muted); font-size: 12px; margin-top: 20px; }
"""


def _require_rdkit():
    try:
        from rdkit import Chem, rdBase
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError as error:
        raise ImportError(
            "HTML export draws structures with RDKit. Install it with "
            "`pip install 'deepmedchem[rdkit]'`, or export CSV, SMILES, or JSON instead."
        ) from error
    return Chem, rdBase, rdMolDraw2D


def _compact_svg(svg: str) -> str:
    """Move RDKit's repeated inline styles into attributes and drop default values."""

    try:
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", svg)
        root = ElementTree.fromstring(text)
        for element in root.iter():
            element.attrib.pop("class", None)
            style = element.attrib.pop("style", None)
            if not style:
                continue
            properties = dict(part.split(":", 1) for part in style.split(";") if ":" in part)
            if properties.get("fill") == "none":
                properties.pop("fill-rule", None)
            for name, value in properties.items():
                name, value = name.strip(), value.strip()
                if _DEFAULT_STYLE.get(name) != value:
                    element.set(name, value)
        # Write the SVG namespace as a plain xmlns attribute instead of calling
        # ElementTree.register_namespace, which would change a process-wide registry.
        prefix = f"{{{_SVG_NAMESPACE}}}"
        for element in root.iter():
            if isinstance(element.tag, str) and element.tag.startswith(prefix):
                element.tag = element.tag[len(prefix) :]
        root.set("xmlns", _SVG_NAMESPACE)
        return ElementTree.tostring(root, encoding="unicode")
    except (ElementTree.ParseError, ValueError):
        return svg


def _structure_image(smiles: str, chem, rdMolDraw2D) -> str | None:
    """Return a data URI with the 2D drawing of ``smiles``, or None if RDKit cannot parse it."""

    molecule = chem.MolFromSmiles(smiles) if smiles else None
    if molecule is None:
        return None
    width, height = _IMAGE_SIZE
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height, -1, -1, True)
    drawer.drawOptions().clearBackground = False
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, molecule)
    drawer.FinishDrawing()
    svg = _compact_svg(drawer.GetDrawingText())
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _text(value: Any) -> str:
    return html.escape("–" if value is None or value == "" else str(value), quote=True)


def _score(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "–"


def _price(value: int | None) -> str:
    return f"${value}" if value is not None else "–"


def _score_column(result: SearchResult) -> tuple[str, str] | None:
    """Return the (header, kind) of the score column, matching the CLI table."""

    if result.method == "substructure":
        return ("Match", "match")
    if result.method == "sample":
        return None
    return ("Score", "score")


def render_html_fragment(
    result: SearchResult, *, limit: int = DEFAULT_HTML_LIMIT
) -> tuple[str, int]:
    """Return the scoped HTML card grid (for notebooks) and the number of molecules drawn."""

    limit = _validate_limit(limit)
    chem, rd_base, rd_draw = _require_rdkit()
    hits = result.hits
    shown = hits[:limit]
    if len(shown) > LARGE_RENDER_WARNING:
        warnings.warn(
            f"Rendering {len(shown)} molecules may take a while and produce a large file.",
            UserWarning,
            stacklevel=3,
        )
    score_column = _score_column(result)
    metric = result.metric or result.method

    with rd_base.BlockLogs():
        cards = []
        for hit in shown:
            image = _structure_image(hit.smiles, chem, rd_draw)
            structure = (
                f'<img src="{image}" width="{_IMAGE_SIZE[0]}" height="{_IMAGE_SIZE[1]}" '
                f'alt="{_text(hit.smiles)}" loading="lazy">'
                if image
                else '<span class="unavailable">structure not available</span>'
            )
            badges = []
            if score_column and score_column[1] == "match":
                badges.append('<span class="badge score">Exact match</span>')
            elif score_column:
                kind = "score" if hit.score is not None else "empty"
                badges.append(
                    f'<span class="badge {kind}" title="{_text(metric)}">'
                    f'<span class="label">Score</span>{_text(_score(hit.score))}</span>'
                )
            kind = "price" if hit.price is not None else "empty"
            label = "" if hit.price is not None else '<span class="label">Price</span>'
            badges.append(
                f'<span class="badge {kind}" title="Estimated price">'
                f"{label}{_text(_price(hit.price))}</span>"
            )
            cards.append(
                f'<article class="card"><div class="rank">#{_text(hit.rank)}</div>'
                f'<div class="structure">{structure}</div>'
                f'<div class="badges">{"".join(badges)}</div>'
                f'<div class="details">'
                f'<p class="id" title="Product ID: {_text(hit.product_id)}">'
                f"{_text(hit.product_id)}</p>"
                f'<p class="smiles" title="{_text(hit.smiles)}">{_text(hit.smiles)}</p>'
                f"</div></article>"
            )

    details = [
        value
        for value in (
            result.database_id,
            f"release {result.database_release}" if result.database_release else None,
            result.method,
            metric if metric != result.method else None,
        )
        if value
    ]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notice = (
        f'<p class="notice">Showing {len(shown)} of {len(hits)} molecules. '
        f"Raise the limit to draw more (<code>limit=</code> in Python, "
        f"<code>--html-limit</code> in the CLI).</p>"
        if len(shown) < len(hits)
        else ""
    )
    fragment = (
        f'<div class="dmc-results"><style>{_CSS}</style>'
        f"<header><h1>DeepMedChem results</h1>"
        f'<span class="meta">{_text(" · ".join(details))}</span>'
        f'<span class="meta">{len(hits)} molecules · generated {_text(generated)}</span>'
        f"</header>{notice}"
        f'<div class="grid">{"".join(cards)}</div>'
        f"<footer>Prices are estimates; confirm price and availability with the vendor. "
        f"Generated by deepmedchem {_text(__version__)}.</footer></div>"
    )
    return fragment, len(shown)


def html_document(result: SearchResult, fragment: str) -> str:
    """Wrap a rendered fragment in a complete standalone HTML document."""

    title = _text(f"DeepMedChem results - {result.database_id or 'search'}")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title>"
        "<style>body { margin: 0; padding: 32px 24px; background: #f9fafb; }"
        "body > .dmc-results { max-width: 1200px; margin-inline: auto; }"
        "@media (max-width: 599px) { body { padding: 20px 16px; } }</style>"
        f"</head><body>{fragment}</body></html>\n"
    )


def write_html(
    result: SearchResult, path: str | os.PathLike[str], *, limit: int = DEFAULT_HTML_LIMIT
) -> int:
    """Write ``result`` as a standalone HTML file and return the number of molecules drawn."""

    fragment, drawn = render_html_fragment(result, limit=limit)
    Path(path).write_text(html_document(result, fragment), encoding="utf-8")
    return drawn


def default_html_path(result: SearchResult, *, directory: str | os.PathLike[str] = ".") -> Path:
    """Return ``./deepmedchem-<database>-<method>-<UTC timestamp>.html``.

    If that file already exists (two exports within the same second), a counter is
    appended (``...-2.html``, ``...-3.html``) so an earlier export is never overwritten.
    """

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = [
        _UNSAFE_FILENAME.sub("-", str(value)).strip("-")
        for value in (result.database_id, result.method)
        if value
    ]
    stem = "-".join(["deepmedchem", *[part for part in parts if part], timestamp])
    candidate = Path(directory) / f"{stem}.html"
    counter = 2
    while candidate.exists():
        candidate = Path(directory) / f"{stem}-{counter}.html"
        counter += 1
    return candidate


def in_notebook() -> bool:
    """Return True inside a Jupyter-style notebook kernel (Jupyter, VS Code, Colab)."""

    ipython = sys.modules.get("IPython")
    if ipython is None:
        return False
    try:
        shell = ipython.get_ipython()
    except Exception:  # pragma: no cover - defensive; IPython internals vary
        return False
    return type(shell).__name__ in {"ZMQInteractiveShell", "Shell"}


def show_in_notebook(fragment: str) -> None:
    from IPython.display import HTML, display

    display(HTML(fragment))


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return limit
