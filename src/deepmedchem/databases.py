"""Display metadata and release-pinned shorthand IDs (September 2026).

Live catalog counts/pricing remain authoritative. Shipping and feasibility estimates:
https://www.alipheron.com/spaces/ , except VAST: XtalPi Library Overview 2026 H2.
BioSolveIT analogues:
https://www.biosolveit.de/chemical-spaces/ . Asterisks denote indirect mappings.
"""

from __future__ import annotations

DATABASE_DETAILS: dict[str, dict[str, str]] = {
    "cheminfinita-2026-02": {
        "abbreviation": "cheminfinita",
        "biosolveit": "CHEMriya_55bn_2025-10*",
        "type": "Make-On-Demand",
        "availability": "5-8 weeks",
        "success": "55-85%",
        "url": "https://www.otavachemicals.com/",
    },
    "d2b-spacem1": {
        "abbreviation": "spacem1",
        "biosolveit": "—",
        "type": "Make-On-Demand",
        "availability": "2-6 weeks",
        "success": ">85%",
        "url": "https://molecule.one/",
    },
    "enamine-real-v5a": {
        "abbreviation": "enamine",
        "biosolveit": "REALSpace_95bn_2026-04**",
        "type": "Make-On-Demand",
        "availability": "3-4 weeks",
        "success": ">80%",
        "url": "https://enamine.net/compound-collections/real-compounds/real-space-navigator",
    },
    "freedom-space-5": {
        "abbreviation": "freedom",
        "biosolveit": "FreedomSpace_296bn_2026-03",
        "type": "Make-On-Demand",
        "availability": "5-6 weeks",
        "success": ">80%",
        "url": "https://chem-space.com/freedom-space",
    },
    "synple-explore-2025-10": {
        "abbreviation": "explore",
        "biosolveit": "eXplore_8tr_2026-06",
        "type": "Make-On-Demand",
        "availability": "3-4 weeks",
        "success": ">85%",
        "url": "https://www.emolecules.com/products/explore",
    },
    "synple-synple-2025-10": {
        "abbreviation": "synple",
        "biosolveit": "Synple_8tr_2026-06",
        "type": "Make-On-Demand",
        "availability": "3-4 weeks",
        "success": ">85%",
        "url": "https://www.emolecules.com/products/explore",
    },
    "vast-2026-h2": {
        "abbreviation": "vast",
        "biosolveit": "VAST_4bn_2026-05",
        "type": "Make-On-Demand",
        "availability": "2-4 weeks",
        "success": "85%+",
        "url": "https://aifchem.com/",
    },
    "MCULE-IN-STOCK": {
        "served_by": "cheese",
        "abbreviation": "mcule-in-stock",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://mcule.com/",
    },
    "MCULE-FULL": {
        "served_by": "cheese",
        "abbreviation": "mcule-full",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://mcule.com/",
    },
    "MOLPORT": {
        "served_by": "cheese",
        "abbreviation": "molport",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://molport.com/",
    },
    "CHEMSPACE-SCREENING": {
        "served_by": "cheese",
        "abbreviation": "chemspace-screening",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://chem-space.com/",
    },
    "ZINC15": {
        "served_by": "cheese",
        "abbreviation": "zinc15",
        "biosolveit": "—",
        "type": "Other",
        "availability": "No availability info",
        "success": "N/A",
        "url": "https://zinc15.docking.org/",
    },
    # Enumerated make-on-demand catalogues served by classic CHEESE Search.
    "ENAMINE-REAL": {
        "served_by": "cheese",
        "abbreviation": "enamine-real",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "3–4 weeks",
        "success": ">80%",
        "url": "https://enamine.net/compound-collections/real-compounds",
    },
    "ENAMINE-AA": {
        "served_by": "cheese",
        "abbreviation": "enamine-aa",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "3–4 weeks",
        "success": ">80%",
        "url": "https://enamine.net/",
    },
    "CHEMSPACE-5B-RO5": {
        "served_by": "cheese",
        "abbreviation": "chemspace-5b-ro5",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "5–6 weeks",
        "success": ">80%",
        "url": "https://chem-space.com/",
    },
    "CHEMSPACE-5B-BEYOND-RO5": {
        "served_by": "cheese",
        "abbreviation": "chemspace-5b-beyond-ro5",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "5–6 weeks",
        "success": ">80%",
        "url": "https://chem-space.com/",
    },
    "CHEMSPACE-5B-FREEDOM": {
        "served_by": "cheese",
        "abbreviation": "chemspace-5b-freedom",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "5–6 weeks",
        "success": ">80%",
        "url": "https://chem-space.com/freedom-space",
    },
    "EXPLORE-DIVERSE": {
        "served_by": "cheese",
        "abbreviation": "explore-diverse",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "3–4 weeks",
        "success": ">85%",
        "url": "https://www.emolecules.com/products/explore",
    },
    "EXPLORE-ENUMERATED": {
        "served_by": "cheese",
        "abbreviation": "explore-enumerated",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "3–4 weeks",
        "success": ">85%",
        "url": "https://www.emolecules.com/products/explore",
    },
    "SYNPLE-4B": {
        "served_by": "cheese",
        "abbreviation": "synple-4b",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "3–4 weeks",
        "success": ">85%",
        "url": "https://www.emolecules.com/products/explore",
    },
    "XTALPI": {
        "served_by": "cheese",
        "abbreviation": "xtalpi",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "2–4 weeks",
        "success": "85%+",
        "url": "https://aifchem.com/",
    },
    "CHEMRIYA": {
        "served_by": "cheese",
        "abbreviation": "chemriya",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "Ask vendor",
        "success": "N/A",
        "url": "https://www.otavachemicals.com/",
    },
    "MOLECULE-ONE": {
        "served_by": "cheese",
        "abbreviation": "molecule-one",
        "biosolveit": "—",
        "type": "Make-On-Demand (enumerated)",
        "availability": "2–6 weeks",
        "success": ">85%",
        "url": "https://molecule.one/",
    },
}

# Preferred presentation order; unknown/private catalog entries follow these spaces.
DATABASE_DISPLAY_ORDER = (
    "enamine-real-v5a",
    "freedom-space-5",
    "synple-explore-2025-10",
    "synple-synple-2025-10",
    "vast-2026-h2",
    "cheminfinita-2026-02",
    "d2b-spacem1",
    # Classic CHEESE catalogues follow the platform spaces.
    "ENAMINE-REAL",
    "CHEMSPACE-5B-RO5",
    "CHEMSPACE-5B-BEYOND-RO5",
    "CHEMSPACE-5B-FREEDOM",
    "EXPLORE-ENUMERATED",
    "EXPLORE-DIVERSE",
    "SYNPLE-4B",
    "XTALPI",
    "MOLECULE-ONE",
    "CHEMRIYA",
    "ENAMINE-AA",
    "MCULE-IN-STOCK",
    "MCULE-FULL",
    "MOLPORT",
    "CHEMSPACE-SCREENING",
    "ZINC15",
)

# Databases served by classic CHEESE Search (api.cheese.deepmedchem.com) rather than
# the platform API. The client routes similarity search for these to /molsearch;
# substructure search, sampling, selections and runs are platform-only.
CLASSIC_DATABASES = frozenset(
    database_id
    for database_id, info in DATABASE_DETAILS.items()
    if info.get("served_by") == "cheese"
)

_ALIASES = {info["abbreviation"]: database_id for database_id, info in DATABASE_DETAILS.items()}
assert len(_ALIASES) == len(DATABASE_DETAILS), "database abbreviations must be unique"


def is_classic_database(database_id: str) -> bool:
    """True when ``database_id`` (a full ID, not an alias) is served by classic CHEESE."""
    return database_id in CLASSIC_DATABASES


def resolve_database(database: str) -> str:
    """Expand a known shorthand; preserve full IDs and private/unknown IDs verbatim."""
    return _ALIASES.get(database.casefold(), database)
