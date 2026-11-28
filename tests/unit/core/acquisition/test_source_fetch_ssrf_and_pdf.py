"""SSRF hardening and PDF full-text extraction for ``source.fetch``."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fpdf import FPDF

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    MCP_TOOL_OUTBOUND_HOSTS,
    MCP_TOOL_SENSITIVITIES,
    TOOL_SOURCE_FETCH,
)
from src.agents.tools.mediation import (
    InMemoryToolAuditStore,
    ToolCallIdentity,
    build_tool_boundary,
    plain,
)
from src.core.acquisition.fetch_tool import (
    SOURCE_FETCH_TOOL,
    HardenedHttpFetcher,
    SourceFetchInput,
    SSRFBlockedError,
    hardened_source_fetch_tool,
    is_disallowed_ip,
)
from src.core.acquisition.pdf_text import ExtractedText, extract_pdf_text
from src.core.acquisition.resolution import resolve
from src.core.acquisition.service import (
    SNAPSHOT_ARTIFACT_KIND,
    AcquisitionStatus,
    ExcerptRequest,
    SourceAcquisitionService,
)
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.acquisition.sources import SourceType
from src.core.capabilities.issuer import PlanCapabilityIssuer
from src.core.contracts import (
    CapabilityDenialReason,
    CapabilityGrant,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    SensitivityClass,
    TrustClassification,
    WorkerAssignment,
)
from src.core.contracts.redaction import redact, snapshot_digest
from src.core.tools import ToolBoundary, ToolOutcomeStatus
from src.core.tools.audit import NullEventPublisher
from src.core.tools.outbound import OUTBOUND_TOOL_HOSTS, SOURCE_FETCH_OUTBOUND_HOSTS
from src.core.tools.prompts import (
    PromptIdentityVerifier,
    PromptRegistry,
    PromptRenderer,
    PromptTemplate,
)
from src.core.tools.secrets import NullSecretProvider
from src.models.execution_authority import ExecutionAuthorityBinding
from tests.unit.core.acquisition.test_acquisition_service import (
    FakeArtifactRepository,
    FakeAuditStore,
    FakeEvidenceRepository,
)

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
PUBLIC_IP = "93.184.216.34"


def _make_pdf(pages: list[str]) -> bytes:
    doc = FPDF()
    doc.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    for text in pages:
        doc.add_page()
        doc.set_font("Helvetica", size=12)
        if text:
            doc.cell(text=text)
    return bytes(doc.output())


async def _bounded_pdf_extractor(data: bytes) -> tuple[ExtractedText, int]:
    return extract_pdf_text(data, max_pages=300, max_chars=2_000_000), 0


def _static_dns(mapping: dict[str, list[str]]) -> Any:
    async def resolver(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise socket.gaierror(f"name not resolved: {host}")
        return list(mapping[host])

    return resolver


def _grant(
    *,
    allowed_hosts: tuple[str, ...] = ("arxiv.org",),
    now: datetime | None = None,
) -> CapabilityGrant:
    anchor = now or datetime.now(UTC)
    return CapabilityGrant(
        grant_id="grant-source-fetch",
        run_id="run-1",
        task_id="task-1",
        capability_scope=f"plan-issued:{SOURCE_FETCH_TOOL}",
        tool_name=SOURCE_FETCH_TOOL,
        tool_versions=("1.0.0",),
        sensitivity=SensitivityClass.EXTERNAL_READ,
        max_input_trust=TrustClassification.DERIVED_UNTRUSTED,
        requires_approval=False,
        allowed_hosts=allowed_hosts,
        issued_at=anchor - timedelta(minutes=1),
        expires_at=anchor + timedelta(minutes=10),
    )


def _authority_binding(
    *,
    allowed_outbound_hosts: tuple[str, ...],
    now: datetime | None = None,
) -> ExecutionAuthorityBinding:
    anchor = now or datetime.now(UTC)
    worker = WorkerAssignment(
        worker_id="w-fetch",
        worker_type="literature_review",
        objective="Fetch paper full text",
        output_schema={},
        permission_scopes=("research:read",),
        tool_allowlist=(SOURCE_FETCH_TOOL,),
        allowed_outbound_hosts=allowed_outbound_hosts,
    )
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="auth-fetch",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id="wf-1",
        routing_policy_id="rp-1",
        strategy="direct",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="sup-1",
        supervisor_type="research",
        workers=(worker,),
        edges=(
            RoutingEdge(
                source_node_id="sup-1",
                target_node_id=worker.worker_id,
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="openrouter", model="openai/gpt-5"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("openrouter",),
            model_allowlist=("openai/gpt-5",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=Decimal("1.00"),
            max_total_tokens=10_000,
            max_tool_invocations=10,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=120,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=anchor + timedelta(minutes=10),
        compiled_at=anchor,
    )


class TestSSRFHardening:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.255.255.254",
            "::1",
            "169.254.169.254",
            "169.254.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.10",
            "fc00::1",
            "fd12:3456:789a::1",
            "fe80::1",
            "0.0.0.0",
            "::",
            "100.64.0.1",
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:10.0.0.1",
        ],
    )
    def test_disallowed_ips_are_rejected(self, ip: str) -> None:
        assert is_disallowed_ip(ip) is True

    @pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:2800:220:1::"])
    def test_public_global_ips_are_allowed(self, ip: str) -> None:
        assert is_disallowed_ip(ip) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "blocked_url",
        [
            "http://arxiv.org/pdf/2401.00001.pdf",
            "file:///etc/passwd",
            "ftp://arxiv.org/pub/paper.pdf",
            "https://127.0.0.1/secret",
            "https://[::1]/secret",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.1/internal",
            "https://user:pass@arxiv.org/pdf/2401.00001.pdf",
            "https://arxiv.org:8443/pdf/2401.00001.pdf",
            "https://evil.example/paper.pdf",
        ],
    )
    async def test_blocked_urls_are_refused_before_any_http_request(
        self, blocked_url: str
    ) -> None:
        captured: list[httpx.Request] = []
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (captured.append(req), httpx.Response(200, content=b"ok"))[
                    1
                ]
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        request = SourceFetchInput(
            source_uri=blocked_url, source_type=SourceType.ACADEMIC_PAPER
        )
        from src.core.tools.spec import ToolCallContext

        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda ref: "",
            allowed_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
        )

        with pytest.raises(SSRFBlockedError):
            await fetcher(request, ctx)
        assert captured == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "resolved_ips",
        [
            ["127.0.0.1"],
            ["::1"],
            ["169.254.169.254"],
            ["10.0.0.5"],
            ["192.168.1.1"],
            ["fc00::1"],
            ["::ffff:127.0.0.1"],
            ["::ffff:169.254.169.254"],
            [PUBLIC_IP, "127.0.0.1"],
            [],
        ],
    )
    async def test_dns_resolving_to_private_loopback_or_metadata_ip_is_denied_before_connect(
        self, resolved_ips: list[str]
    ) -> None:
        captured: list[httpx.Request] = []
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (captured.append(req), httpx.Response(200, content=b"ok"))[
                    1
                ]
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": resolved_ips}),
        )
        request = SourceFetchInput(
            source_uri="https://arxiv.org/pdf/2401.00001.pdf",
            source_type=SourceType.ACADEMIC_PAPER,
        )
        from src.core.tools.spec import ToolCallContext

        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda ref: "",
            allowed_hosts=("arxiv.org",),
        )

        with pytest.raises(SSRFBlockedError):
            await fetcher(request, ctx)
        assert captured == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "redirect_target",
        [
            "http://169.254.169.254/latest/meta-data/",
            "https://127.0.0.1/admin",
            "https://[::1]/admin",
            "https://evil.example/steal",
            "http://arxiv.org/pdf/downgraded.pdf",
            "https://export.arxiv.org/pdf/not-in-grant.pdf",
        ],
    )
    async def test_redirect_to_disallowed_or_private_target_is_blocked(
        self, redirect_target: str
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            if len(captured) == 1:
                return httpx.Response(302, headers={"Location": redirect_target})
            return httpx.Response(200, content=b"secret-metadata")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns(
                {"arxiv.org": [PUBLIC_IP], "export.arxiv.org": [PUBLIC_IP]}
            ),
        )
        request = SourceFetchInput(
            source_uri="https://arxiv.org/pdf/2401.00001",
            source_type=SourceType.ACADEMIC_PAPER,
        )
        from src.core.tools.spec import ToolCallContext

        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda ref: "",
            allowed_hosts=("arxiv.org",),
        )

        with pytest.raises(SSRFBlockedError):
            await fetcher(request, ctx)
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_redirect_to_allowlisted_host_resolving_to_loopback_is_blocked(
        self,
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            if req.url.host == "arxiv.org":
                return httpx.Response(
                    302,
                    headers={"Location": "https://export.arxiv.org/pdf/2401.00001.pdf"},
                )
            return httpx.Response(200, content=b"rebound")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns(
                {"arxiv.org": [PUBLIC_IP], "export.arxiv.org": ["127.0.0.1"]}
            ),
        )
        request = SourceFetchInput(
            source_uri="https://arxiv.org/pdf/2401.00001",
            source_type=SourceType.ACADEMIC_PAPER,
        )
        from src.core.tools.spec import ToolCallContext

        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda ref: "",
            allowed_hosts=("arxiv.org", "export.arxiv.org"),
        )

        with pytest.raises(SSRFBlockedError):
            await fetcher(request, ctx)
        assert [str(r.url) for r in captured] == ["https://arxiv.org/pdf/2401.00001"]

    @pytest.mark.asyncio
    async def test_redirect_loop_respects_max_redirects_and_allows_safe_redirect(
        self,
    ) -> None:
        hops: list[str] = []

        def infinite_handler(req: httpx.Request) -> httpx.Response:
            hops.append(str(req.url))
            return httpx.Response(
                302, headers={"Location": f"https://arxiv.org/hop/{len(hops)}"}
            )

        fetcher_loop = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(transport=httpx.MockTransport(infinite_handler)),
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
            max_redirects=2,
        )
        from src.core.tools.spec import ToolCallContext

        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda ref: "",
            allowed_hosts=("arxiv.org",),
        )
        with pytest.raises(SSRFBlockedError, match="redirect"):
            await fetcher_loop(
                SourceFetchInput(
                    source_uri="https://arxiv.org/start",
                    source_type=SourceType.ACADEMIC_PAPER,
                ),
                ctx,
            )
        assert len(hops) == 3

        def safe_redirect_handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/abs/2401.00001":
                return httpx.Response(302, headers={"Location": "/pdf/2401.00001.pdf"})
            return httpx.Response(
                200,
                content=b"plain text paper",
                headers={"content-type": "text/plain"},
            )

        fetcher_ok = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(safe_redirect_handler)
            ),
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
            max_redirects=3,
        )
        fetched = await fetcher_ok(
            SourceFetchInput(
                source_uri="https://arxiv.org/abs/2401.00001",
                source_type=SourceType.ACADEMIC_PAPER,
            ),
            ctx,
        )
        assert fetched.final_uri == "https://arxiv.org/pdf/2401.00001.pdf"
        assert fetched.content == b"plain text paper"


class TestSourceFetchBoundaryAndPdfFullText:
    def test_source_fetch_declared_in_outbound_and_mcp_specs(self) -> None:
        assert SOURCE_FETCH_TOOL in OUTBOUND_TOOL_HOSTS
        assert (
            MCP_TOOL_SENSITIVITIES[TOOL_SOURCE_FETCH] is SensitivityClass.EXTERNAL_READ
        )
        assert MCP_TOOL_OUTBOUND_HOSTS[TOOL_SOURCE_FETCH] == SOURCE_FETCH_OUTBOUND_HOSTS

    @pytest.mark.asyncio
    async def test_authority_host_allowlist_denies_unapproved_host_at_boundary(
        self, tmp_path: Path
    ) -> None:
        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        captured: list[httpx.Request] = []
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: (
                        captured.append(req),
                        httpx.Response(200, content=b"ok"),
                    )[1]
                )
            ),
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        binding = _authority_binding(allowed_outbound_hosts=("export.arxiv.org",))
        grants = PlanCapabilityIssuer.issue(
            binding,
            run_id="run-1",
            task_id="task-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        audit = InMemoryToolAuditStore()
        integration = MCPIntegration(
            enable_fallback=False,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            audit_store=audit,
            grants=grants,
            snapshot_store=store,
            source_fetcher=fetcher,
        )

        result = await integration.fetch_source(
            source_uri="https://arxiv.org/pdf/2401.00001.pdf",
            source_type=SourceType.ACADEMIC_PAPER,
        )

        assert result["success"] is False
        assert result["tool_outcome"] == ToolOutcomeStatus.DENIED.value
        assert captured == []
        assert audit.decisions[-1] is not None
        assert (
            audit.decisions[-1].denial_reason is CapabilityDenialReason.HOST_NOT_ALLOWED
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response_content_type",
        [
            "application/pdf",
            "application/pdf; charset=binary",
            "application/octet-stream",
        ],
    )
    async def test_pdf_fetch_extracts_full_text_preserves_digest_and_locator(
        self,
        tmp_path: Path,
        response_content_type: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import document_ingestion

        sandbox_calls: list[bytes] = []

        async def spy_sandbox(data: bytes) -> tuple[ExtractedText, int]:
            sandbox_calls.append(data)
            return await _bounded_pdf_extractor(data)

        monkeypatch.setattr(document_ingestion, "extract_pdf_sandboxed", spy_sandbox)

        pdf_bytes = _make_pdf(
            [
                "Quantum entanglement results on page one.",
                "Experimental table on page two.",
            ]
        )
        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    content=pdf_bytes,
                    headers={"content-type": response_content_type},
                )
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        audit = InMemoryToolAuditStore()
        boundary = build_tool_boundary(audit_store=audit)
        boundary.register(
            hardened_source_fetch_tool(
                store=store,
                fetcher=fetcher,
                timeout_seconds=30.0,
            )
        )
        grant = _grant(allowed_hosts=("arxiv.org",))

        outcome = await boundary.invoke(
            tool_name=SOURCE_FETCH_TOOL,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={
                "source_uri": "https://arxiv.org/pdf/2401.00001.pdf",
                "source_type": SourceType.ACADEMIC_PAPER.value,
            },
            input_trust=TrustClassification.USER_SUPPLIED,
            output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            grants=(grant,),
        )

        assert outcome.succeeded, outcome.detail
        assert sandbox_calls == [pdf_bytes]
        handle = plain(outcome.unwrap())
        assert handle["content_sha256"] == snapshot_digest(pdf_bytes)
        assert handle["content_kind"] == "full_text"
        assert handle["extracted_text_sha256"] is not None
        assert handle["extracted_text_storage_uri"] is not None
        assert handle["extracted_text_locator"] is not None
        assert handle["extracted_page_count"] == 2
        assert store.get(handle["storage_uri"]) == pdf_bytes

        extracted_bytes = store.get(handle["extracted_text_storage_uri"])
        assert snapshot_digest(extracted_bytes) == handle["extracted_text_sha256"]
        resolved = resolve(handle["extracted_text_locator"], extracted_bytes)
        assert resolved == extracted_bytes
        decoded = resolved.decode("utf-8")
        assert "Quantum entanglement results on page one." in decoded
        assert "Experimental table on page two." in decoded

        # Handle survives redaction and raw text never enters the boundary record.
        assert redact(handle) == handle
        assert "Quantum entanglement" not in outcome.invocation.model_dump_json()

    @pytest.mark.asyncio
    async def test_acquisition_service_wires_extracted_text_artifact_for_pdf(
        self, tmp_path: Path
    ) -> None:
        pdf_bytes = _make_pdf(["Full text evidence from fetched PDF."])
        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    content=pdf_bytes,
                    headers={"content-type": "application/pdf"},
                )
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        registry = PromptRegistry()
        registry.register(
            PromptTemplate(
                prompt_id="literature.acquire",
                version="1.0.0",
                source="Acquire $topic.",
            )
        )
        prompt = PromptRenderer(
            registry=registry, secret_provider=NullSecretProvider()
        ).render(
            prompt_id="literature.acquire",
            version="1.0.0",
            variables={"topic": "physics"},
        )
        boundary = ToolBoundary(
            secret_provider=NullSecretProvider(),
            audit_store=FakeAuditStore(),
            event_publisher=NullEventPublisher(),
            clock=lambda: NOW,
            prompt_verifier=PromptIdentityVerifier(registry=registry),
        )
        boundary.register(
            hardened_source_fetch_tool(
                store=store,
                fetcher=fetcher,
                timeout_seconds=30.0,
                pdf_extractor=_bounded_pdf_extractor,
                clock=lambda: NOW,
            )
        )
        artifacts = FakeArtifactRepository()
        evidence = FakeEvidenceRepository()
        service = SourceAcquisitionService(
            boundary=boundary,
            store=store,
            artifacts=artifacts,  # type: ignore[arg-type]
            evidence=evidence,  # type: ignore[arg-type]
            secret_provider=NullSecretProvider(),
            clock=lambda: NOW,
            source_types={SOURCE_FETCH_TOOL: SourceType.ACADEMIC_PAPER},
        )
        grant = _grant(allowed_hosts=("arxiv.org",), now=NOW)

        outcome = await service.acquire(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            source_uri="https://arxiv.org/pdf/2401.00001.pdf",
            capability_scope=grant.capability_scope,
            grants=(grant,),
            prompt=prompt,
            excerpts=[ExcerptRequest("bytes", 0, len(pdf_bytes))],
        )

        assert outcome.status is AcquisitionStatus.ACQUIRED
        assert outcome.artifact is not None
        assert outcome.artifact.kind == SNAPSHOT_ARTIFACT_KIND
        assert outcome.artifact.metadata["content_kind"] == "full_text"
        assert outcome.extracted_text_artifact is not None
        assert outcome.extracted_text_artifact.kind == "extracted_text"
        assert outcome.extracted_text_artifact.metadata["content_kind"] == "full_text"
        assert outcome.extracted_text_artifact.metadata[
            "source_content_sha256"
        ] == snapshot_digest(pdf_bytes)
        extracted_locator = outcome.extracted_text_artifact.metadata["locator"]
        assert isinstance(extracted_locator, str)
        extracted_bytes = store.get(outcome.extracted_text_artifact.storage_uri)
        assert (
            resolve(extracted_locator, extracted_bytes).decode("utf-8")
            == "Full text evidence from fetched PDF."
        )
        assert len(artifacts.created) == 2
        assert {a.kind for a in artifacts.created} == {
            SNAPSHOT_ARTIFACT_KIND,
            "extracted_text",
        }
