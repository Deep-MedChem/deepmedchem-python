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
)

_ALIASES = {info["abbreviation"]: database_id for database_id, info in DATABASE_DETAILS.items()}
assert len(_ALIASES) == len(DATABASE_DETAILS), "database abbreviations must be unique"


# Classic CHEESE Search catalogues (enumerated and in-stock) that the SDK reached before it
# moved to the platform API alone. Their IDs still go to the platform API, which will serve
# several of them under these same IDs; the set only explains a "not served" answer (each
# alias was the casefolded ID). Drop an entry when the platform API starts serving it.
RETIRED_DATABASES = frozenset(
    database_id.casefold()
    for database_id in (
        "ENAMINE-REAL",
        "ENAMINE-AA",
        "CHEMSPACE-5B-RO5",
        "CHEMSPACE-5B-BEYOND-RO5",
        "CHEMSPACE-5B-FREEDOM",
        "EXPLORE-ENUMERATED",
        "EXPLORE-DIVERSE",
        "EXPLORE-5P4B",
        "SYNPLE-4B",
        "XTALPI",
        "MOLECULE-ONE",
        "CHEMRIYA",
        "MCULE-IN-STOCK",
        "MCULE-FULL",
        "MCULE-AA",
        "MCULE-AA-IN-STOCK",
        "MCULE-UNNATURAL-AA",
        "MCULE-UNNATURAL-AA-IN-STOCK",
        "MOLPORT",
        "CHEMSPACE-SCREENING",
        "ZINC15",
    )
)


def expand_database_alias(database: str) -> str:
    """Expand a known shorthand; preserve full IDs and private/unknown IDs verbatim."""
    return _ALIASES.get(database.casefold(), database)


def resolve_database(database: str) -> str:
    """Return the platform database ID for ``database``; no ID is rejected locally.

    Retired CHEESE IDs are sent casefolded, the form the platform API serves them under.
    """
    if database.casefold() in RETIRED_DATABASES:
        return database.casefold()
    return expand_database_alias(database)


def retired_database_message(database_id: str) -> str | None:
    """Explain why the platform API does not serve ``database_id``, if it is a retired ID."""
    if database_id.casefold() not in RETIRED_DATABASES:
        return None
    return (
        f"{database_id.upper()} was a classic CHEESE Search catalogue, and the platform API, "
        "the only one the DeepMedChem Python SDK uses, does not serve it. "
        "Run `dmc databases` to list the chemical spaces you can search."
    )
