"""Official Python client for the DeepMedChem platform API."""

__version__ = "0.4.0b2"

from . import aio, subsets
from .batch import BatchQuery, BatchQueryResult, BatchResult, read_queries
from .client import (
    AsyncClient,
    AsyncDMCClient,
    Client,
    DeepMedChemError,
    DMCClient,
    DMCError,
    RetiredDatabaseError,
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
    SelectionResult,
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
from .subsets import SUBSETS, Filters, Pattern, PropertyCondition, Subset

__all__ = [
    "AsyncClient",
    "AsyncDMCClient",
    "AsyncOptimization",
    "Batch",
    "BatchQuery",
    "BatchQueryResult",
    "BatchResult",
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
    "RetiredDatabaseError",
    "Run",
    "SampleResult",
    "SearchMeta",
    "SearchResult",
    "Selection",
    "SelectionResult",
    "SUBSETS",
    "Filters",
    "Pattern",
    "PropertyCondition",
    "Subset",
    "subsets",
    "SubmitReceipt",
    "SubstructureResult",
    "Usage",
    "aio",
    "catalog",
    "normalize_scores",
    "optimize",
    "prepare_order",
    "read_queries",
    "sample",
    "search",
    "substructure",
    "usage",
    "__version__",
]
