"""Citation metadata contracts.

Citation text is a deterministic rendering over metadata the run holds; a model
never types an author, year, or DOI. The rule that makes this true lives here,
at construction: a field whose provenance is ``model_inferred`` cannot be built,
so no renderer ever has to remember to refuse one.
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from .base import ContractId, ContractModel

__all__ = ["CitationMetadata", "MetadataProvenance", "SourcedValue"]


class MetadataProvenance(StrEnum):
    REGISTRY = "registry"
    DOCUMENT = "document"
    USER_SUPPLIED = "user_supplied"
    MODEL_INFERRED = "model_inferred"


_EVIDENCE_BACKED = frozenset({MetadataProvenance.REGISTRY, MetadataProvenance.DOCUMENT})

NonBlank = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


class SourcedValue(ContractModel):
    """One metadata value and where it came from."""

    value: NonBlank
    provenance: MetadataProvenance
    evidence_id: ContractId | None = None

    @model_validator(mode="after")
    def _check_provenance(self) -> Self:
        if self.provenance is MetadataProvenance.MODEL_INFERRED:
            raise ValueError("model_inferred metadata is never renderable")
        if self.provenance in _EVIDENCE_BACKED and self.evidence_id is None:
            raise ValueError(
                f"{self.provenance.value} metadata requires an evidence_id"
            )
        return self


class CitationMetadata(ContractModel):
    """CSL-JSON-shaped metadata: author family names, issued year, DOI."""

    authors: tuple[SourcedValue, ...] = ()
    year: SourcedValue | None = None
    doi: SourcedValue | None = None
