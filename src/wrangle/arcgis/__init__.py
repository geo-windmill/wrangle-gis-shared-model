"""ArcGIS REST client, layer assessment, and the pagination ladder."""

from .assess import Assessment, assess_layer, profile_fields
from .client import ArcGISClient, ArcGISError
from .types import (
    CodedDomain,
    Extent,
    FieldProfile,
    LayerField,
    LayerInfo,
    Verdict,
)

__all__ = [
    "ArcGISClient",
    "ArcGISError",
    "Assessment",
    "assess_layer",
    "profile_fields",
    "CodedDomain",
    "Extent",
    "FieldProfile",
    "LayerField",
    "LayerInfo",
    "Verdict",
]
