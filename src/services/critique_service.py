"""Run the critique panel over a stored document and persist what it keeps.

The panel (``src.agents.critique``) is pure. This module owns the provider model,
the consent text a caller must agree to, the budget reservation, and the plain
text report rendered on each read.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.critique import panel
from src.agents.critique.model import (
    GeminiReviewModel,
    ReviewModel,
    TracingEnabledRefusal,
)
from src.agents.critique.panel import (
    PRIOR_FINDINGS_MAX_CHARS,
    PanelResult,
    claims_reference_verification,
    run_panel,
    template_sha256,
)
from src.agents.critique.roles import ROLES
from src.core.config import settings
from src.core.memory_encryption import document_cipher
from src.middleware.tenant_context import set_postgres_tenant_context
from src.repositories.critique_repository import (
    CritiqueRepository,
    CritiqueView,
)
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services.document_jobs import JobLimits, Outcome, SessionFactory
from src.services.gemini_limiter import RateLimiter

# The API enforces this at admission; the panel refuses anything longer as well.
CRITIQUE_MAX_CHARS = panel.CRITIQUE_MAX_CHARS
CRITIQUE_MODEL_ID = "gemini-2.5-flash"
CRITIQUE_TEMPERATURE = 0.2
CRITIQUE_PER_WORKER_CONCURRENCY = 1
CRITIQUE_PER_USER_DAILY = 5
CRITIQUE_PER_ORG_CONCURRENT = 2
CRITIQUE_RESERVE_FACTOR = 10
CRITIQUE_PROMPT_OVERHEAD_CHARS = 8_000
CRITIQUE_PRIOR_FINDINGS_CHARS = 2 * PRIOR_FINDINGS_MAX_CHARS
CRITIQUE_ORG_DAILY_CHARS = 60_000_000
CRITIQUE_GLOBAL_DAILY_CHARS = 300_000_000
CALLS_PER_RUN_MAX = 2 * len(ROLES)
PROVIDER = "google-gemini"
PROVIDER_HOSTS = ["generativelanguage.googleapis.com"]

CRITIQUE_LIMITS = JobLimits(
    per_user_daily=CRITIQUE_PER_USER_DAILY,
    per_org_daily_units=CRITIQUE_ORG_DAILY_CHARS,
    global_daily_units=CRITIQUE_GLOBAL_DAILY_CHARS,
    per_org_concurrent=CRITIQUE_PER_ORG_CONCURRENT,
)

# One limiter for critique and revision, so a worker never exceeds 10 model
# requests a minute or 2 at once across both. It is per worker.
MODEL_RATE_LIMITER = RateLimiter(rate_limit=10, rate_period=60, max_concurrent=2)

CONSENT_TEXT_V1 = (
    "The full text of this document will be sent in up to 10 requests to Google's "
    "Gemini API (host generativelanguage.googleapis.com, model gemini-2.5-flash) "
    "using this service's shared API key. The provider's terms for that key govern "
    "how long the provider keeps the text. Do not upload material you are not "
    "entitled to share with a third party, such as a manuscript you received for "
    "confidential peer review. Results are model-generated and uncalibrated."
)


@dataclass(frozen=True)
class Consent:
    version: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


CONSENTS = (Consent("critique-consent-v1", CONSENT_TEXT_V1),)

REPORT_FOOTER = (
    "References were not checked against any registry.",
    "No text-reuse check was attached to this critique.",
    "Delimiting reduces but does not prevent instructions inside the document from "
    "influencing the reviewers.",
    "A missing finding is not evidence that a problem is absent.",
)
REPORT_DISCLAIMER = (
    "Model-assisted and uncalibrated. All roles use one model family; they are not "
    "independent reviewers."
)
REFERENCE_WARNING = (
    "Warning: this finding asserts something about references that no part of this "
    "system checked."
)


def reserved_units(char_count: int) -> int:
    """Worst-case input characters one critique can send to the provider."""
    return (
        char_count + CRITIQUE_PROMPT_OVERHEAD_CHARS
    ) * CRITIQUE_RESERVE_FACTOR + CRITIQUE_PRIOR_FINDINGS_CHARS


def prompt_versions() -> dict[str, str]:
    return {role.name: template_sha256(role) for role in ROLES}


def build_review_model() -> ReviewModel:
    """The one provider model; ``max_retries=1`` so a call is one provider request."""
    chat = ChatGoogleGenerativeAI(
        model=CRITIQUE_MODEL_ID,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=CRITIQUE_TEMPERATURE,
        max_retries=1,
    )
    return GeminiReviewModel(chat, MODEL_RATE_LIMITER)


def make_work(
    *,
    session_factory: SessionFactory,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    model: ReviewModel,
) -> Callable[[], Awaitable[PanelResult | None]]:
    async def work() -> PanelResult | None:
        async with session_factory() as session:
            await set_postgres_tenant_context(session, str(organization_id))
            document = await UploadedDocumentRepository(
                session, document_cipher()
            ).get_text(document_id, organization_id=organization_id, user_id=user_id)
        if document is None:
            raise LookupError("document unavailable")
        try:
            return await run_panel(document, model=model)
        except TracingEnabledRefusal:
            # Nothing was sent; the run fails with its reason.
            return None

    return work


def final_status(panel: PanelResult) -> str:
    succeeded = sum(r.status == "succeeded" for r in panel.roles)
    if succeeded == len(ROLES):
        return "succeeded"
    return "partial" if succeeded else "failed"


def role_outcomes(panel: PanelResult) -> dict[str, dict[str, Any]]:
    return {
        r.role: {
            "status": r.status,
            "kept": len(r.issues),
            "dropped": dict(r.drops),
            "fields_sanitized": r.fields_sanitized,
            "finish_reason": r.finish_reason,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "error_type": r.error_type,
        }
        for r in panel.roles
    }


def make_persist(
    *, critique_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID
) -> Callable[[AsyncSession, PanelResult | None], Awaitable[Outcome]]:
    async def persist(session: AsyncSession, panel: PanelResult | None) -> Outcome:
        if panel is None:
            return "failed", {"status_reason": "tracing_enabled"}
        await CritiqueRepository(session, document_cipher()).add_findings(
            critique_id=critique_id,
            organization_id=organization_id,
            user_id=user_id,
            issues=[issue for r in panel.roles for issue in r.issues],
        )
        status = final_status(panel)
        fields: dict[str, Any] = {"role_outcomes": role_outcomes(panel)}
        if status == "failed":
            fields["status_reason"] = "all_roles_failed"
        return status, fields

    return persist


def _one_line(text: str) -> str:
    """A quote is source text, so it may hold line breaks that would forge report lines."""
    return " \u23ce ".join(text.splitlines())


def _dropped(counts: dict[str, int]) -> str:
    return ", ".join(f"{reason} {n}" for reason, n in sorted(counts.items())) or "none"


def render_report(critique: CritiqueView, document_text_sha256: str) -> str:
    """Plain text only, so a client can never auto-load a URL the model was induced to write."""
    completed = critique.completed_at.isoformat() if critique.completed_at else "n/a"
    versions = ", ".join(
        f"{role} {sha[:12]}" for role, sha in critique.prompt_versions.items()
    )
    lines = [
        "Critique report",
        f"Model: {critique.model_id} (temperature {critique.temperature})",
        f"Status: {critique.status}"
        + (f" ({critique.status_reason})" if critique.status_reason else ""),
        f"Completed: {completed}",
        f"Document text sha256: {document_text_sha256}",
        f"Prompt versions: {versions}",
        REPORT_DISCLAIMER,
        "",
        "Roles (a finding is kept only when its quote is located in the document; "
        "dropped counts say how many were not located, not how precise the rest are):",
    ]
    for role, outcome in critique.role_outcomes.items():
        lines.append(
            f"- {role}: {outcome['status']}, kept {outcome['kept']}, "
            f"dropped {_dropped(outcome['dropped'])}"
        )
    lines += ["", f"Findings: {len(critique.findings)}"]
    for number, finding in enumerate(critique.findings, start=1):
        lines += [
            "",
            f"{number}. [{finding.severity}] {finding.role} / {finding.category}, "
            f"page {finding.page}",
            f'   Quote: "{_one_line(finding.quote)}"',
        ]
        if finding.second_quote:
            lines.append(f'   Second quote: "{_one_line(finding.second_quote)}"')
        lines += [f"   Problem: {finding.problem}", f"   Fix: {finding.fix}"]
        if claims_reference_verification(
            finding.problem
        ) or claims_reference_verification(finding.fix):
            lines.append(f"   {REFERENCE_WARNING}")
    lines += ["", *REPORT_FOOTER]
    return "\n".join(lines) + "\n"
