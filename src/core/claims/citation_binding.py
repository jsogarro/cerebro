"""Bind claim spans to citations over run-owned evidence, deterministically.

A model never types a citation: this function builds one from metadata that
carries provenance, renders it through the author-date renderer, and then scans
the rendered draft for citation-shaped prose the renderer did not emit.

Policy:

* Only ``registry``/``document``/``user_supplied`` metadata binds. A
  ``model_inferred`` candidate, or one citing evidence outside the run, is
  rejected with a typed gap; it is never rendered and never replaced by a guess.
* Disagreeing values for ``year``/``doi`` bind nothing (``METADATA_CONFLICT``).
* ``citation_id`` hashes content only: the evidence snapshot digest and locator,
  the bound metadata with provenance, the support verdict, style and binder
  version. No run id, timestamp, or input ordering reaches it. Authors are
  ordered by their explicit ``position``, so author order is content, not an
  accident of the input.
* Not checked here: that ``Evidence.content_sha256`` matches the snapshot bytes
  (source-integrity verification is a separate stage).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence

from src.core.claims.citation_prose import find_unrendered_citation_prose
from src.core.claims.citation_render import (
    AUTHOR_DATE_MINIMAL,
    RenderedCitation,
    render,
)
from src.core.contracts.citation import (
    BindingGap,
    BindingGapReason,
    CitationBindingResult,
    CitationField,
    CitationMetadata,
    CitationRecord,
    CitationStatus,
    ClaimSpan,
    MetadataCandidate,
    MetadataProvenance,
    SourcedValue,
)
from src.core.contracts.provenance import ClaimSupport, ClaimSupportStatus, Evidence

BINDER_VERSION = "1"

_REASON = BindingGapReason


class CitationBindingError(ValueError):
    code = "citation.invalid_claim_spans"


def _bind_metadata(
    claim_id: str,
    evidence_id: str,
    candidates: Sequence[MetadataCandidate],
    run_evidence: Mapping[str, Evidence],
) -> tuple[CitationMetadata, list[BindingGap]]:
    gaps: list[BindingGap] = []

    def gap(reason: BindingGapReason, field: CitationField, detail: str) -> None:
        gaps.append(
            BindingGap(
                reason=reason,
                claim_id=claim_id,
                evidence_id=evidence_id,
                field=field,
                detail=detail,
            )
        )

    usable: dict[CitationField, list[MetadataCandidate]] = {
        f: [] for f in CitationField
    }
    ordered = sorted(
        candidates,
        key=lambda c: (c.field, c.position, c.value, c.provenance, c.evidence_id or ""),
    )
    for c in ordered:
        if c.provenance is MetadataProvenance.MODEL_INFERRED:
            gap(_REASON.METADATA_UNRENDERABLE, c.field, "model-inferred value rejected")
        elif (
            c.evidence_id is None
            and c.provenance is not MetadataProvenance.USER_SUPPLIED
        ):
            gap(
                _REASON.EVIDENCE_NOT_IN_RUN,
                c.field,
                f"{c.provenance.value} value names no evidence",
            )
        elif c.evidence_id is not None and c.evidence_id not in run_evidence:
            gap(
                _REASON.EVIDENCE_NOT_IN_RUN,
                c.field,
                f"unknown evidence {c.evidence_id!r}",
            )
        else:
            usable[c.field].append(c)

    def sourced(c: MetadataCandidate) -> SourcedValue:
        return SourcedValue(
            value=c.value, provenance=c.provenance, evidence_id=c.evidence_id
        )

    by_position: dict[int, list[MetadataCandidate]] = {}
    for c in usable[CitationField.AUTHOR]:
        by_position.setdefault(c.position, []).append(c)
    authors: list[SourcedValue] = []
    if any(len({c.value for c in group}) > 1 for group in by_position.values()):
        gap(
            _REASON.METADATA_CONFLICT,
            CitationField.AUTHOR,
            "authors disagree at a position",
        )
    else:
        authors = [sourced(by_position[p][0]) for p in sorted(by_position)]

    single: dict[CitationField, SourcedValue | None] = {}
    for field in (CitationField.YEAR, CitationField.DOI):
        values = {c.value for c in usable[field]}
        if len(values) > 1:
            gap(_REASON.METADATA_CONFLICT, field, f"{len(values)} distinct values")
            single[field] = None
        else:
            single[field] = sourced(usable[field][0]) if usable[field] else None

    metadata = CitationMetadata(
        authors=tuple(authors),
        year=single[CitationField.YEAR],
        doi=single[CitationField.DOI],
    )
    return metadata, gaps


def _citation_id(
    evidence: Evidence,
    metadata: CitationMetadata,
    support: ClaimSupportStatus,
    style: str,
) -> str:
    payload = {
        "evidence": {
            "content_sha256": evidence.content_sha256,
            "locator": evidence.locator,
        },
        "metadata": metadata.model_dump(mode="json"),
        "support": support.value,
        "style": style,
        "binder": BINDER_VERSION,
    }
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate_spans(text: str, spans: Sequence[ClaimSpan]) -> None:
    ids = [s.claim_id for s in spans]
    if len(ids) != len(set(ids)):
        raise CitationBindingError("duplicate claim_id in claim spans")
    for s in spans:
        if s.end > len(text) or s.end <= s.start:
            raise CitationBindingError(
                f"claim span {s.claim_id!r} is outside the draft"
            )
        if s.end < len(text) and text[s.end - 1].isalnum() and text[s.end].isalnum():
            raise CitationBindingError(f"claim span {s.claim_id!r} ends mid-word")


def bind_citations(
    text: str,
    spans: Sequence[ClaimSpan],
    *,
    evidence: Mapping[str, Evidence],
    support: Mapping[str, ClaimSupport],
    metadata: Mapping[str, Sequence[MetadataCandidate]],
    style: str = AUTHOR_DATE_MINIMAL,
) -> CitationBindingResult:
    """Render citations after each claim span of ``text`` and report every gap.

    ``text`` is the draft prose with no citation markers. ``metadata`` maps the
    cited evidence id to the candidates offered for it.
    """
    _validate_spans(text, spans)
    gaps: list[BindingGap] = []
    records: list[CitationRecord] = []
    # claim_id -> [(record, rendered citation)] in emission order
    pieces: dict[str, list[tuple[CitationRecord, RenderedCitation]]] = {}

    ordered_spans = sorted(spans, key=lambda s: (s.end, s.start, s.claim_id))
    for span in ordered_spans:
        cid = span.claim_id
        if not span.evidence_ids:
            gaps.append(BindingGap(reason=_REASON.NO_EVIDENCE_SLOT, claim_id=cid))
            continue
        verdict = support.get(cid)
        if verdict is None or verdict.status is ClaimSupportStatus.UNSUPPORTED:
            gaps.append(
                BindingGap(
                    reason=_REASON.NO_CLAIM_SUPPORT
                    if verdict is None
                    else _REASON.CLAIM_UNSUPPORTED,
                    claim_id=cid,
                )
            )
            empty = CitationMetadata()
            rendered = render(empty, style, support=ClaimSupportStatus.UNSUPPORTED)
            record = CitationRecord(
                citation_id=None,
                claim_id=cid,
                evidence_id=None,
                status=CitationStatus.UNSUPPORTED,
                support=None if verdict is None else verdict.status,
                in_text=rendered.text,
                metadata=empty,
            )
            records.append(record)
            pieces[cid] = [(record, rendered)]
            continue

        pieces[cid] = []
        for eid in sorted(span.evidence_ids):
            source = evidence.get(eid)
            if source is None:
                gaps.append(
                    BindingGap(
                        reason=_REASON.EVIDENCE_NOT_IN_RUN,
                        claim_id=cid,
                        evidence_id=eid,
                    )
                )
                continue
            if eid not in verdict.evidence_ids:
                gaps.append(
                    BindingGap(
                        reason=_REASON.EVIDENCE_NOT_IN_SUPPORT,
                        claim_id=cid,
                        evidence_id=eid,
                    )
                )
                continue
            meta, meta_gaps = _bind_metadata(cid, eid, metadata.get(eid, ()), evidence)
            gaps.extend(meta_gaps)
            rendered = render(meta, style, support=verdict.status)
            for field in rendered.missing_fields:
                gaps.append(
                    BindingGap(
                        reason=_REASON.METADATA_MISSING,
                        claim_id=cid,
                        evidence_id=eid,
                        field=CitationField(field),
                    )
                )
            missing = rendered.missing_fields
            if rendered.missing_fields:
                status, citation_id = CitationStatus.INCOMPLETE, None
            else:
                if meta.doi is None:
                    missing, status = ("doi",), CitationStatus.MINTED_PARTIAL
                else:
                    status = CitationStatus.MINTED
                citation_id = _citation_id(source, meta, verdict.status, style)
            record = CitationRecord(
                citation_id=citation_id,
                claim_id=cid,
                evidence_id=eid,
                status=status,
                support=verdict.status,
                in_text=rendered.text,
                metadata=meta,
                missing_fields=missing,
            )
            records.append(record)
            pieces[cid].append((record, rendered))

    out: list[str] = []
    rendered_spans: list[tuple[int, int]] = []
    cursor = shift = 0
    for span in ordered_spans:
        out.append(text[cursor : span.end])
        cursor = span.end
        base = span.end + shift
        for _, rendered in pieces.get(span.claim_id, ()):
            out.append(" " + rendered.text)
            base += 1
            rendered_spans.extend((base + a, base + b) for a, b in rendered.spans)
            base += len(rendered.text)
            shift += 1 + len(rendered.text)
    out.append(text[cursor:])
    rendered_text = "".join(out)

    # The draft carries no citation markers, so any hit in it is residual prose.
    # Scanning it too catches a citation the insertion points split in two.
    findings = find_unrendered_citation_prose(rendered_text, rendered_spans)
    seen_text = {f.text for f in findings}
    findings += tuple(
        f for f in find_unrendered_citation_prose(text) if f.text not in seen_text
    )
    for finding in findings:
        gaps.append(
            BindingGap(
                reason=_REASON.UNRENDERED_CITATION_PROSE,
                detail=f"{finding.kind.value} at {finding.start}-{finding.end}: {finding.text}",
            )
        )

    return CitationBindingResult(
        records=tuple(sorted(records, key=lambda r: (r.claim_id, r.evidence_id or ""))),
        gaps=tuple(
            sorted(
                set(gaps),
                key=lambda g: (
                    g.reason.value,
                    g.claim_id or "",
                    g.evidence_id or "",
                    g.field or "",
                    g.detail,
                ),
            )
        ),
        rendered_text=rendered_text,
    )
