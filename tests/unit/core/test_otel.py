"""Tests for OpenTelemetry SDK ownership, instrumentation, redaction, and fault isolation."""

from __future__ import annotations

import threading
import time
import tomllib
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.agents.models import AgentResult, AgentTask
from src.agents.tools.base_tool import AgentTool
from src.agents.tools.mediation import ToolCallIdentity
from src.agents.tools.registry import ToolRegistry
from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
)
from src.core.budget import BudgetLedger
from src.core.config import Settings
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    ExecutionPlan,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingDecision,
    WorkerAssignment,
)
from src.core.otel import (
    GENAI_SEMCONV_SCHEMA_URL,
    SEMCONV_SCHEMA_URL,
    get_otel_tracer_provider,
    init_otel,
    is_otel_enabled,
    normalize_otlp_traces_endpoint,
    reset_otel_state,
    shutdown_otel,
    trace_provider_span,
    trace_run_span,
    trace_tool_span,
    trace_worker_span,
)
from src.services.model_invocation import (
    CallPrice,
    ModelInvocationContext,
    invoke_model,
)

SECRET_API_KEY = "sk-SENTINEL-do-not-export-998877"
SECRET_BEARER = "Bearer eyJhbGciOiJSUzI1NiJ9.SENTINEL_JWT_PAYLOAD.sig"
SECRET_HELD = "CUSTOM_HELD_SENTINEL_SECRET_42"
SECRET_PROMPT = "PROMPT_SENTINEL_CONFIDENTIAL_TEXT"
SECRET_COMPLETION = "COMPLETION_SENTINEL_CONFIDENTIAL_TEXT"
SECRET_SOURCE = "SOURCE_SENTINEL_VERBATIM_EXCERPT"
SECRET_TOOL_ARG = "TOOL_ARG_SENTINEL_RAW_VALUE"
SECRET_SQL_LITERAL = "SQL_LITERAL_SENTINEL_DATA"
SECRET_QUERY_TOKEN = "QUERY_TOKEN_SENTINEL_12345"


class _RaisingExporter(SpanExporter):
    """Exporter that raises on every lifecycle method to test fault isolation."""

    def __init__(self) -> None:
        self.export_attempts = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.export_attempts += len(spans)
        raise RuntimeError("OTLP collector unreachable")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError("flush boom")

    def shutdown(self) -> None:
        raise RuntimeError("shutdown boom")


class _HangingExporter(SpanExporter):
    """Exporter that blocks in shutdown until released, to test bounded timeout."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._release.wait(timeout=10.0)


@pytest.fixture(autouse=True)
def _clean_otel_state():
    reset_otel_state()
    yield
    reset_otel_state()


def _serialized_spans(spans: Sequence[ReadableSpan]) -> str:
    parts: list[str] = []
    for span in spans:
        parts.append(span.name)
        parts.append(repr(dict(span.attributes or {})))
        parts.append(repr(span.status.description))
        for event in span.events:
            parts.append(event.name)
            parts.append(repr(dict(event.attributes or {})))
    return "\n".join(parts)


class TestConfigurationAndPrecedence:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENABLE_TRACING", raising=False)
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        fresh = Settings(SECRET_KEY="x" * 32)
        assert fresh.ENABLE_TRACING is False
        assert fresh.OTEL_EXPORTER_OTLP_ENDPOINT is None
        monkeypatch.setattr("src.core.config.get_settings", lambda: fresh)

        assert is_otel_enabled() is False
        assert init_otel() is False
        assert get_otel_tracer_provider() is None
        # Global provider must remain uninstalled (ProxyTracerProvider).
        assert not isinstance(trace.get_tracer_provider(), TracerProvider)

    def test_endpoint_alone_does_not_enable_tracing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fresh = Settings(
            SECRET_KEY="x" * 32,
            ENABLE_TRACING=False,
            OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4318",
        )
        monkeypatch.setattr("src.core.config.get_settings", lambda: fresh)

        assert is_otel_enabled() is False
        assert init_otel() is False
        assert get_otel_tracer_provider() is None

    def test_otel_sdk_disabled_env_overrides_enable_tracing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = Settings(
            SECRET_KEY="x" * 32,
            ENABLE_TRACING=True,
            OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4318",
        )
        monkeypatch.setattr("src.core.config.get_settings", lambda: fresh)
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

        assert is_otel_enabled() is False
        assert init_otel() is False
        assert get_otel_tracer_provider() is None

    def test_endpoint_normalization_supports_collector_and_langfuse_otlp(self) -> None:
        assert (
            normalize_otlp_traces_endpoint("http://otel-collector:4318")
            == "http://otel-collector:4318/v1/traces"
        )
        assert (
            normalize_otlp_traces_endpoint("http://otel-collector:4318/")
            == "http://otel-collector:4318/v1/traces"
        )
        langfuse_otlp = "https://cloud.langfuse.com/api/public/otel/v1/traces"
        assert normalize_otlp_traces_endpoint(langfuse_otlp) == langfuse_otlp

    def test_semconv_schema_urls_are_pinned(self) -> None:
        assert SEMCONV_SCHEMA_URL.startswith("https://opentelemetry.io/schemas/")
        assert "semconv-genai" in GENAI_SEMCONV_SCHEMA_URL

    def test_otlp_http_exporter_declared_as_direct_dependency(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        pyproject = tomllib.loads(
            (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies: list[str] = pyproject["project"]["dependencies"]
        assert any(
            dep.lower().startswith("opentelemetry-exporter-otlp-proto-http")
            for dep in dependencies
        ), "opentelemetry-exporter-otlp-proto-http must be declared directly"

    def test_repo_otlp_endpoint_templates_use_http_port_4318(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        endpoints: dict[str, str] = {}
        for name in (".env.example", ".env.production.example"):
            for raw_line in (repo_root / name).read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if line.startswith("OTEL_EXPORTER_OTLP_ENDPOINT="):
                    endpoints[name] = line.split("=", 1)[1].strip()
                    break
            assert name in endpoints, f"OTEL_EXPORTER_OTLP_ENDPOINT missing in {name}"

        configmap = yaml.safe_load(
            (repo_root / "k8s" / "configmap.yaml").read_text(encoding="utf-8")
        )
        endpoints["k8s/configmap.yaml"] = configmap["data"][
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        ]

        for source, endpoint in endpoints.items():
            parsed = urlparse(endpoint)
            assert parsed.scheme in {"http", "https"}, (source, endpoint)
            assert parsed.port == 4318, (source, endpoint)
            assert normalize_otlp_traces_endpoint(endpoint).endswith(":4318/v1/traces")


class TestPrivateProviderAndInstrumentation:
    def test_single_private_provider_does_not_mutate_global_provider(self) -> None:
        exporter = InMemorySpanExporter()
        global_before = trace.get_tracer_provider()

        assert init_otel(span_exporter=exporter, force_enable=True) is True
        provider = get_otel_tracer_provider()
        assert isinstance(provider, TracerProvider)
        assert trace.get_tracer_provider() is global_before
        assert provider is not global_before

        shutdown_otel()
        assert get_otel_tracer_provider() is None

    def test_request_produces_one_trace_with_db_and_http_child_spans(self) -> None:
        exporter = InMemorySpanExporter()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "ok"})
        )

        @asynccontextmanager
        async def _lifespan(app: FastAPI):
            init_otel(
                app=app,
                engines=(engine,),
                span_exporter=exporter,
                force_enable=True,
            )
            try:
                yield
            finally:
                shutdown_otel()
                await engine.dispose()

        mini_app = FastAPI(lifespan=_lifespan)

        @mini_app.get("/work")
        async def do_work() -> dict[str, Any]:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            async with httpx.AsyncClient(transport=transport) as client:
                resp = await client.get(
                    f"https://upstream.example.com/items?api_key={SECRET_QUERY_TOKEN}",
                    headers={"Authorization": SECRET_BEARER},
                )
            return {"db": True, "upstream": resp.status_code}

        with TestClient(mini_app) as client:
            res = client.get(
                f"/work?token={SECRET_QUERY_TOKEN}",
                headers={"Authorization": SECRET_BEARER},
            )
            assert res.status_code == 200
            assert res.json() == {"db": True, "upstream": 200}

        spans = exporter.get_finished_spans()
        assert len(spans) >= 3

        server_spans = [s for s in spans if s.kind == trace.SpanKind.SERVER]
        db_spans = [
            s for s in spans if (s.attributes or {}).get("db.system") == "sqlite"
        ]
        http_client_spans = [
            s
            for s in spans
            if s.kind == trace.SpanKind.CLIENT
            and (s.attributes or {}).get("server.address") == "upstream.example.com"
        ]

        assert len(server_spans) == 1
        assert len(db_spans) >= 1
        assert len(http_client_spans) == 1

        root_span = server_spans[0]
        trace_id = root_span.context.trace_id
        for child in (*db_spans, *http_client_spans):
            assert child.context.trace_id == trace_id
            assert child.parent is not None
            assert child.parent.span_id == root_span.context.span_id

        blob = _serialized_spans(spans)
        for sentinel in (SECRET_BEARER, SECRET_QUERY_TOKEN):
            assert sentinel not in blob


class TestRedactionAndSanitization:
    def test_sanitizing_processor_strips_secrets_content_and_high_cardinality_labels(
        self,
    ) -> None:
        exporter = InMemorySpanExporter()
        assert (
            init_otel(
                span_exporter=exporter,
                secret_values_provider=lambda: frozenset({SECRET_HELD}),
                force_enable=True,
            )
            is True
        )
        provider = get_otel_tracer_provider()
        assert provider is not None
        tracer = provider.get_tracer("cerebro.test")

        with tracer.start_as_current_span("sensitive_op") as span:
            span.set_attribute("gen_ai.prompt", SECRET_PROMPT)
            span.set_attribute("gen_ai.completion", SECRET_COMPLETION)
            span.set_attribute("source_text", SECRET_SOURCE)
            span.set_attribute("gen_ai.tool.call.arguments", SECRET_TOOL_ARG)
            span.set_attribute("db.statement", f"SELECT '{SECRET_SQL_LITERAL}'")
            span.set_attribute("http.request.header.authorization", SECRET_BEARER)
            span.set_attribute("url.query", f"token={SECRET_QUERY_TOKEN}")
            span.set_attribute(
                "url.full",
                f"https://user:pass@api.example.com/v1/chat?api_key={SECRET_API_KEY}",
            )
            span.set_attribute("user.id", "unbounded-user-uuid-123")
            span.set_attribute(
                "safe.note",
                f"key={SECRET_API_KEY} held={SECRET_HELD} email=alice@example.com",
            )
            span.add_event(
                "exception",
                attributes={
                    "exception.type": "RuntimeError",
                    "exception.message": f"failed with {SECRET_API_KEY} and {SECRET_HELD}",
                },
            )
            span.set_status(
                trace.StatusCode.ERROR,
                description=f"boom {SECRET_BEARER} {SECRET_HELD}",
            )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        exported = spans[0]
        attrs = dict(exported.attributes or {})

        for forbidden_key in (
            "gen_ai.prompt",
            "gen_ai.completion",
            "source_text",
            "gen_ai.tool.call.arguments",
            "db.statement",
            "http.request.header.authorization",
            "url.query",
            "user.id",
        ):
            assert forbidden_key not in attrs

        assert attrs["url.full"] == "https://api.example.com/v1/chat"
        assert "[EMAIL]" in str(attrs["safe.note"])
        assert "[REDACTED]" in str(attrs["safe.note"])

        blob = _serialized_spans(spans)
        for sentinel in (
            SECRET_API_KEY,
            SECRET_BEARER,
            SECRET_HELD,
            SECRET_PROMPT,
            SECRET_COMPLETION,
            SECRET_SOURCE,
            SECRET_TOOL_ARG,
            SECRET_SQL_LITERAL,
            SECRET_QUERY_TOKEN,
            "alice@example.com",
            "user:pass@",
        ):
            assert sentinel not in blob


class TestFaultIsolationAndBoundedShutdown:
    def test_raising_exporter_does_not_alter_request_or_operation_outcomes(
        self,
    ) -> None:
        raising_exporter = _RaisingExporter()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"v": 7})
        )

        @asynccontextmanager
        async def _lifespan(app: FastAPI):
            init_otel(
                app=app,
                engines=(engine,),
                span_exporter=raising_exporter,
                force_enable=True,
            )
            try:
                yield
            finally:
                shutdown_otel()
                await engine.dispose()

        mini_app = FastAPI(lifespan=_lifespan)

        @mini_app.get("/check")
        async def check() -> dict[str, int]:
            async with engine.begin() as conn:
                row = (await conn.execute(text("SELECT 42"))).scalar_one()
            async with httpx.AsyncClient(transport=transport) as client:
                resp = await client.get("https://upstream.example.com/v")
            return {"db": int(row), "http": resp.json()["v"]}

        with TestClient(mini_app) as client:
            res = client.get("/check")
            assert res.status_code == 200
            assert res.json() == {"db": 42, "http": 7}

        assert raising_exporter.export_attempts >= 3
        assert get_otel_tracer_provider() is None

    def test_hung_exporter_shutdown_is_bounded(self) -> None:
        release = threading.Event()
        hanging = _HangingExporter(release)
        init_otel(span_exporter=hanging, force_enable=True)
        try:
            start = time.monotonic()
            shutdown_otel(timeout_seconds=0.1)
            elapsed = time.monotonic() - start
            assert elapsed < 1.5
            assert get_otel_tracer_provider() is None
        finally:
            release.set()


class TestG03RunWorkerProviderToolHierarchy:
    @pytest.mark.asyncio
    async def test_plan_worker_provider_and_tool_share_single_trace(self) -> None:
        exporter = InMemorySpanExporter()
        init_otel(
            span_exporter=exporter,
            secret_values_provider=lambda: frozenset({SECRET_HELD}),
            force_enable=True,
        )

        budget = ExecutionBudget(
            max_cost_usd=Decimal("1"),
            max_total_tokens=10_000,
            max_tool_invocations=5,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=30,
        )
        worker = WorkerAssignment(
            worker_id="w-lit-1",
            worker_type="literature_reviewer",
            objective="Gather evidence",
            output_schema={},
            permission_scopes=("tools:read",),
            tool_allowlist=("echo_tool",),
        )
        decision = RoutingDecision(
            routing_decision_id="dec-1",
            strategy="balanced",
            domains=("research",),
            collaboration_mode=CollaborationMode.DIRECT,
            supervisor_id=None,
            supervisor_type=None,
            workers=(worker,),
            edges=(),
            provider_model_policy=ProviderModelPolicy(
                primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-flash"),
                fallback_mode=FallbackMode.FAIL_CLOSED,
                fallbacks=(),
                provider_allowlist=("gemini",),
                model_allowlist=("gemini-2.5-flash",),
            ),
            budget=budget,
            stop_conditions=("complete",),
            evaluator_requirements=(),
        )
        now = datetime.now(UTC)
        plan = ExecutionPlan(
            execution_plan_id="plan-1",
            plan_version=1,
            run_id="run-g03-1",
            workflow_definition_id="wf-1",
            workflow_definition_version="2.0.0",
            routing_policy_id="policy-1",
            routing_policy_version="1",
            compiled_at=now,
            deadline=now + timedelta(minutes=1),
            amendment=None,
            routing_decision=decision,
        )

        class _EchoParams(BaseModel):
            text: str

        class _EchoTool(AgentTool[_EchoParams]):
            name = "echo_tool"
            description = "test tool"

            @property
            def params_model(self) -> type[_EchoParams]:
                return _EchoParams

            async def _execute_impl(self, params: _EchoParams) -> dict[str, str]:
                return {"echo": params.text}

        registry = ToolRegistry()
        registry.register(_EchoTool())

        async def worker_exec(
            assigned: WorkerAssignment, worker_task: AgentTask
        ) -> AgentResult:
            ctx = ModelInvocationContext(ledger=BudgetLedger(budget))
            price = CallPrice(cost_usd=Decimal("0.001"), digest="d" * 64)

            async def provider_call(_timeout: float | None) -> Any:
                return SimpleNamespace(
                    success=True,
                    content=f"secret completion {SECRET_COMPLETION}",
                    finish_reason="completed",
                    total_tokens=30,
                    prompt_tokens=20,
                    completion_tokens=10,
                    cost_estimate=0.001,
                    provider="gemini",
                    model_name="gemini-2.5-flash",
                    request_id="req-1",
                )

            call_res = await invoke_model(
                ctx, provider_call, estimated_tokens=100, price=price
            )
            assert call_res.succeeded

            tool_res = await registry.execute(
                "echo_tool",
                {"text": f"{SECRET_TOOL_ARG} {SECRET_HELD}"},
                identity=ToolCallIdentity.from_agent_task(worker_task),
            )
            assert tool_res.success is True
            return AgentResult(
                task_id=worker_task.id,
                status="success",
                output={"summary": "ok"},
                confidence=0.9,
                execution_time=0.01,
            )

        executor = ExecutionPlanTopologyExecutor(
            worker_executor=worker_exec,
            capability_checker=lambda _t: True,
        )
        root_task = AgentTask(
            id="task-1",
            agent_type="research",
            input_data={"query": SECRET_PROMPT},
            context={"run_id": "run-g03-1"},
        )
        plan_res = await executor.execute(plan, root_task)
        assert plan_res.succeeded

        spans = exporter.get_finished_spans()
        names = {s.name for s in spans}
        assert "invoke_workflow research_workflow" in names
        assert "invoke_agent literature_reviewer" in names
        assert "chat gemini-2.5-flash" in names or "chat" in names
        assert "execute_tool echo_tool" in names

        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1

        workflow_span = next(
            s for s in spans if s.name == "invoke_workflow research_workflow"
        )
        agent_span = next(
            s for s in spans if s.name == "invoke_agent literature_reviewer"
        )
        tool_span = next(s for s in spans if s.name == "execute_tool echo_tool")
        chat_span = next(s for s in spans if s.name.startswith("chat"))

        assert agent_span.parent is not None
        assert agent_span.parent.span_id == workflow_span.context.span_id
        assert tool_span.parent is not None
        assert tool_span.parent.span_id == agent_span.context.span_id
        assert chat_span.parent is not None
        assert chat_span.parent.span_id == agent_span.context.span_id

        assert (agent_span.attributes or {}).get("cerebro.worker_id") == "w-lit-1"
        assert (tool_span.attributes or {}).get("gen_ai.tool.name") == "echo_tool"
        assert (chat_span.attributes or {}).get("gen_ai.usage.input_tokens") == 20
        assert (chat_span.attributes or {}).get("gen_ai.usage.output_tokens") == 10

        blob = _serialized_spans(spans)
        for sentinel in (
            SECRET_PROMPT,
            SECRET_COMPLETION,
            SECRET_TOOL_ARG,
            SECRET_HELD,
        ):
            assert sentinel not in blob

    def test_span_context_managers_are_noops_when_disabled(self) -> None:
        assert get_otel_tracer_provider() is None
        with (
            trace_run_span(run_id="r1") as run_s,
            trace_worker_span(agent_name="w1", run_id="r1") as worker_s,
            trace_provider_span(provider="gemini", model="m", run_id="r1") as prov_s,
            trace_tool_span(
                tool_name="t1", run_id="r1", task_id="tk1", attempt_id="a1"
            ) as tool_s,
        ):
            assert run_s is None
            assert worker_s is None
            assert prov_s is None
            assert tool_s is None


class TestLifespanAndLangfuseCharacterization:
    def test_main_app_lifespan_installs_no_provider_when_disabled(self) -> None:
        from src.api.main import app

        with TestClient(app) as client:
            res = client.get("/health")
            assert res.status_code == 200
            assert get_otel_tracer_provider() is None
            assert not getattr(app, "_is_instrumented_by_opentelemetry", False)

    def test_main_app_lifespan_wires_and_tears_down_otel_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.api import main
        from src.core import otel

        exporter = InMemorySpanExporter()
        original_init = otel.init_otel

        def _init_with_fake_exporter(*args: Any, **kwargs: Any) -> bool:
            kwargs.setdefault("span_exporter", exporter)
            kwargs.setdefault("force_enable", True)
            return original_init(*args, **kwargs)

        monkeypatch.setattr(otel, "init_otel", _init_with_fake_exporter)

        with TestClient(main.app) as client:
            assert get_otel_tracer_provider() is not None
            res = client.get("/health")
            assert res.status_code == 200

        assert get_otel_tracer_provider() is None
        assert not getattr(main.app, "_is_instrumented_by_opentelemetry", False)
        spans = exporter.get_finished_spans()
        assert any(
            s.kind == trace.SpanKind.SERVER and "/health" in s.name for s in spans
        )

    def test_langfuse_remains_isolated_from_private_otel_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        from src.core import tracing

        exporter = InMemorySpanExporter()
        init_otel(span_exporter=exporter, force_enable=True)

        fake_lf = MagicMock(name="LangfuseClient")
        fake_lf.create_trace_id.return_value = "lf-trace-1"
        routing_obs = MagicMock(name="RoutingSpan")
        fake_lf.start_observation.return_value = routing_obs

        monkeypatch.setattr(tracing, "_tracing_enabled", lambda: True)
        monkeypatch.setattr(tracing, "get_langfuse_client", lambda: fake_lf)

        with tracing.trace_masr_routing("q-1", f"find {SECRET_PROMPT}") as obs:
            assert obs is routing_obs

        fake_lf.start_observation.assert_called_once()
        routing_obs.end.assert_called_once()
        # OTel private provider stays independent and never exports raw query text.
        assert SECRET_PROMPT not in _serialized_spans(exporter.get_finished_spans())
