"""Official Python client for the DeepMedChem platform API."""

__version__ = "0.4.0b1"

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
from .models import (
    Batch,
    Hit,
    Molecule,
    Observation,
    OptimizationResource,
    OptimizationResult,
    SampleResult,
    SearchMeta,
    SearchResult,
    SubmitReceipt,
    SubstructureResult,
    Usage,
)
from .optimization import (
    AsyncOptimization,
    Optimization,
    normalize_scores,
    optimize,
)
from .ordering import OrderBundle, OrderDraft, OrderMolecule, prepare_order
from .selection import Run, Selection

__all__ = [
    "AsyncClient",
    "AsyncDMCClient",
    "AsyncOptimization",
    "Batch",
    "Client",
    "Config",
    "CredentialError",
    "CredentialProvider",
    "DeepMedChemError",
    "DMCClient",
    "DMCError",
    "Hit",
    "Molecule",
    "Observation",
    "Optimization",
    "OptimizationResource",
    "OptimizationResult",
    "OrderBundle",
    "OrderDraft",
    "OrderMolecule",
    "Run",
    "SampleResult",
    "SearchMeta",
    "SearchResult",
    "Selection",
    "SubmitReceipt",
    "SubstructureResult",
    "Usage",
    "aio",
    "catalog",
    "normalize_scores",
    "optimize",
    "prepare_order",
    "sample",
    "search",
    "substructure",
    "usage",
    "__version__",
]
