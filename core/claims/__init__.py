"""Versioned public-claim governance and repository linting."""

from core.claims.linter import DEFAULT_SURFACE_GLOBS, discover_public_surfaces, scan_surfaces
from core.claims.registry import ClaimRegistryService, load_claim_registry, validate_claims
from core.claims.schema import CLAIM_REGISTRY_SCHEMA_VERSION, ClaimRegistryDocument, ValidationReport

__all__ = [
    "CLAIM_REGISTRY_SCHEMA_VERSION",
    "DEFAULT_SURFACE_GLOBS",
    "ClaimRegistryDocument",
    "ClaimRegistryService",
    "ValidationReport",
    "discover_public_surfaces",
    "load_claim_registry",
    "scan_surfaces",
    "validate_claims",
]
