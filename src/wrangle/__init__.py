"""Wrangling public geospatial data into a shared model — FOSS4G NA 2026 demo."""

__version__ = "0.1.0"

# .env (beside pyproject.toml) first: several modules read a path from the environment when they are
# imported (WRANGLE_DB, WRANGLE_OUT_DIR, ...). A variable already set always wins.
from .ai import load_env

load_env()

from .cache import CacheMiss, Mode, ResponseCache  # noqa: E402
from .models import TargetField, TargetModel, available_models, load_model  # noqa: E402
from .registry import Source, SourceSet, available_features, load_sources  # noqa: E402

__all__ = [
    "__version__",
    "load_env",
    "ResponseCache",
    "Mode",
    "CacheMiss",
    "TargetModel",
    "TargetField",
    "load_model",
    "available_models",
    "Source",
    "SourceSet",
    "load_sources",
    "available_features",
]
