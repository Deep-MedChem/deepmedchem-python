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
        "abbreviation": "mcule-in-stock",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://mcule.com/",
    },
    "MCULE-FULL": {
        "abbreviation": "mcule-full",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://mcule.com/",
    },
    "MOLPORT": {
        "abbreviation": "molport",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://molport.com/",
    },
    "CHEMSPACE-SCREENING": {
        "abbreviation": "chemspace-screening",
        "biosolveit": "—",
        "type": "In-Stock",
        "availability": "Immediate",
        "success": "100%",
        "url": "https://chem-space.com/",
    },
    "ZINC15": {
        "abbreviation": "zinc15",
        "biosolveit": "—",
        "type": "Other",
        "availability": "No availability info",
        "success": "N/A",
        "url": "https://zinc15.docking.org/",
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

# Only make-on-demand spaces are currently supported by the Python API.
_ALIASES = {
    info["abbreviation"]: database_id
    for database_id, info in DATABASE_DETAILS.items()
    if info["type"] == "Make-On-Demand"
}


def resolve_database(database: str) -> str:
    """Expand a known shorthand; preserve full IDs and private/unknown IDs verbatim."""
    return _ALIASES.get(database.casefold(), database)
