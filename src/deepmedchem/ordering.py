"""Prepare reviewable vendor email drafts from DeepMedChem result CSV files.

This module intentionally does not send email or place an order.  It writes a
small, price-free request bundle first and then callers may ask the operating
system to open the generated ``mailto:`` drafts.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

DEEPMEDCHEM_CC = "info@deepmedchem.com"
REFERENCE_SUFFIX = "-DMCH"
INLINE_MOLECULE_LIMIT = 20
INLINE_CHARACTER_LIMIT = 6_000


@dataclass(frozen=True)
class VendorContact:
    """Procurement contact for one or more DeepMedChem database IDs."""

    name: str
    email: str


# Stable vendor contacts are based on the CHEESE database metadata source of truth:
# cheese-database/cheese_database/molecules/databases_info.json.  The lower-case IDs are
# the public API v2 IDs; upper-case entries retain compatibility with CHEESE CSV exports.
# ZINC15 is deliberately absent: it is an aggregator rather than a purchasing vendor.
_CONTACTS_BY_DATABASE: dict[str, VendorContact] = {}


def _register(contact: VendorContact, *database_ids: str) -> None:
    for database_id in database_ids:
        _CONTACTS_BY_DATABASE[database_id.casefold()] = contact


_register(
    VendorContact("Chemspace", "sales@chem-space.com"),
    "freedom-space-5",
    "CHEMSPACE-FREEDOM-SYNTHON",
    "CHEMSPACE-SCREENING",
    "CHEMSPACE-5B-BEYOND-RO5",
    "CHEMSPACE-5B-RO5",
    "CHEMSPACE-5B-FREEDOM",
)
_register(VendorContact("XtalPi", "contact@xtalpi.com"), "vast-2026-h2", "XTALPI", "XTALPI-SYNTHON")
_register(
    VendorContact("OTAVA Chemicals", "sales@otavachemicals.com"),
    "cheminfinita-2026-02",
    "CHEMINFINITA-SYNTHON",
)
_register(
    VendorContact("eMolecules", "sales@emolecules.com"),
    "synple-synple-2025-10",
    "synple-explore-2025-10",
    "EXPLORE-ENUMERATED",
    "EXPLORE-DIVERSE",
    "SYNPLE-1B",
    "SYNPLE-4B",
    "EMOLECULES-SYNPLE-SYNTHON",
    "EMOLECULES-EXPLORE-SYNTHON",
)
_register(
    VendorContact("Molecule.one", "hello@molecule.one"),
    "d2b-spacem1",
    "MOLECULE-ONE",
    "MOLECULEONE-D2B-SYNTHON",
)
_register(
    VendorContact("Enamine", "info@enamine.net"),
    "enamine-real-v5a",
    "ENAMINE-REAL-V5A-SYNTHON",
    "ENAMINE-AA",
)
_register(
    VendorContact("Enamine LTD", "libraries@enamine.net"),
    "ENAMINE-REAL",
    "ENAMINE-CARBOXYLIC",
)
_register(VendorContact("Chemriya", "info@chemriya.com"), "CHEMRIYA")
_register(VendorContact("Mcule", "order@mcule.com"), "MCULE-FULL", "MCULE-IN-STOCK")
_register(VendorContact("Molport", "sales@molport.com"), "MOLPORT")

CURRENT_DATABASE_IDS: tuple[str, ...] = (
    "freedom-space-5",
    "vast-2026-h2",
    "cheminfinita-2026-02",
    "synple-synple-2025-10",
    "synple-explore-2025-10",
    "d2b-spacem1",
    "enamine-real-v5a",
)

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_COLUMN_ALIASES = {
    "smiles": ("smiles", "canonical_smiles", "product_smiles"),
    "product_id": ("product_id", "id", "molecule_id", "deepmedchem_id"),
    "database_id": ("database_id", "database", "db_name"),
}


@dataclass(frozen=True)
class OrderMolecule:
    database_id: str
    deepmedchem_id: str
    smiles: str


@dataclass(frozen=True)
class OrderDraft:
    vendor: str
    email: str
    molecules: tuple[OrderMolecule, ...]
    directory: Path
    csv_path: Path
    email_path: Path
    subject: str
    body: str
    mailto_url: str


@dataclass(frozen=True)
class OrderBundle:
    directory: Path
    mode: str
    drafts: tuple[OrderDraft, ...]

    @property
    def molecule_count(self) -> int:
        return sum(len(draft.molecules) for draft in self.drafts)


def _validate_email(value: str) -> str:
    email = value.strip()
    if email.casefold() == "n/a" or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError(f"Invalid vendor email address: {value!r}")
    return email


def procurement_contacts() -> dict[str, VendorContact]:
    """Return a copy of the built-in database-to-vendor contact registry."""

    return dict(_CONTACTS_BY_DATABASE)


def _contact_for(database_id: str) -> VendorContact:
    try:
        contact = _CONTACTS_BY_DATABASE[database_id.casefold()]
    except KeyError as error:
        raise ValueError(
            f"No procurement contact is configured for database {database_id!r}. "
            "Use --to to provide the intended recipient explicitly."
        ) from error
    _validate_email(contact.email)
    return contact


def _column(fieldnames: list[str], logical_name: str) -> str | None:
    normalized = {name.strip().casefold(): name for name in fieldnames}
    return next(
        (normalized[alias] for alias in _COLUMN_ALIASES[logical_name] if alias in normalized),
        None,
    )


def _reference_id(product_id: str, *, row_number: int) -> str:
    value = product_id.strip()
    if not value:
        return f"DMC-{row_number:06d}{REFERENCE_SUFFIX}"
    if value.upper().endswith(REFERENCE_SUFFIX):
        return value
    return f"{value}{REFERENCE_SUFFIX}"


def _without_control_characters(value: str, *, field: str, source: Path, row: int) -> str:
    if any(character in value for character in "\r\n\t"):
        raise ValueError(f"{source}: row {row} has a control character in {field}")
    return value


def read_order_csv(path: str | Path, *, database: str | None = None) -> list[OrderMolecule]:
    """Read an SDK/CHEESE CSV and return only procurement-safe molecule fields."""

    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{source} does not contain a CSV header")
        smiles_column = _column(reader.fieldnames, "smiles")
        id_column = _column(reader.fieldnames, "product_id")
        database_column = _column(reader.fieldnames, "database_id")
        if smiles_column is None:
            raise ValueError(f"{source} must contain a 'smiles' column")
        if database_column is None and not database:
            raise ValueError(
                f"{source} does not identify its database; pass --database DATABASE_ID"
            )

        molecules: list[OrderMolecule] = []
        seen: set[tuple[str, str, str]] = set()
        for row_number, row in enumerate(reader, start=2):
            smiles = _without_control_characters(
                str(row.get(smiles_column) or "").strip(),
                field="SMILES",
                source=source,
                row=row_number,
            )
            product_id = (
                _without_control_characters(
                    str(row.get(id_column) or "").strip(),
                    field="product ID",
                    source=source,
                    row=row_number,
                )
                if id_column
                else ""
            )
            database_id = (
                str(row.get(database_column) or "").strip() if database_column else ""
            ) or str(database or "").strip()
            database_id = _without_control_characters(
                database_id, field="database ID", source=source, row=row_number
            )

            # Legacy CHEESE downloads can contain the query as their first row.
            if (
                product_id.casefold() == "query molecule"
                or database_id.casefold() == "query molecule"
            ):
                continue
            if not smiles:
                raise ValueError(f"{source}: row {row_number} has an empty SMILES value")
            if not database_id:
                raise ValueError(f"{source}: row {row_number} has no database ID")

            molecule = OrderMolecule(
                database_id=database_id,
                deepmedchem_id=_reference_id(product_id, row_number=row_number - 1),
                smiles=smiles,
            )
            identity = (molecule.database_id.casefold(), molecule.deepmedchem_id, molecule.smiles)
            if identity not in seen:
                molecules.append(molecule)
                seen.add(identity)

    if not molecules:
        raise ValueError(f"{source} does not contain any orderable molecule rows")
    return molecules


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "vendor"


def _draft_text(
    *,
    vendor: str,
    molecules: list[OrderMolecule],
    get_quote: bool,
    amount_mg: float | None,
    name: str | None,
) -> tuple[str, str]:
    count = len(molecules)
    sdk_label = "DeepMedChem Python SDK"
    if get_quote:
        subject = f"Quote request - {count} molecules identified with {sdk_label}"
        request = (
            "Please confirm current pricing, availability, and expected lead times for the "
            f"{count} molecules included in this request, which were identified using the "
            f"{sdk_label}."
        )
    else:
        subject = f"Order request - {count} molecules identified with {sdk_label}"
        request = (
            f"I would like to initiate an order for the {count} molecules included in this "
            f"request, which were identified using the {sdk_label}. Please confirm final pricing, "
            "availability, expected lead times, and the order details before processing."
        )

    lines = [f"Dear {vendor} team,", "", request]
    if amount_mg is not None:
        lines.extend(["", f"Requested amount: {amount_mg:g} mg per molecule."])
    elif not get_quote:
        lines.extend(["", "Please let me know the available pack sizes."])
    lines.extend(
        [
            "",
            "DeepMedChem price estimates are intentionally omitted and are not binding.",
            "The generated molecules.csv file contains only database IDs, DeepMedChem reference "
            "IDs, and SMILES.",
        ]
    )
    inline_characters = sum(
        len(m.database_id) + len(m.deepmedchem_id) + len(m.smiles) + 2 for m in molecules
    )
    if count <= INLINE_MOLECULE_LIMIT and inline_characters <= INLINE_CHARACTER_LIMIT:
        lines.extend(["", "Molecules:", "database_id\tdeepmedchem_id\tsmiles"])
        lines.extend(f"{m.database_id}\t{m.deepmedchem_id}\t{m.smiles}" for m in molecules)
    else:
        lines.extend(["", "Please see the accompanying molecules.csv file for the molecule list."])
    lines.extend(["", "Thank you for your assistance.", "", "Best regards,", name or "[Your Name]"])
    return subject, "\n".join(lines)


def _mailto(email: str, *, subject: str, body: str, cc: str) -> str:
    query = urlencode({"cc": cc, "subject": subject, "body": body}, quote_via=quote)
    return f"mailto:{quote(email, safe='@')}?{query}"


def _write_request_csv(path: Path, molecules: list[OrderMolecule]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("database_id", "deepmedchem_id", "smiles"))
        writer.writeheader()
        writer.writerows(
            {
                "database_id": molecule.database_id,
                "deepmedchem_id": molecule.deepmedchem_id,
                "smiles": molecule.smiles,
            }
            for molecule in molecules
        )


def prepare_order(
    input_path: str | Path,
    *,
    get_quote: bool = False,
    database: str | None = None,
    output_dir: str | Path | None = None,
    to: str | None = None,
    cc: str = DEEPMEDCHEM_CC,
    amount_mg: float | None = None,
    name: str | None = None,
) -> OrderBundle:
    """Create a price-free request bundle and return its draft metadata."""

    if amount_mg is not None and amount_mg <= 0:
        raise ValueError("--amount-mg must be greater than zero")
    cc = _validate_email(cc)
    override = VendorContact("Vendor", _validate_email(to)) if to else None
    molecules = read_order_csv(input_path, database=database)

    grouped: dict[str, list[OrderMolecule]] = defaultdict(list)
    contacts: dict[str, VendorContact] = {}
    for molecule in molecules:
        contact = override or _contact_for(molecule.database_id)
        key = contact.email.casefold()
        grouped[key].append(molecule)
        contacts[key] = contact

    source = Path(input_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = (
        Path(output_dir) if output_dir else source.with_name(f"{source.stem}-order-{timestamp}")
    )
    if target.exists():
        raise ValueError(f"Output directory already exists: {target}")
    target.mkdir(parents=True)

    mode = "quote" if get_quote else "order"
    drafts: list[OrderDraft] = []
    used_slugs: set[str] = set()
    for email_key in sorted(grouped):
        contact = contacts[email_key]
        vendor_slug = _slug(contact.name)
        if vendor_slug in used_slugs:
            digest = hashlib.sha256(email_key.encode()).hexdigest()[:8]
            vendor_slug = f"{vendor_slug}-{digest}"
        used_slugs.add(vendor_slug)
        draft_dir = target / vendor_slug
        draft_dir.mkdir()
        request_csv = draft_dir / "molecules.csv"
        email_txt = draft_dir / "email.txt"
        group = grouped[email_key]
        _write_request_csv(request_csv, group)
        subject, body = _draft_text(
            vendor=contact.name,
            molecules=group,
            get_quote=get_quote,
            amount_mg=amount_mg,
            name=name,
        )
        email_txt.write_text(
            f"To: {contact.email}\nCc: {cc}\nSubject: {subject}\n\n{body}\n",
            encoding="utf-8",
        )
        drafts.append(
            OrderDraft(
                vendor=contact.name,
                email=contact.email,
                molecules=tuple(group),
                directory=draft_dir,
                csv_path=request_csv,
                email_path=email_txt,
                subject=subject,
                body=body,
                mailto_url=_mailto(contact.email, subject=subject, body=body, cc=cc),
            )
        )

    manifest: dict[str, Any] = {
        "schema": "deepmedchem-order-request/1",
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(source),
        "molecule_count": len(molecules),
        "requests": [
            {
                "vendor": draft.vendor,
                "email": draft.email,
                "molecule_count": len(draft.molecules),
                "databases": sorted({m.database_id for m in draft.molecules}),
                "directory": draft.directory.name,
            }
            for draft in drafts
        ],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_lines = [
        f"DeepMedChem {mode} request bundle",
        f"Source: {source}",
        f"Molecules: {len(molecules)}",
        f"Vendor requests: {len(drafts)}",
        "",
    ]
    summary_lines.extend(
        f"{draft.vendor}: {len(draft.molecules)} molecules -> {draft.email} "
        f"({draft.directory.name}/)"
        for draft in drafts
    )
    summary_lines.extend(
        [
            "",
            "If an email draft did not open, copy the matching email.txt and attach or paste "
            "molecules.csv.",
        ]
    )
    (target / "summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return OrderBundle(directory=target, mode=mode, drafts=tuple(drafts))


def open_order_drafts(bundle: OrderBundle) -> int:
    """Ask the OS to open every draft and return the number accepted by its handler."""

    opened = 0
    for draft in bundle.drafts:
        if webbrowser.open(draft.mailto_url, new=0, autoraise=True):
            opened += 1
    return opened
