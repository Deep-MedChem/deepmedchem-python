"""Official Python client for the DeepMedChem platform API."""

__version__ = "0.3.0b1"

from . import aio
from .client import (
    AsyncClient,
    AsyncDMCClient,
    Client,
    DeepMedChemError,
    DMCClient,
    DMCError,
)
from .config import Config, CredentialError, CredentialProvider
from .facade import catalog, sample, search, substructure, usage
from .models import Hit, SampleResult, SearchMeta, SearchResult, SubstructureResult, Usage
from .ordering import OrderBundle, OrderDraft, OrderMolecule, prepare_order
from .selection import Run, Selection

__all__ = [
    "AsyncClient",
    "AsyncDMCClient",
    "Client",
    "Config",
    "CredentialError",
    "CredentialProvider",
    "DeepMedChemError",
    "DMCClient",
    "DMCError",
    "Hit",
    "OrderBundle",
    "OrderDraft",
    "OrderMolecule",
    "Run",
    "SampleResult",
    "SearchMeta",
    "SearchResult",
    "Selection",
    "SubstructureResult",
    "Usage",
    "aio",
    "catalog",
    "prepare_order",
    "sample",
    "search",
    "substructure",
    "usage",
    "__version__",
]
