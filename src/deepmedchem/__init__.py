"""Official Python client for the DeepMedChem platform API."""

__version__ = "0.2.0b2"

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
from .facade import catalog, sample, search, substructure
from .models import Hit, SampleResult, SearchMeta, SearchResult, SubstructureResult
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
    "Run",
    "SampleResult",
    "SearchMeta",
    "SearchResult",
    "Selection",
    "SubstructureResult",
    "aio",
    "catalog",
    "sample",
    "search",
    "substructure",
    "__version__",
]
