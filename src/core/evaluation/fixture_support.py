"""Fixture claim-support verdicts: containment against an expert-labelled manifest.

This is not a semantic judge. A manifest of authored labels says which claim
text is supported, partially supported, disputed or unsupported by which
evidence snapshot, and quotes the span the label rests on. A label only stands
when that quote is contained in the evidence excerpt actually read from the
snapshot; otherwise the claim is recorded ``unsupported`` with the evidence it
failed against. A claim the manifest does not name gets no verdict, so the
resolver records it as ``not_attempted``. Nothing here scores similarity, so a
negated or paraphrased claim cannot inherit a nearby positive label.

Every verdict says ``method=containment`` and names the manifest, so a stored
row can never be read as calibrated semantic accuracy.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from src.core.claims.resolution import ClaimVerdict
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupportStatus,
    ProducerKind,
)
from src.core.contracts.citation import CitationField, MetadataCandidate
from src.core.contracts.citation import MetadataProvenance as Provenance
from src.services.text_alignment import MatchKind, locate

FIXTURE_EVALUATOR_ID: Final = "fixture.claim_support"
FIXTURE_EVALUATOR_VERSION: Final = "1"
METHOD: Final = "containment"


class FixtureManifestError(ValueError):
    pass


@dataclass(frozen=True)
class FixtureEntry:
    claim_text: str
    status: ClaimSupportStatus
    evidence_sha256: str
    quote: str
    explanation: str
    authors: tuple[str, ...] = ()
    year: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class FixtureManifest:
    manifest_id: str
    sha256: str
    entries: Mapping[str, FixtureEntry]

    @classmethod
    def from_json(cls, text: str, expected_sha256: str) -> "FixtureManifest":
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != expected_sha256:
            raise FixtureManifestError("claim-support fixture manifest hash mismatch")
        raw = json.loads(text)
        entries: dict[str, FixtureEntry] = {}
        for item in raw["entries"]:
            entry = FixtureEntry(
                claim_text=item["claim_text"],
                status=ClaimSupportStatus(item["status"]),
                evidence_sha256=item["evidence_sha256"],
                quote=item["quote"],
                explanation=item["explanation"],
                authors=tuple(item.get("authors", ())),
                year=item.get("year"),
                doi=item.get("doi"),
            )
            if entry.claim_text in entries:
                raise FixtureManifestError(
                    f"duplicate fixture claim {entry.claim_text!r}"
                )
            entries[entry.claim_text] = entry
        return cls(manifest_id=raw["manifest_id"], sha256=digest, entries=entries)

    def metadata_candidates(
        self, evidence_digests: Mapping[str, str]
    ) -> dict[str, list[MetadataCandidate]]:
        """Citation metadata the manifest's author supplied, by evidence id.

        It is labelled ``user_supplied`` because a person typed it; the binder
        never accepts a value inferred from the evidence.
        """
        out: dict[str, list[MetadataCandidate]] = {}
        for entry in self.entries.values():
            for evidence_id, digest in evidence_digests.items():
                if digest != entry.evidence_sha256:
                    continue
                values = [
                    MetadataCandidate(
                        field=CitationField.AUTHOR,
                        value=author,
                        provenance=Provenance.USER_SUPPLIED,
                        position=position,
                    )
                    for position, author in enumerate(entry.authors)
                ]
                for field, value in (
                    (CitationField.YEAR, entry.year),
                    (CitationField.DOI, entry.doi),
                ):
                    if value:
                        values.append(
                            MetadataCandidate(
                                field=field,
                                value=value,
                                provenance=Provenance.USER_SUPPLIED,
                            )
                        )
                out.setdefault(evidence_id, []).extend(values)
        return out


def load_fixture_manifest(
    path: str | None, sha256: str | None
) -> FixtureManifest | None:
    """The pinned manifest at ``path``, or ``None`` when none is configured."""
    if not path:
        return None
    return FixtureManifest.from_json(Path(path).read_text(), sha256 or "")


@dataclass(frozen=True)
class EvidenceExcerpt:
    """What was actually read for one run-owned evidence row.

    ``excerpt`` is ``None`` when the snapshot could not be read back at the
    digest the evidence cites; such evidence can never support a claim.
    """

    evidence_id: str
    content_sha256: str
    excerpt: str | None


@dataclass(frozen=True)
class FixtureJudgement:
    verdicts: tuple[ClaimVerdict, ...]
    unmapped: int
    integrity_failures: int


def _explain(manifest: FixtureManifest, entry: FixtureEntry, outcome: str) -> str:
    return (
        f"method={METHOD} fixture={manifest.manifest_id}@{manifest.sha256[:12]}: "
        f"{outcome} {entry.explanation}"
    )


def judge(
    manifest: FixtureManifest,
    claims: Sequence[tuple[str, str]],
    evidence: Sequence[EvidenceExcerpt],
    *,
    evaluated_at: datetime,
) -> FixtureJudgement:
    """One verdict per manifest-named claim; ``claims`` is ``(claim_id, text)``."""
    by_digest: dict[str, list[EvidenceExcerpt]] = {}
    for item in evidence:
        by_digest.setdefault(item.content_sha256, []).append(item)

    verdicts: list[ClaimVerdict] = []
    unmapped = integrity_failures = 0
    for claim_id, text in claims:
        entry = manifest.entries.get(text)
        if entry is None:
            unmapped += 1
            continue

        def verdict(
            status: ClaimSupportStatus,
            explanation: str,
            evidence_ids: tuple[str, ...] = (),
            reason: AbsentEvidenceReason | None = None,
            *,
            claim_id: str = claim_id,
        ) -> ClaimVerdict:
            return ClaimVerdict(
                claim_id=claim_id,
                status=status,
                explanation=explanation,
                evaluator_id=FIXTURE_EVALUATOR_ID,
                evaluator_version=FIXTURE_EVALUATOR_VERSION,
                producer_kind=ProducerKind.SYSTEM,
                evaluated_at=evaluated_at,
                evidence_ids=evidence_ids,
                absent_evidence_reason=reason,
            )

        candidates = by_digest.get(entry.evidence_sha256, [])
        if not candidates:
            verdicts.append(
                verdict(
                    ClaimSupportStatus.UNSUPPORTED,
                    _explain(
                        manifest, entry, "no run evidence has the labelled digest."
                    ),
                    reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
                )
            )
            continue
        readable = [c for c in candidates if c.excerpt is not None]
        if not readable:
            integrity_failures += 1
            verdicts.append(
                verdict(
                    ClaimSupportStatus.UNSUPPORTED,
                    _explain(manifest, entry, "the snapshot failed its digest check."),
                    reason=AbsentEvidenceReason.RETRIEVAL_FAILED,
                )
            )
            continue
        if entry.status is ClaimSupportStatus.UNSUPPORTED:
            verdicts.append(
                verdict(
                    ClaimSupportStatus.UNSUPPORTED,
                    _explain(manifest, entry, "labelled unsupported."),
                    reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
                )
            )
            continue
        holders = tuple(
            c.evidence_id
            for c in readable
            if c.excerpt is not None
            and locate(entry.quote, c.excerpt).kind is not MatchKind.NOT_FOUND
        )
        if holders:
            verdicts.append(
                verdict(
                    entry.status,
                    _explain(manifest, entry, f"labelled {entry.status.value};"),
                    holders,
                )
            )
        else:
            integrity_failures += 1
            verdicts.append(
                verdict(
                    ClaimSupportStatus.UNSUPPORTED,
                    _explain(
                        manifest, entry, "the labelled span is not in the evidence."
                    ),
                    tuple(c.evidence_id for c in readable),
                )
            )
    return FixtureJudgement(tuple(verdicts), unmapped, integrity_failures)
