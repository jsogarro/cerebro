"""Phase 2a (Issue #344): outbound sensitivity and per-authority host allowlists.

Verifies that:
1. Outbound research tools (``AcademicSearchTool``, ``CitationTool``) declare
   ``SensitivityClass.EXTERNAL_READ`` and their pinned outbound hosts, while
   local compute tools (``StatisticsTool``, ``KnowledgeGraphTool``) remain
   ``SensitivityClass.READ_ONLY`` with no outbound hosts.
2. A canary string passed as a search query (or DOI) reaches ZERO hosts the
   admitted execution authority has not pre-approved.
3. Adding an undeclared outbound host to ``AcademicSearchTool`` or
   ``CitationTool`` fails both static/runtime drift checks and fails closed
   before hitting the HTTP transport.
4. Grants issued by ``PlanCapabilityIssuer`` enforce per-authority host
   allowlists and survive ORM/migration schema checks.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    TOOL_ACADEMIC_SEARCH,
    TOOL_ANALYZE_STATISTICS,
    TOOL_BUILD_KNOWLEDGE_GRAPH,
    TOOL_FORMAT_CITATIONS,
    TOOL_VERSION,
    ResearchToolSet,
)
from src.agents.tools.mediation import InMemoryToolAuditStore, ToolCallIdentity
from src.core.capabilities.issuer import PlanCapabilityIssuer
from src.core.contracts import (
    CapabilityDenialReason,
    CapabilityGrant,
    CapabilityRequest,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    SensitivityClass,
    TrustClassification,
    WorkerAssignment,
)
from src.core.contracts.capabilities import (
    CapabilityDecisionEffect,
    decide_capability,
)
from src.core.tools import ToolSpec, ToolSpecError
from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.mcp.tools.citation_tool import CitationTool
from src.models.db.base import Base
from src.models.db.capability import AgentCapabilityGrant
from src.models.db.run_lifecycle import AgentRun, AgentRunTask
from src.models.execution_authority import ExecutionAuthorityBinding

REPO_ROOT = Path(__file__).resolve().parents[3]
CANARY = "CANARY-SECRET-EXFIL-7f3a9c"
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001</id>
    <title>Mock ArXiv Paper</title>
    <summary>Abstract</summary>
    <published>2024-01-10T00:00:00Z</published>
    <author><name>Alice Researcher</name></author>
  </entry>
</feed>"""


def _mock_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        host = request.url.host
        if host == "export.arxiv.org":
            return httpx.Response(200, text=ARXIV_XML)
        if host == "eutils.ncbi.nlm.nih.gov":
            if "esearch.fcgi" in request.url.path:
                return httpx.Response(
                    200, json={"esearchresult": {"idlist": ["12345"]}}
                )
            return httpx.Response(
                200,
                json={
                    "result": {
                        "12345": {
                            "title": "Mock PubMed Paper",
                            "authors": [{"name": "Bob Med"}],
                            "pubdate": "2024 Jan",
                            "source": "J Med",
                            "articleids": [],
                        }
                    }
                },
            )
        if host == "api.semanticscholar.org":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "paperId": "p1",
                            "title": "Mock S2 Paper",
                            "abstract": "Abs",
                            "authors": [{"name": "Carol S2"}],
                            "year": 2024,
                            "venue": "NeurIPS",
                            "citationCount": 5,
                            "url": "https://www.semanticscholar.org/paper/p1",
                            "externalIds": {},
                        }
                    ]
                },
            )
        if host == "api.crossref.org":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Mock Crossref Paper"],
                        "author": [{"family": "Doe", "given": "Jane"}],
                        "published-print": {"date-parts": [[2024]]},
                        "container-title": ["Journal of Testing"],
                    }
                },
            )
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


def _authority_binding(
    *,
    tools: tuple[str, ...],
    allowed_outbound_hosts: tuple[str, ...] = (),
) -> ExecutionAuthorityBinding:
    worker_kwargs: dict[str, Any] = {
        "worker_id": "worker-research",
        "worker_type": "literature_review",
        "objective": "Search literature under authority constraints",
        "output_schema": {},
        "permission_scopes": ("research:read",),
        "tool_allowlist": tools,
    }
    if allowed_outbound_hosts:
        worker_kwargs["allowed_outbound_hosts"] = allowed_outbound_hosts
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="auth-host-test",
        authority_version="1",
        run_id="run-host-1",
        workflow_definition_id="wf-1",
        routing_policy_id="rp-1",
        strategy="direct",
        collaboration_mode="direct",
        domains=("research",),
        supervisor_id=None,
        supervisor_type=None,
        workers=(WorkerAssignment(**worker_kwargs),),
        edges=(),
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
        deadline=NOW + timedelta(minutes=10),
        compiled_at=NOW,
    )


def _durable_identity() -> ToolCallIdentity:
    return ToolCallIdentity(
        run_id="run-host-1",
        task_id="task-host-1",
        attempt_id="attempt-host-1",
        organization_id="00000000-0000-0000-0000-000000000001",
    )


class TestCanaryNeverReachesUnapprovedHost:
    @pytest.mark.asyncio
    async def test_academic_search_canary_blocked_when_authority_approves_no_hosts(
        self,
    ) -> None:
        """Plan 2a red test: canary query must never hit an unapproved host."""
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        binding = _authority_binding(tools=(TOOL_ACADEMIC_SEARCH,))
        grants = PlanCapabilityIssuer.issue(
            binding,
            run_id="run-host-1",
            task_id="task-host-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        store = InMemoryToolAuditStore()
        integration = MCPIntegration(
            mcp_client=ResearchToolSet(academic_search=tool),
            enable_fallback=False,
            identity=_durable_identity(),
            audit_store=store,
            grants=grants,
        )

        result = await integration.search_academic_sources(
            query=CANARY,
            databases=["arxiv", "pubmed", "semantic_scholar"],
        )

        assert result["success"] is False
        assert result["tool_outcome"] == "denied"
        assert captured == [], (
            f"canary reached unapproved host(s): {[str(r.url) for r in captured]}"
        )
        assert not any(CANARY in str(r.url) for r in captured)
        assert store.decisions[-1] is not None
        assert (
            store.decisions[-1].denial_reason is CapabilityDenialReason.HOST_NOT_ALLOWED
        )

    @pytest.mark.asyncio
    async def test_academic_search_allows_approved_host_and_denies_unapproved_sibling(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        binding = _authority_binding(
            tools=(TOOL_ACADEMIC_SEARCH,),
            allowed_outbound_hosts=("export.arxiv.org",),
        )
        grants = PlanCapabilityIssuer.issue(
            binding,
            run_id="run-host-1",
            task_id="task-host-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        store = InMemoryToolAuditStore()
        integration = MCPIntegration(
            mcp_client=ResearchToolSet(academic_search=tool),
            enable_fallback=False,
            identity=_durable_identity(),
            audit_store=store,
            grants=grants,
        )

        allowed = await integration.search_academic_sources(
            query="graph neural networks",
            databases=["arxiv"],
        )
        assert allowed["success"] is True
        assert [r.url.host for r in captured] == ["export.arxiv.org"]

        captured.clear()
        denied_single = await integration.search_academic_sources(
            query=CANARY,
            databases=["pubmed"],
        )
        assert denied_single["success"] is False
        assert denied_single["tool_outcome"] == "denied"
        assert captured == []
        assert store.decisions[-1] is not None
        assert (
            store.decisions[-1].denial_reason is CapabilityDenialReason.HOST_NOT_ALLOWED
        )

        denied_mixed = await integration.search_academic_sources(
            query=CANARY,
            databases=["arxiv", "semantic_scholar"],
        )
        assert denied_mixed["success"] is False
        assert denied_mixed["tool_outcome"] == "denied"
        assert captured == []

    @pytest.mark.asyncio
    async def test_citation_tool_host_allowlist_enforced_at_boundary_and_tool(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        citation_tool = CitationTool()
        citation_tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        # 1. Through ToolBoundary without api.crossref.org approved -> denied
        binding_unapproved = _authority_binding(tools=(TOOL_FORMAT_CITATIONS,))
        grants_unapproved = PlanCapabilityIssuer.issue(
            binding_unapproved,
            run_id="run-host-1",
            task_id="task-host-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        store = InMemoryToolAuditStore()
        integration_denied = MCPIntegration(
            mcp_client=ResearchToolSet(citation=citation_tool),
            enable_fallback=False,
            identity=_durable_identity(),
            audit_store=store,
            grants=grants_unapproved,
        )
        denied = await integration_denied.format_citations(
            sources=[{"title": "Paper", "authors": ["Doe, J."], "year": 2024}],
            style="APA",
        )
        assert denied["success"] is False
        assert denied["tool_outcome"] == "denied"
        assert store.decisions[-1] is not None
        assert (
            store.decisions[-1].denial_reason is CapabilityDenialReason.HOST_NOT_ALLOWED
        )

        # 2. Through ToolBoundary with api.crossref.org approved -> succeeds
        binding_approved = _authority_binding(
            tools=(TOOL_FORMAT_CITATIONS,),
            allowed_outbound_hosts=("api.crossref.org",),
        )
        grants_approved = PlanCapabilityIssuer.issue(
            binding_approved,
            run_id="run-host-1",
            task_id="task-host-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        integration_ok = MCPIntegration(
            mcp_client=ResearchToolSet(citation=citation_tool),
            enable_fallback=False,
            identity=_durable_identity(),
            grants=grants_approved,
        )
        ok = await integration_ok.format_citations(
            sources=[{"title": "Paper", "authors": ["Doe, J."], "year": 2024}],
            style="APA",
        )
        assert ok["success"] is True

        # 3. Direct DOI resolution with canary refuses unapproved host before HTTP
        with pytest.raises(PermissionError, match=r"api\.crossref\.org"):
            await citation_tool.execute(
                doi=f"10.1234/{CANARY}",
                allowed_hosts=(),
            )
        assert captured == []

        # 4. Direct DOI resolution with api.crossref.org approved hits transport
        resolved = await citation_tool.execute(
            doi="10.1234/allowed",
            allowed_hosts=("api.crossref.org",),
        )
        assert resolved["success"] is True
        assert [r.url.host for r in captured] == ["api.crossref.org"]


class TestPinnedOutboundHostDeclarations:
    @pytest.mark.asyncio
    async def test_academic_search_declared_hosts_match_runtime_requests(self) -> None:
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        result = await tool.execute(
            query="transformer",
            databases=["pubmed", "arxiv", "semantic_scholar"],
            allowed_hosts=AcademicSearchTool.OUTBOUND_HOSTS,
        )

        assert result["success"] is True
        contacted = {req.url.host for req in captured}
        assert contacted == set(AcademicSearchTool.OUTBOUND_HOSTS)
        assert contacted == {
            "eutils.ncbi.nlm.nih.gov",
            "export.arxiv.org",
            "api.semanticscholar.org",
        }

    @pytest.mark.asyncio
    async def test_citation_tool_declared_hosts_match_runtime_requests(self) -> None:
        captured: list[httpx.Request] = []
        tool = CitationTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        result = await tool.execute(
            doi="10.1000/test",
            allowed_hosts=CitationTool.OUTBOUND_HOSTS,
        )

        assert result["success"] is True
        contacted = {req.url.host for req in captured}
        assert contacted == set(CitationTool.OUTBOUND_HOSTS)
        assert contacted == {"api.crossref.org"}

    def test_academic_search_base_attributes_only_use_declared_hosts(self) -> None:
        tool = AcademicSearchTool()
        base_hosts = {
            urlparse(value).hostname
            for name, value in vars(tool).items()
            if isinstance(value, str) and value.startswith(("http://", "https://"))
        }
        assert base_hosts == set(AcademicSearchTool.OUTBOUND_HOSTS)

    def test_citation_tool_base_attributes_only_use_declared_hosts(self) -> None:
        tool = CitationTool()
        base_hosts = {
            urlparse(value).hostname
            for name, value in vars(tool).items()
            if isinstance(value, str) and value.startswith(("http://", "https://"))
        }
        assert base_hosts == set(CitationTool.OUTBOUND_HOSTS)

    @pytest.mark.asyncio
    async def test_undeclared_host_on_academic_search_fails_closed_before_transport(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))
        tool.arxiv_base = "https://undeclared.attacker.example/api/query"

        with pytest.raises(PermissionError, match=r"undeclared\.attacker\.example"):
            await tool.execute(
                query=CANARY,
                databases=["arxiv"],
                allowed_hosts=("undeclared.attacker.example",),
            )
        assert captured == []

    @pytest.mark.asyncio
    async def test_undeclared_host_on_citation_tool_fails_closed_before_transport(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        tool = CitationTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))
        tool.crossref_base = "https://undeclared.attacker.example/works"

        with pytest.raises(PermissionError, match=r"undeclared\.attacker\.example"):
            await tool.execute(
                doi=f"10.1234/{CANARY}",
                allowed_hosts=("undeclared.attacker.example",),
            )
        assert captured == []


class TestSensitivityClassificationAndDocstringTruthfulness:
    def test_mcp_specs_classify_outbound_vs_local_tools_accurately(self) -> None:
        integration = MCPIntegration(enable_fallback=False)

        search_spec = integration.boundary.specification(TOOL_ACADEMIC_SEARCH)
        citation_spec = integration.boundary.specification(TOOL_FORMAT_CITATIONS)
        stats_spec = integration.boundary.specification(TOOL_ANALYZE_STATISTICS)
        kg_spec = integration.boundary.specification(TOOL_BUILD_KNOWLEDGE_GRAPH)

        assert search_spec.sensitivity is SensitivityClass.EXTERNAL_READ
        assert set(search_spec.outbound_hosts) == {
            "eutils.ncbi.nlm.nih.gov",
            "export.arxiv.org",
            "api.semanticscholar.org",
        }

        assert citation_spec.sensitivity is SensitivityClass.EXTERNAL_READ
        assert set(citation_spec.outbound_hosts) == {"api.crossref.org"}

        assert stats_spec.sensitivity is SensitivityClass.READ_ONLY
        assert stats_spec.outbound_hosts == ()

        assert kg_spec.sensitivity is SensitivityClass.READ_ONLY
        assert kg_spec.outbound_hosts == ()

    def test_mcp_tool_specs_module_has_no_false_no_outbound_claim(self) -> None:
        source = (
            REPO_ROOT / "src" / "agents" / "integrations" / "mcp_tool_specs.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        all_strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        joined = "\n".join(all_strings).lower()
        assert (
            "none of them writes to a third-party system or sends run content"
            not in joined
        )

    def test_tool_spec_rejects_inconsistent_sensitivity_and_outbound_hosts(
        self,
    ) -> None:
        class _In(BaseModel):
            model_config = ConfigDict(extra="forbid")
            q: str

        class _Out(BaseModel):
            ok: bool

        async def _noop(params: Any, ctx: Any) -> dict[str, bool]:
            return {"ok": True}

        with pytest.raises(ToolSpecError):
            ToolSpec(
                name="bad.read_only_with_host",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=_In,
                output_model=_Out,
                timeout_seconds=5.0,
                handler=_noop,
                outbound_hosts=("export.arxiv.org",),
            )

        with pytest.raises(ToolSpecError):
            ToolSpec(
                name="bad.external_read_without_hosts",
                version="1.0.0",
                sensitivity=SensitivityClass.EXTERNAL_READ,
                input_model=_In,
                output_model=_Out,
                timeout_seconds=5.0,
                handler=_noop,
                outbound_hosts=(),
            )

    @pytest.mark.asyncio
    async def test_read_only_grant_cannot_authorize_outbound_academic_search(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))
        read_only_grant = CapabilityGrant(
            grant_id="grant-ro",
            run_id="run-host-1",
            task_id="task-host-1",
            capability_scope="plan-issued:mcp.academic_search",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_versions=(TOOL_VERSION,),
            sensitivity=SensitivityClass.READ_ONLY,
            max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            requires_approval=False,
            issued_at=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        store = InMemoryToolAuditStore()
        integration = MCPIntegration(
            mcp_client=ResearchToolSet(academic_search=tool),
            enable_fallback=False,
            identity=_durable_identity(),
            audit_store=store,
            grants=(read_only_grant,),
        )

        result = await integration.search_academic_sources(
            query=CANARY, databases=["arxiv"]
        )

        assert result["success"] is False
        assert result["tool_outcome"] == "denied"
        assert captured == []
        assert store.decisions[-1] is not None
        assert (
            store.decisions[-1].denial_reason
            is CapabilityDenialReason.SENSITIVITY_MISMATCH
        )


class TestCapabilityContractAndIssuerHostEnforcement:
    def test_issuer_sets_external_read_and_intersects_authority_hosts(self) -> None:
        binding = _authority_binding(
            tools=(
                TOOL_ACADEMIC_SEARCH,
                TOOL_FORMAT_CITATIONS,
                TOOL_ANALYZE_STATISTICS,
                TOOL_BUILD_KNOWLEDGE_GRAPH,
                "arithmetic",
            ),
            allowed_outbound_hosts=("export.arxiv.org", "unrecognized.example"),
        )

        grants = {
            g.tool_name: g
            for g in PlanCapabilityIssuer.issue(
                binding, run_id="run-host-1", task_id="task-host-1", issued_at=NOW
            )
        }

        assert (
            grants[TOOL_ACADEMIC_SEARCH].sensitivity is SensitivityClass.EXTERNAL_READ
        )
        assert grants[TOOL_ACADEMIC_SEARCH].allowed_hosts == ("export.arxiv.org",)

        assert (
            grants[TOOL_FORMAT_CITATIONS].sensitivity is SensitivityClass.EXTERNAL_READ
        )
        assert grants[TOOL_FORMAT_CITATIONS].allowed_hosts == ()

        assert grants[TOOL_ANALYZE_STATISTICS].sensitivity is SensitivityClass.READ_ONLY
        assert grants[TOOL_ANALYZE_STATISTICS].allowed_hosts == ()

        assert (
            grants[TOOL_BUILD_KNOWLEDGE_GRAPH].sensitivity is SensitivityClass.READ_ONLY
        )
        assert grants[TOOL_BUILD_KNOWLEDGE_GRAPH].allowed_hosts == ()

        assert grants["arithmetic"].sensitivity is SensitivityClass.READ_ONLY
        assert grants["arithmetic"].allowed_hosts == ()

    def test_worker_assignment_rejects_malformed_outbound_hosts(self) -> None:
        for bad_host in (
            "https://export.arxiv.org",
            "export.arxiv.org/api",
            "export.arxiv.org:443",
            "*.arxiv.org",
            "export.arxiv.org ",
            "",
        ):
            with pytest.raises(ValidationError):
                WorkerAssignment(
                    worker_id="w1",
                    worker_type="literature_review",
                    objective="Search",
                    output_schema={},
                    permission_scopes=("research:read",),
                    tool_allowlist=(TOOL_ACADEMIC_SEARCH,),
                    allowed_outbound_hosts=(bad_host,),
                )

    def test_capability_request_fingerprint_changes_with_target_hosts(self) -> None:
        base = CapabilityRequest(
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_version="1.0.0",
            capability_scope="scope",
            sensitivity=SensitivityClass.EXTERNAL_READ,
            input_trust=TrustClassification.USER_SUPPLIED,
            input_sha256="a" * 64,
            requested_at=NOW,
            target_hosts=("export.arxiv.org",),
        )
        other = CapabilityRequest(
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_version="1.0.0",
            capability_scope="scope",
            sensitivity=SensitivityClass.EXTERNAL_READ,
            input_trust=TrustClassification.USER_SUPPLIED,
            input_sha256="a" * 64,
            requested_at=NOW,
            target_hosts=("eutils.ncbi.nlm.nih.gov",),
        )
        assert base.fingerprint() != other.fingerprint()

    def test_decide_capability_denies_external_read_without_allowed_hosts(self) -> None:
        request = CapabilityRequest(
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_version="1.0.0",
            capability_scope="scope",
            sensitivity=SensitivityClass.EXTERNAL_READ,
            input_trust=TrustClassification.USER_SUPPLIED,
            input_sha256="a" * 64,
            requested_at=NOW,
            target_hosts=("export.arxiv.org",),
        )
        grant = CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            capability_scope="scope",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_versions=("1.0.0",),
            sensitivity=SensitivityClass.EXTERNAL_READ,
            max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            requires_approval=False,
            allowed_hosts=(),
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
        )

        decision = decide_capability(request=request, grants=(grant,), now=NOW)
        assert decision.effect is CapabilityDecisionEffect.DENY
        assert decision.denial_reason is CapabilityDenialReason.HOST_NOT_ALLOWED


class TestMigrationAndOrmParityForExternalReadAndAllowedHosts:
    def test_alembic_has_single_head(self) -> None:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        assert len(script.get_heads()) == 1

    def test_migration_sql_matches_orm_column_and_sensitivity_check(self) -> None:
        import os
        import re
        import subprocess
        import sys

        alembic_bin = Path(sys.executable).parent / "alembic"
        env = {
            **os.environ,
            "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        }
        upgrade = subprocess.run(
            [str(alembic_bin), "upgrade", "c9d4a7e2b6f1:d4e8f1a3b7c2", "--sql"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        downgrade = subprocess.run(
            [str(alembic_bin), "downgrade", "d4e8f1a3b7c2:c9d4a7e2b6f1", "--sql"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        assert "allowed_hosts" in AgentCapabilityGrant.__table__.c
        assert "ADD COLUMN allowed_hosts" in upgrade.stdout
        assert "DROP COLUMN allowed_hosts" in downgrade.stdout
        assert "ck_agent_capability_grant_sensitivity" in upgrade.stdout
        assert "ck_agent_capability_grant_sensitivity" in downgrade.stdout

        match = re.search(
            r"CONSTRAINT ck_agent_capability_grant_sensitivity CHECK \(sensitivity IN \(([^)]+)\)\)",
            upgrade.stdout,
        )
        assert match is not None, upgrade.stdout
        migrated_values = {
            token.strip().strip("'") for token in match.group(1).split(",")
        }
        orm_values = {member.value for member in SensitivityClass}
        assert migrated_values == orm_values

    def test_orm_grant_persists_external_read_and_allowed_hosts(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(
            engine,
            tables=[
                AgentRun.__table__,
                AgentRunTask.__table__,
                AgentCapabilityGrant.__table__,
            ],
        )
        try:
            with sessionmaker(engine)() as session:
                session.add(
                    AgentRun(
                        run_id="run-1",
                        tenant_id="tenant-1",
                        workflow_definition_id="wf",
                        workflow_definition_version="1",
                        routing_policy_id="rp",
                        routing_policy_version="1",
                        idempotency_key="idem-1",
                        requested_by="user-1",
                        status="created",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                session.add(
                    AgentRunTask(
                        task_id="task-1",
                        run_id="run-1",
                        task_key="k1",
                        task_type="research",
                        objective="obj",
                        idempotency_key="task-idem-1",
                        status="pending",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                session.flush()
                grant_row = AgentCapabilityGrant(
                    grant_id="grant-ext-1",
                    run_id="run-1",
                    task_id="task-1",
                    capability_scope="plan-issued:mcp.academic_search",
                    tool_name=TOOL_ACADEMIC_SEARCH,
                    tool_versions=["1.0.0"],
                    sensitivity=SensitivityClass.EXTERNAL_READ.value,
                    max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED.value,
                    requires_approval=False,
                    allowed_hosts=["export.arxiv.org"],
                    issued_at=NOW,
                    expires_at=NOW + timedelta(minutes=5),
                )
                session.add(grant_row)
                session.flush()
                assert grant_row.allowed_hosts == ["export.arxiv.org"]
                assert grant_row.sensitivity == "external_read"
        finally:
            engine.dispose()


class TestReviewHardeningFixes:
    @pytest.mark.asyncio
    async def test_plan_worker_does_not_receive_sibling_workers_outbound_host_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import Mock

        import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
        from src.agents.models import AgentResult, AgentTask
        from src.ai_brain.integration.masr_supervisor_bridge import (
            MASRSupervisorBridge,
        )
        from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY
        from src.core.contracts import RoutingEdge

        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        worker_a = WorkerAssignment(
            worker_id="worker-a",
            worker_type="literature_review",
            objective="May search arXiv",
            output_schema={},
            permission_scopes=("research:read",),
            tool_allowlist=(TOOL_ACADEMIC_SEARCH,),
            allowed_outbound_hosts=("export.arxiv.org",),
        )
        worker_b = WorkerAssignment(
            worker_id="worker-b",
            worker_type="comparative_analysis",
            objective="Must not inherit Worker A outbound hosts",
            output_schema={},
            permission_scopes=("research:read",),
            tool_allowlist=(TOOL_ACADEMIC_SEARCH,),
            allowed_outbound_hosts=(),
        )
        binding = ExecutionAuthorityBinding.create_for_test(
            authority_id="auth-2w",
            authority_version="1",
            run_id="run-host-1",
            workflow_definition_id="wf-1",
            routing_policy_id="rp-1",
            strategy="parallel",
            collaboration_mode="parallel",
            domains=("research",),
            supervisor_id=None,
            supervisor_type=None,
            workers=(worker_a, worker_b),
            edges=(
                RoutingEdge(
                    source_node_id="worker-a",
                    target_node_id="worker-b",
                    relation="feeds",
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
                max_parallel_tasks=2,
                max_attempts_per_task=1,
                task_timeout_seconds=120,
            ),
            stop_conditions=("complete",),
            evaluator_requirements=(),
            deadline=NOW + timedelta(minutes=10),
            compiled_at=NOW,
        )
        grants = PlanCapabilityIssuer.issue(
            binding,
            run_id="run-host-1",
            task_id="task-host-1",
            issued_at=datetime.now(UTC) - timedelta(seconds=5),
        )

        seen_results: list[dict[str, Any]] = []

        class WorkerB:
            def __init__(self, **kwargs: Any) -> None:
                self.integration = kwargs["config"]["mcp_integration"]

            async def execute(self, task: AgentTask) -> AgentResult:
                res = await self.integration.search_academic_sources(
                    query=CANARY, databases=["arxiv"]
                )
                seen_results.append(res)
                return AgentResult(task.id, "success", res, 1.0, 0.0)

        monkeypatch.setattr(
            bridge_module,
            "MCPIntegration",
            lambda **kwargs: MCPIntegration(
                mcp_client=ResearchToolSet(academic_search=tool), **kwargs
            ),
        )
        monkeypatch.setattr(bridge_module, "settings", Mock(MCP_TOOL_PATH_ENABLED=True))

        bridge = object.__new__(MASRSupervisorBridge)
        bridge.component_registry = Mock()
        bridge.component_registry.resolve.return_value = WorkerB
        bridge.gemini_service = None
        bridge.session_factory = None
        bridge.audit_store = InMemoryToolAuditStore()

        task = AgentTask(
            "task-host-1",
            "worker",
            {},
            {
                "run_id": "run-host-1",
                "task_id": "task-host-1",
                "attempt_id": "attempt-host-1",
                "organization_id": "00000000-0000-0000-0000-000000000001",
                CAPABILITY_GRANTS_CONTEXT_KEY: grants,
            },
        )
        await bridge._execute_plan_worker(worker_b, task)

        assert seen_results[-1]["success"] is False
        assert seen_results[-1]["tool_outcome"] == "denied"
        assert captured == []

    def test_normalize_outbound_host_rejects_newline_ipv4_localhost_and_bare_str(
        self,
    ) -> None:
        from src.core.contracts.capabilities import (
            normalize_outbound_host,
            normalize_outbound_hosts,
        )

        for bad in (
            "foo\n.bar",
            "export.arxiv.org\n",
            "169.254.169.254",
            "127.0.0.1",
            "localhost",
            "intranet",
        ):
            with pytest.raises(ValueError):
                normalize_outbound_host(bad)

        with pytest.raises(ValueError):
            normalize_outbound_hosts("export.arxiv.org", field_name="allowed_hosts")

    def test_require_allowed_outbound_host_rejects_http_port_userinfo_and_bare_str(
        self,
    ) -> None:
        from src.mcp.tools.academic_search_tool import require_allowed_outbound_host

        for bad_url in (
            "http://api.crossref.org/works/10.1/x",
            "https://api.crossref.org:8443/works/10.1/x",
            "https://evil.com@api.crossref.org/works/10.1/x",
        ):
            with pytest.raises(PermissionError):
                require_allowed_outbound_host(
                    bad_url,
                    declared_hosts=("api.crossref.org",),
                    allowed_hosts=("api.crossref.org",),
                )

        with pytest.raises(TypeError):
            require_allowed_outbound_host(
                "https://api.crossref.org/works/10.1/x",
                declared_hosts="api.crossref.org",  # type: ignore[arg-type]
                allowed_hosts=("api.crossref.org",),
            )
        with pytest.raises(TypeError):
            require_allowed_outbound_host(
                "https://api.crossref.org/works/10.1/x",
                declared_hosts=("api.crossref.org",),
                allowed_hosts="api.crossref.org",  # type: ignore[arg-type]
            )

    @pytest.mark.asyncio
    async def test_tools_do_not_follow_redirects_even_when_client_has_follow_redirects_true(
        self,
    ) -> None:
        assert AcademicSearchTool().client.follow_redirects is False
        assert CitationTool().client.follow_redirects is False

        captured: list[httpx.Request] = []

        def redirect_handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.url.host in {"export.arxiv.org", "api.crossref.org"}:
                return httpx.Response(
                    302,
                    headers={"Location": "https://evil.attacker.example/collect"},
                )
            return httpx.Response(200, text="leaked")

        academic = AcademicSearchTool()
        academic.client = httpx.AsyncClient(
            transport=httpx.MockTransport(redirect_handler),
            follow_redirects=True,
        )
        await academic.execute(
            query=CANARY,
            databases=["arxiv"],
            allowed_hosts=("export.arxiv.org",),
        )
        assert [r.url.host for r in captured] == ["export.arxiv.org"]

        captured.clear()
        citation = CitationTool()
        citation.client = httpx.AsyncClient(
            transport=httpx.MockTransport(redirect_handler),
            follow_redirects=True,
        )
        res = await citation.execute(
            doi="10.1234/redirect",
            allowed_hosts=("api.crossref.org",),
        )
        assert res["success"] is False
        assert [r.url.host for r in captured] == ["api.crossref.org"]

    @pytest.mark.asyncio
    async def test_provider_cannot_contact_sibling_providers_declared_host(
        self,
    ) -> None:
        captured: list[httpx.Request] = []
        tool = AcademicSearchTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))
        tool.pubmed_base = "https://export.arxiv.org/entrez/eutils"

        with pytest.raises(PermissionError):
            await tool.execute(
                query=CANARY,
                databases=["pubmed"],
                allowed_hosts=AcademicSearchTool.OUTBOUND_HOSTS,
            )
        assert captured == []

    def test_capability_request_fingerprint_is_backward_compatible_when_target_hosts_empty(
        self,
    ) -> None:
        import hashlib

        req = CapabilityRequest(
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            tool_name="arithmetic",
            tool_version="1.0.0",
            capability_scope="scope",
            sensitivity=SensitivityClass.READ_ONLY,
            input_trust=TrustClassification.USER_SUPPLIED,
            input_sha256="a" * 64,
            requested_at=NOW,
            target_hosts=(),
        )
        legacy_material = "\x1f".join(
            (
                "r1",
                "t1",
                "a1",
                "arithmetic",
                "1.0.0",
                "scope",
                "read_only",
                "user_supplied",
                "a" * 64,
            )
        )
        expected_legacy = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
        assert req.fingerprint() == expected_legacy

        with_host = CapabilityRequest(
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            tool_name="arithmetic",
            tool_version="1.0.0",
            capability_scope="scope",
            sensitivity=SensitivityClass.READ_ONLY,
            input_trust=TrustClassification.USER_SUPPLIED,
            input_sha256="a" * 64,
            requested_at=NOW,
            target_hosts=("export.arxiv.org",),
        )
        assert with_host.fingerprint() != expected_legacy

    @pytest.mark.asyncio
    async def test_tool_call_context_allowed_hosts_narrowed_to_called_target_hosts(
        self,
    ) -> None:
        from src.agents.integrations.mcp_tool_specs import (
            AcademicSearchInput,
            AcademicSearchOutput,
            _academic_search_target_hosts,
        )
        from src.agents.tools.mediation import build_tool_boundary

        seen_allowed: list[tuple[str, ...]] = []

        async def spy_handler(params: Any, ctx: Any) -> dict[str, Any]:
            seen_allowed.append(ctx.allowed_hosts)
            return {
                "sources": [],
                "total_found": 0,
                "filters_applied": {},
                "excluded": {},
                "databases_searched": list(params.databases),
                "search_strategy": "spy",
            }

        boundary = build_tool_boundary()
        boundary.register(
            ToolSpec(
                name=TOOL_ACADEMIC_SEARCH,
                version=TOOL_VERSION,
                sensitivity=SensitivityClass.EXTERNAL_READ,
                input_model=AcademicSearchInput,
                output_model=AcademicSearchOutput,
                timeout_seconds=10.0,
                handler=spy_handler,
                outbound_hosts=AcademicSearchTool.OUTBOUND_HOSTS,
                resolve_target_hosts=_academic_search_target_hosts,
            )
        )
        grant = CapabilityGrant(
            grant_id="g-all",
            run_id="run-host-1",
            task_id="task-host-1",
            capability_scope="plan-issued:mcp.academic_search",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_versions=(TOOL_VERSION,),
            sensitivity=SensitivityClass.EXTERNAL_READ,
            max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            requires_approval=False,
            allowed_hosts=AcademicSearchTool.OUTBOUND_HOSTS,
            issued_at=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        outcome = await boundary.invoke(
            tool_name=TOOL_ACADEMIC_SEARCH,
            run_id="run-host-1",
            task_id="task-host-1",
            attempt_id="attempt-1",
            organization_id="00000000-0000-0000-0000-000000000001",
            capability_scope=grant.capability_scope,
            arguments={"query": "gnn", "databases": ["arxiv"]},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert outcome.succeeded
        assert seen_allowed == [("export.arxiv.org",)]

    def test_read_only_grant_cannot_declare_allowed_hosts(self) -> None:
        with pytest.raises(ValidationError, match="allowed_hosts"):
            CapabilityGrant(
                grant_id="g-ro-hosts",
                run_id="r1",
                task_id="t1",
                capability_scope="scope",
                tool_name="arithmetic",
                tool_versions=("1.0.0",),
                sensitivity=SensitivityClass.READ_ONLY,
                max_input_trust=TrustClassification.USER_SUPPLIED,
                requires_approval=False,
                allowed_hosts=("export.arxiv.org",),
                issued_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=5),
            )

    @pytest.mark.asyncio
    async def test_citation_tool_ignores_none_doi_and_url_encodes_doi(self) -> None:
        captured: list[httpx.Request] = []
        tool = CitationTool()
        tool.client = httpx.AsyncClient(transport=_mock_transport(captured))

        formatted = await tool.execute(
            doi=None,
            sources=[{"title": "Local Paper", "authors": ["Ada"], "year": 2024}],
        )
        assert formatted["success"] is True
        assert captured == []

        resolved = await tool.execute(
            doi="10.1234/foo bar?inject=1#frag",
            allowed_hosts=("api.crossref.org",),
        )
        assert resolved["success"] is True
        assert len(captured) == 1
        assert captured[0].url.query == b""
        assert captured[0].url.fragment == ""
        assert "%3Finject%3D1%23frag" in str(captured[0].url)

    @pytest.mark.asyncio
    async def test_rehydrate_preserves_external_read_grant_allowed_hosts(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch

        from src.api.services.direct_execution_service import DirectExecutionService

        org = "00000000-0000-0000-0000-0000000000ef"
        ts = datetime(2026, 11, 28, 12, tzinfo=UTC).replace(tzinfo=None)

        def _row(**values: object) -> Any:
            defaults = {
                "created_at": ts,
                "updated_at": ts,
                "started_at": None,
                "completed_at": None,
                "status_reason": None,
                "cancellation_requested_at": None,
                "cancellation_reason": None,
            }
            return SimpleNamespace(**(defaults | values))

        run = _row(
            run_id="run-ext",
            tenant_id=org,
            organization_id=org,
            workflow_definition_id="wf",
            workflow_definition_version="1",
            routing_policy_id="rp",
            routing_policy_version="1",
            idempotency_key="idem",
            requested_by="user",
            status="running",
            started_at=ts,
        )
        task = _row(
            run_id="run-ext",
            task_id="task-ext",
            task_key="k-ext",
            task_type="execution_plan",
            objective="objective",
            idempotency_key="task-idem",
            dependency_ids=[],
            assigned_worker_type=None,
            input={"project_id": "proj"},
            status="running",
            started_at=ts,
        )
        attempt = _row(
            run_id="run-ext",
            task_id="task-ext",
            attempt_id="attempt-ext",
            ordinal=1,
            idempotency_key="attempt-idem",
            executor_id=None,
            status="running",
            started_at=ts,
            journaled_result=None,
        )
        grant_row = _row(
            grant_id="grant-ext",
            run_id="run-ext",
            task_id="task-ext",
            capability_scope="plan-issued:mcp.academic_search",
            tool_name=TOOL_ACADEMIC_SEARCH,
            tool_versions=["1.0.0"],
            sensitivity="external_read",
            max_input_trust="external_untrusted",
            requires_approval=False,
            allowed_hosts=["export.arxiv.org"],
            issued_at=ts,
            expires_at=ts + timedelta(hours=1),
        )
        repo = Mock(
            session=Mock(),
            get_tasks_for_run=AsyncMock(return_value=[task]),
            get_attempts_for_task=AsyncMock(return_value=[attempt]),
        )
        cap_repo = Mock(list_grants_for_task=AsyncMock(return_value=[grant_row]))
        service = DirectExecutionService(session_factory=Mock())

        with patch(
            "src.api.services.direct_execution_service.CapabilityRepository",
            return_value=cap_repo,
        ):
            status = await service._rehydrate_execution(repo, run)

        assert status is not None
        assert len(status.capability_grants) == 1
        (restored,) = status.capability_grants
        assert restored.sensitivity is SensitivityClass.EXTERNAL_READ
        assert restored.allowed_hosts == ("export.arxiv.org",)
