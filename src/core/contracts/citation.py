"""Citation metadata contracts.

Citation text is a deterministic rendering over metadata the run holds; a model
never types an author, year, or DOI. The rule that makes this true lives here,
at construction: a field whose provenance is ``model_inferred`` cannot be built,
so no renderer ever has to remember to refuse one.
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from .base import ContentSha256, ContractId, ContractModel, require_unique
from .provenance import ClaimSupportStatus

__all__ = [
    "BindingGap",
    "BindingGapReason",
    "CitationBindingResult",
    "CitationField",
    "CitationMetadata",
    "CitationRecord",
    "CitationStatus",
    "ClaimSpan",
    "MetadataCandidate",
    "MetadataProvenance",
    "SourcedValue",
]


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


class CitationField(StrEnum):
    AUTHOR = "author"
    YEAR = "year"
    DOI = "doi"


class MetadataCandidate(ContractModel):
    """A metadata value offered for binding, before provenance is judged.

    Unlike :class:`SourcedValue` this may carry ``model_inferred``: the binder
    must be able to *see* an inferred value in order to refuse it with a typed
    gap rather than have it fail construction upstream. ``position`` orders
    authors; it is ignored for other fields.
    """

    field: CitationField
    value: NonBlank
    provenance: MetadataProvenance
    evidence_id: ContractId | None = None
    position: int = Field(default=0, ge=0)


class ClaimSpan(ContractModel):
    """A half-open character span of the draft that one claim occupies."""

    claim_id: ContractId
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    evidence_ids: tuple[ContractId, ...] = ()

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a claim span must be non-empty (end > start)")
        require_unique(self.evidence_ids, field_name="evidence_ids")
        return self


class BindingGapReason(StrEnum):
    NO_EVIDENCE_SLOT = "no_evidence_slot"
    EVIDENCE_NOT_IN_RUN = "evidence_not_in_run"
    EVIDENCE_NOT_IN_SUPPORT = "evidence_not_in_support"
    NO_CLAIM_SUPPORT = "no_claim_support"
    CLAIM_UNSUPPORTED = "claim_unsupported"
    METADATA_UNRENDERABLE = "metadata_unrenderable"
    METADATA_MISSING = "metadata_missing"
    METADATA_CONFLICT = "metadata_conflict"
    UNRENDERED_CITATION_PROSE = "unrendered_citation_prose"


class CitationStatus(StrEnum):
    MINTED = "minted"
    MINTED_PARTIAL = "minted_partial"
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"


class BindingGap(ContractModel):
    """A binding-specific diagnostic; not a second claim-support store."""

    reason: BindingGapReason
    claim_id: ContractId | None = None
    evidence_id: ContractId | None = None
    field: CitationField | None = None
    detail: str = ""


class CitationRecord(ContractModel):
    """One rendered citation, or the explicit marker standing in for one.

    ``citation_id`` is a digest of content only (evidence snapshot hash and
    locator, bound metadata with provenance, support verdict, style and binder
    version) and is ``None`` unless a citation was actually minted.
    """

    citation_id: ContentSha256 | None
    claim_id: ContractId
    evidence_id: ContractId | None
    status: CitationStatus
    support: ClaimSupportStatus | None
    in_text: str
    metadata: CitationMetadata
    missing_fields: tuple[str, ...] = ()


class CitationBindingResult(ContractModel):
    records: tuple[CitationRecord, ...]
    gaps: tuple[BindingGap, ...]
    rendered_text: str

    @property
    def clean(self) -> bool:
        return not self.gaps
