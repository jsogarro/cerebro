"""Acquire, locate, then re-resolve in a genuinely new process.

This is packet 4A's stated acceptance test for non-guarantee 4, and it is
written against the hazard 4A named alongside it: the test passes trivially if
the "fresh session" is not actually fresh, or if the locator is re-derived
rather than re-resolved.

**How freshness stops being a claim.** The re-resolution half runs in a
separate OS process (``tests/integration/acquisition_resolver_child.py``),
launched with three strings: a database URL, a snapshot store root, and an
evidence id. No engine, connection, session, identity map, store object, or
Python object of any kind crosses a process boundary, so there is nothing to
inspect and nothing to take on trust.

**How re-derivation is ruled out.** Three preconditions are asserted here
rather than described:

1. The child is **not given the expected excerpt**, and the test asserts that
   about its own argument list — so it cannot echo an answer it was handed.
2. Pointed at an evidence id that is not in the database, the child **exits
   non-zero**. Without this, a child that silently produced nothing and a
   database that agreed would be indistinguishable, which is the "``200 []``
   proves the conversion ran" failure in another costume.
3. Two evidence rows over the **same snapshot** with **different spans**
   resolve to different excerpts, each matching its own. A child that ignored
   the stored locator — resolving a fixed span, or returning the whole
   snapshot — would make these two agree, and the test would go red.
"""

import json
import subprocess
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.acquisition.fetch_tool import (
    SOURCE_FETCH_TOOL,
    FetchedSource,
    SourceFetchInput,
    snapshot_fetch_tool,
)
from src.core.acquisition.service import (
    AcquisitionStatus,
    ExcerptRequest,
    SourceAcquisitionService,
)
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.acquisition.sources import (
    LicenseDeclaration,
    SourceLicense,
    SourceType,
)
from src.core.contracts.capabilities import CapabilityGrant, SensitivityClass
from src.core.contracts.provenance import ToolInvocation
from src.core.contracts.trust import TrustClassification
from src.core.tools.audit import NullEventPublisher, ToolAuditEvent
from src.core.tools.boundary import ToolBoundary
from src.core.tools.prompts import (
    PromptIdentityVerifier,
    PromptRegistry,
    PromptRenderer,
    PromptTemplate,
    RenderedPrompt,
)
from src.core.tools.secrets import NullSecretProvider
from src.core.tools.spec import ToolCallContext
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evidence_repository import EvidenceRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [
    pytest.mark.integration,
    pytest.mark.xfail(
        strict=True,
        reason=(
            "Blocked on ArtifactRepository.create_artifact, which cannot "
            "persist nested metadata: `metadata_=dict(artifact.metadata)` is a "
            "shallow copy, but `JsonObject` freezes recursively, so the "
            "license block stays a `mappingproxy` and asyncpg raises "
            "`TypeError: Object of type mappingproxy is not JSON "
            "serializable`. The contract already ships the fix — its own "
            "`PlainSerializer`, reachable as `artifact.model_dump()"
            "['metadata']` — and the repository is another packet's file. "
            "strict=True on purpose: when that lands, these XPASS and the "
            "suite goes red, so nobody has to remember to remove this."
        ),
    ),
]

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
SCOPE = "research.acquire"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

PAGE = (
    b"Newton published the Principia in 1687.\n"
    b"It reshaped the foundations of mechanics.\n"
)
FIRST_SPAN = ExcerptRequest("char", 0, 39)
SECOND_SPAN = ExcerptRequest("char", 40, 81)


class _RecordingAuditStore:
    """Holds tool invocations in memory.

    The durable tool-invocation write path is packet 4B's table wired by 4D;
    what this test is about is the evidence and artifact rows, which *are*
    written through the real repositories against real Postgres below.
    """

    def __init__(self) -> None:
        self.invocations: list[ToolInvocation] = []

    async def find_invocation(
        self, *, run_id: str, organization_id: str | None, idempotency_key: str
    ) -> ToolInvocation | None:
        return None

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
    ) -> None:
        self.invocations.append(invocation)


async def _fetch(request: SourceFetchInput, context: ToolCallContext) -> FetchedSource:
    return FetchedSource(
        content=PAGE,
        media_type="text/plain",
        final_uri=request.source_uri,
        license=SourceLicense(
            identifier="CC-BY-4.0",
            declared_by=LicenseDeclaration.HTTP_HEADER,
            statement="Creative Commons Attribution 4.0",
        ),
    )


def _prompt() -> RenderedPrompt:
    registry = PromptRegistry()
    registry.register(
        PromptTemplate(
            prompt_id="literature.acquire",
            version="1.0.0",
            source="Find sources about $topic.",
        )
    )
    renderer = PromptRenderer(registry=registry, secret_provider=NullSecretProvider())
    return renderer.render(
        prompt_id="literature.acquire",
        version="1.0.0",
        variables={"topic": "classical mechanics"},
    )


def _registry_for(prompt: RenderedPrompt) -> PromptRegistry:
    registry = PromptRegistry()
    registry.register(
        PromptTemplate(
            prompt_id=prompt.binding.prompt_id,
            version=prompt.binding.prompt_version,
            source=prompt.template_source,
        )
    )
    return registry


@pytest.fixture(name="store_root")
def store_root_fixture(tmp_path: Path) -> Path:
    return tmp_path / "snapshots"


@pytest.fixture(name="database_url")
def database_url_fixture(postgres_container) -> str:
    return postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )


@pytest_asyncio.fixture(name="acquired")
async def acquired_fixture(db_session: AsyncSession, store_root: Path):
    """Acquire one source and **commit**, so another process can see it.

    Committing is load-bearing rather than incidental: an uncommitted row is
    visible only to the session that wrote it, so a child process reading it
    would find nothing — and the test would fail for a reason that has nothing
    to do with locator stability.
    """

    await seed_run_task_attempt(db_session, organization_id=ORG_ID)

    prompt = _prompt()
    store = FilesystemSnapshotStore(root=store_root)
    boundary = ToolBoundary(
        secret_provider=NullSecretProvider(),
        audit_store=_RecordingAuditStore(),
        event_publisher=NullEventPublisher(),
        clock=lambda: NOW,
        prompt_verifier=PromptIdentityVerifier(registry=_registry_for(prompt)),
    )
    boundary.register(
        snapshot_fetch_tool(store=store, fetcher=_fetch, timeout_seconds=5.0)
    )
    service = SourceAcquisitionService(
        boundary=boundary,
        store=store,
        artifacts=ArtifactRepository(db_session),
        evidence=EvidenceRepository(db_session),
        secret_provider=NullSecretProvider(),
        clock=lambda: NOW,
        source_types={SOURCE_FETCH_TOOL: SourceType.WEB_PAGE},
    )

    outcome = await service.acquire(
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        organization_id=str(ORG_ID),
        source_uri="https://example.org/principia",
        capability_scope=SCOPE,
        grants=[
            CapabilityGrant(
                grant_id="grant-1",
                run_id="run-1",
                task_id="task-1",
                capability_scope=SCOPE,
                tool_name=SOURCE_FETCH_TOOL,
                tool_versions=("1.0.0",),
                sensitivity=SensitivityClass.READ_ONLY,
                max_input_trust=TrustClassification.DERIVED_UNTRUSTED,
                requires_approval=False,
                issued_at=NOW - timedelta(hours=1),
                expires_at=NOW + timedelta(hours=1),
            )
        ],
        prompt=prompt,
        excerpts=[FIRST_SPAN, SECOND_SPAN],
    )
    assert outcome.status is AcquisitionStatus.ACQUIRED

    await db_session.commit()
    return outcome


def _run_child(
    database_url: str, store_root: Path, evidence_id: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.integration.acquisition_resolver_child",
            database_url,
            str(store_root),
            evidence_id,
        ],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_a_locator_re_resolves_to_identical_bytes_in_a_new_process(
    acquired, database_url: str, store_root: Path
) -> None:
    """The canonical round trip. One spawn.

    Everything else this module checks is a precondition on *this* assertion,
    and lives in the test below so the expensive path stays a single case.
    """

    expected = acquired.excerpts[0]

    child = _run_child(database_url, store_root, acquired.evidence[0].evidence_id)

    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert bytes.fromhex(result["excerpt_hex"]) == expected
    # The locator the child resolved is the one the row holds, as a string.
    assert result["locator"] == acquired.evidence[0].locator
    assert result["content_sha256"] == acquired.artifact.content_sha256
    # Pinned literally, so a change in span arithmetic cannot quietly agree
    # with itself.
    assert expected == b"Newton published the Principia in 1687."


@pytest.mark.asyncio
async def test_the_round_trip_cannot_pass_vacuously(
    acquired, database_url: str, store_root: Path
) -> None:
    """The four preconditions that make the assertion above mean something.

    Grouped into one case because each needs the same expensive fixture, and
    the point of the subprocess is that *one* test pays for a real process
    restart rather than every test paying for one.
    """

    first_id = acquired.evidence[0].evidence_id
    second_id = acquired.evidence[1].evidence_id

    # 1. The child is never handed the answer it returns. Without this, every
    #    other assertion here is satisfied by a child that echoes its input.
    arguments = " ".join([database_url, str(store_root), first_id])
    assert acquired.excerpts[0].decode() not in arguments
    assert acquired.excerpts[0].hex() not in arguments
    assert acquired.evidence[0].locator not in arguments

    # 2. An absent row exits non-zero, so silence cannot read as agreement.
    missing = _run_child(database_url, store_root, "evidence-never-written")
    assert missing.returncode != 0
    assert "no evidence row" in missing.stderr

    # 3. Two spans over one snapshot resolve to different excerpts, each its
    #    own. A child resolving a fixed span would make these agree.
    first = json.loads(_run_child(database_url, store_root, first_id).stdout)
    second = json.loads(_run_child(database_url, store_root, second_id).stdout)
    assert first["excerpt_hex"] != second["excerpt_hex"]
    assert bytes.fromhex(first["excerpt_hex"]) == acquired.excerpts[0]
    assert bytes.fromhex(second["excerpt_hex"]) == acquired.excerpts[1]
    assert first["content_sha256"] == second["content_sha256"]

    # 4. Locator sensitivity, stated rather than inferred. A resolver that
    #    ignored the locator and returned the whole snapshot would still be
    #    sensitive to its bytes argument, and check 3 would catch it only
    #    because these two spans happen to differ. Asserting that neither
    #    excerpt is the whole snapshot removes the "happen to" from the claim.
    for resolved in (first["excerpt_hex"], second["excerpt_hex"]):
        assert bytes.fromhex(resolved) != PAGE
        assert len(bytes.fromhex(resolved)) < len(PAGE)
