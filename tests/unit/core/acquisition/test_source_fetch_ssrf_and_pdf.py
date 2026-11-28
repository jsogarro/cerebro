"""SSRF hardening and PDF full-text extraction for ``source.fetch``."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpcore
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
    AbsentEvidenceReason,
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


class _CountingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_yielded += 1
            yield chunk


class _RecordingNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        *,
        response_bytes: bytes,
        sni_hostnames: list[str | None],
        written_chunks: list[bytes],
    ) -> None:
        self._response = response_bytes
        self._offset = 0
        self._sni_hostnames = sni_hostnames
        self._written_chunks = written_chunks

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._offset >= len(self._response):
            return b""
        chunk = self._response[self._offset : self._offset + max_bytes]
        self._offset += len(chunk)
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._written_chunks.append(bytes(buffer))

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._sni_hostnames.append(server_hostname)
        return self


class _RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, response_bytes: bytes) -> None:
        self._response_bytes = response_bytes
        self.connected_targets: list[tuple[str, int]] = []
        self.sni_hostnames: list[str | None] = []
        self.written_chunks: list[bytes] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected_targets.append((host, port))
        return _RecordingNetworkStream(
            response_bytes=self._response_bytes,
            sni_hostnames=self.sni_hostnames,
            written_chunks=self.written_chunks,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("unix socket connect must never be called")

    async def sleep(self, seconds: float) -> None:
        return None


class TestReviewHardeningRegressions:
    @pytest.mark.asyncio
    async def test_fetcher_pins_validated_ip_and_preserves_tls_sni_and_host_header(
        self,
    ) -> None:
        from src.core.tools.spec import ToolCallContext

        dns_calls: list[tuple[str, int]] = []

        async def rebinding_dns(host: str, port: int) -> list[str]:
            dns_calls.append((host, port))
            if len(dns_calls) == 1:
                return [PUBLIC_IP]
            return ["127.0.0.1"]

        raw_http = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 14\r\n"
            b"\r\n"
            b"pinned-ip-body"
        )
        backend = _RecordingNetworkBackend(raw_http)
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            dns_resolver=rebinding_dns,
            network_backend=backend,
        )
        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda _ref: "",
            allowed_hosts=("arxiv.org",),
        )

        fetched = await fetcher(
            SourceFetchInput(
                source_uri="https://arxiv.org/abs/2401.00001",
                source_type=SourceType.ACADEMIC_PAPER,
            ),
            ctx,
        )

        assert fetched.content == b"pinned-ip-body"
        assert dns_calls == [("arxiv.org", 443)]
        assert backend.connected_targets == [(PUBLIC_IP, 443)]
        assert backend.sni_hostnames == ["arxiv.org"]
        sent_wire = b"".join(backend.written_chunks).lower()
        assert b"host: arxiv.org\r\n" in sent_wire

    @pytest.mark.asyncio
    async def test_streaming_rejects_oversized_content_length_without_reading_body(
        self,
    ) -> None:
        from src.core.tools.spec import ToolCallContext

        stream = _CountingAsyncStream([b"a" * 200, b"b" * 200])
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    headers={"content-length": "400", "content-type": "text/plain"},
                    stream=stream,
                )
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            max_bytes=100,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda _ref: "",
            allowed_hosts=("arxiv.org",),
        )

        with pytest.raises(ValueError, match="byte limit"):
            await fetcher(
                SourceFetchInput(
                    source_uri="https://arxiv.org/abs/2401.00001",
                    source_type=SourceType.ACADEMIC_PAPER,
                ),
                ctx,
            )
        assert stream.chunks_yielded == 0

    @pytest.mark.asyncio
    async def test_streaming_aborts_chunked_response_as_soon_as_cap_exceeded(
        self,
    ) -> None:
        from src.core.tools.spec import ToolCallContext

        stream = _CountingAsyncStream([b"x" * 100 for _ in range(10)])
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    headers={"content-type": "text/plain"},
                    stream=stream,
                )
            )
        )
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            max_bytes=250,
            client=client,
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        ctx = ToolCallContext(
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            tool_invocation_id="inv-1",
            resolve_secret=lambda _ref: "",
            allowed_hosts=("arxiv.org",),
        )

        with pytest.raises(ValueError, match="byte limit"):
            await fetcher(
                SourceFetchInput(
                    source_uri="https://arxiv.org/abs/2401.00001",
                    source_type=SourceType.ACADEMIC_PAPER,
                ),
                ctx,
            )
        assert stream.chunks_yielded == 3

    @pytest.mark.asyncio
    async def test_boundary_allows_cross_host_redirect_within_grant_and_blocks_disallowed_hop(
        self, tmp_path: Path
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            if req.url.host == "doi.org" and req.url.path == "/10.48550/allowed":
                return httpx.Response(
                    302,
                    headers={"Location": "https://arxiv.org/pdf/2401.00001.pdf"},
                )
            if req.url.host == "doi.org" and req.url.path == "/10.48550/disallowed":
                return httpx.Response(
                    302,
                    headers={"Location": "https://export.arxiv.org/pdf/2401.00001.pdf"},
                )
            return httpx.Response(
                200,
                content=b"resolved paper text",
                headers={"content-type": "text/plain"},
            )

        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            dns_resolver=_static_dns(
                {
                    "doi.org": [PUBLIC_IP],
                    "arxiv.org": [PUBLIC_IP],
                    "export.arxiv.org": [PUBLIC_IP],
                }
            ),
        )
        boundary = build_tool_boundary(audit_store=InMemoryToolAuditStore())
        boundary.register(
            hardened_source_fetch_tool(
                store=store,
                fetcher=fetcher,
                timeout_seconds=30.0,
            )
        )
        grant = _grant(allowed_hosts=("doi.org", "arxiv.org"))

        allowed_outcome = await boundary.invoke(
            tool_name=SOURCE_FETCH_TOOL,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={
                "source_uri": "https://doi.org/10.48550/allowed",
                "source_type": SourceType.ACADEMIC_PAPER.value,
            },
            input_trust=TrustClassification.USER_SUPPLIED,
            output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            grants=(grant,),
        )
        assert allowed_outcome.succeeded, allowed_outcome.detail
        assert (
            plain(allowed_outcome.unwrap())["final_uri"]
            == "https://arxiv.org/pdf/2401.00001.pdf"
        )
        assert [r.url.host for r in captured] == ["doi.org", "arxiv.org"]

        captured.clear()
        disallowed_outcome = await boundary.invoke(
            tool_name=SOURCE_FETCH_TOOL,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={
                "source_uri": "https://doi.org/10.48550/disallowed",
                "source_type": SourceType.ACADEMIC_PAPER.value,
            },
            input_trust=TrustClassification.USER_SUPPLIED,
            output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            grants=(grant,),
        )
        assert disallowed_outcome.status is ToolOutcomeStatus.FAILED
        assert disallowed_outcome.detail is not None
        assert "export.arxiv.org" in disallowed_outcome.detail
        assert [r.url.host for r in captured] == ["doi.org"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content_type", "body"),
        [
            ("text/html", b"<html><body>PDFs begin with %PDF-1.7.</body></html>"),
            ("text/html; charset=utf-8", b"%PDF-1.7 is mentioned at offset zero"),
            ("text/plain", b"%PDF-1.7 magic header documentation"),
            ("application/json", b'{"magic": "%PDF-1.7"}'),
            ("application/octet-stream", b"prefix before %PDF-1.7"),
        ],
    )
    async def test_pdf_sniffing_never_misclassifies_text_html_or_mid_body_magic(
        self,
        tmp_path: Path,
        content_type: str,
        body: bytes,
    ) -> None:
        extractor_calls: list[bytes] = []

        async def spy_extractor(data: bytes) -> tuple[ExtractedText, int]:
            extractor_calls.append(data)
            return await _bounded_pdf_extractor(data)

        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _req: httpx.Response(
                        200,
                        content=body,
                        headers={"content-type": content_type},
                    )
                )
            ),
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        boundary = build_tool_boundary(audit_store=InMemoryToolAuditStore())
        boundary.register(
            hardened_source_fetch_tool(
                store=store,
                fetcher=fetcher,
                timeout_seconds=30.0,
                pdf_extractor=spy_extractor,
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
                "source_uri": "https://arxiv.org/abs/2401.00001",
                "source_type": SourceType.ACADEMIC_PAPER.value,
            },
            input_trust=TrustClassification.USER_SUPPLIED,
            output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            grants=(grant,),
        )

        assert outcome.succeeded, outcome.detail
        assert extractor_calls == []
        handle = plain(outcome.unwrap())
        assert handle["extracted_text_sha256"] is None
        assert handle["content_kind"] is None

    @pytest.mark.asyncio
    async def test_pdf_sniffing_still_detects_pdf_at_offset_zero_when_content_type_absent(
        self, tmp_path: Path
    ) -> None:
        pdf_bytes = _make_pdf(["Absent content type PDF page."])
        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        fetcher = HardenedHttpFetcher(
            declared_hosts=SOURCE_FETCH_OUTBOUND_HOSTS,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _req: httpx.Response(200, content=pdf_bytes)
                )
            ),
            dns_resolver=_static_dns({"arxiv.org": [PUBLIC_IP]}),
        )
        boundary = build_tool_boundary(audit_store=InMemoryToolAuditStore())
        boundary.register(
            hardened_source_fetch_tool(
                store=store,
                fetcher=fetcher,
                timeout_seconds=30.0,
                pdf_extractor=_bounded_pdf_extractor,
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
        handle = plain(outcome.unwrap())
        assert handle["content_kind"] == "full_text"
        assert handle["extracted_text_sha256"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("extracted_payload", "locator", "pages"),
        [
            (b"valid text", "bytes:0-9999", [{"page": 1, "start": 0, "end": 10}]),
            (
                b"valid text",
                "not-a-valid-locator",
                [{"page": 1, "start": 0, "end": 10}],
            ),
            (b"\xff\xfe\x80", "bytes:0-3", [{"page": 1, "start": 0, "end": 1}]),
            (b"valid text", "bytes:0-10", [{"page": 1, "start": 0, "end": 99}]),
            (b"valid text", "bytes:0-10", [{"page": 1, "start": 5, "end": 2}]),
            (b"valid text", "bytes:0-10", [{"page": 0, "start": 0, "end": 5}]),
            (b"valid text", "bytes:0-10", [{"page": 1, "start": 0}]),
        ],
    )
    async def test_acquisition_service_guards_extracted_text_locator_decode_and_pages(
        self,
        tmp_path: Path,
        extracted_payload: bytes,
        locator: str,
        pages: list[dict[str, int]],
    ) -> None:
        from src.core.acquisition.fetch_tool import SourceFetchOutput
        from src.core.acquisition.sources import LicenseDeclaration
        from src.core.tools.spec import ToolSpec

        store = FilesystemSnapshotStore(root=tmp_path / "snaps")
        raw_pdf = b"%PDF-1.7 raw bytes"
        raw_uri = store.put(raw_pdf)
        extracted_uri = store.put(extracted_payload)

        async def tampered_handler(_payload: Any, _ctx: Any) -> dict[str, Any]:
            return {
                "content_sha256": snapshot_digest(raw_pdf),
                "storage_uri": raw_uri,
                "media_type": "application/pdf",
                "byte_length": len(raw_pdf),
                "final_uri": "https://arxiv.org/pdf/2401.00001.pdf",
                "license_identifier": None,
                "license_declared_by": LicenseDeclaration.UNDECLARED.value,
                "license_statement": None,
                "content_kind": "full_text",
                "extracted_text_sha256": snapshot_digest(extracted_payload),
                "extracted_text_storage_uri": extracted_uri,
                "extracted_text_locator": locator,
                "extracted_text_byte_length": len(extracted_payload),
                "extracted_page_count": len(pages),
                "extracted_pages": pages,
            }

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
            ToolSpec(
                name=SOURCE_FETCH_TOOL,
                version="1.0.0",
                sensitivity=SensitivityClass.EXTERNAL_READ,
                input_model=SourceFetchInput,
                output_model=SourceFetchOutput,
                timeout_seconds=30.0,
                handler=tampered_handler,
                outbound_hosts=("arxiv.org",),
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
            excerpts=[ExcerptRequest("bytes", 0, len(raw_pdf))],
        )

        assert outcome.status is AcquisitionStatus.UNAVAILABLE
        assert (
            outcome.absent_evidence_reason is AbsentEvidenceReason.SOURCE_INACCESSIBLE
        )
        assert artifacts.created == []
        assert evidence.rows == []
