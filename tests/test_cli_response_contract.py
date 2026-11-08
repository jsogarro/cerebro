"""CLI renderers and WebSocket client against the API's real response contract.

Payloads are built from the API's own Pydantic response models, so these tests
break if a model field is renamed or the CLI reads a key the API does not send.
"""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from src.api.routes.query_api import IntelligentQueryResponse
from src.cli.client import APIError, ResearchAPIClient
from src.cli.main import cli
from src.cli.websocket_client import CLIWebSocketClient
from src.models.masr_api_models import (
    CostBreakdown,
    CostEstimationResponse,
    ModelInfo,
    RouterStatus,
    RoutingDecisionResponse,
    SupervisorAllocation,
)

SECRET = "tok.SECRET.value"


def _dump(model) -> dict:
    return json.loads(model.model_dump_json())


def _invoke(args: list[str], *, get=None, post=None):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = get
    client.post.return_value = post
    with patch("src.cli.commands.agents.ResearchAPIClient", return_value=client):
        return CliRunner().invoke(cli, ["--no-color", "agents", *args])


def test_route_renders_selected_models_and_supervisors() -> None:
    payload = _dump(
        RoutingDecisionResponse(
            routing_id="r-1",
            domain="research",
            complexity="simple",
            strategy="balanced",
            collaboration_mode="direct",
            supervisor_allocations=[
                SupervisorAllocation(
                    supervisor_type="research_sup",
                    worker_count=2,
                    refinement_rounds=1,
                    estimated_latency_ms=10,
                )
            ],
            selected_models=[
                ModelInfo(
                    provider="deepseek",
                    model_id="deepseek-chat-xyz",
                    tier="standard",
                    cost_per_token=0.001,
                    max_tokens=1000,
                    capabilities=[],
                    average_latency_ms=5,
                    quality_score=0.5,
                )
            ],
            estimated_cost=1.2345,
            estimated_latency_ms=10,
            confidence_score=0.9,
            reasoning="because",
        )
    )
    out = _invoke(["route", "q"], post=payload).output
    assert "deepseek-chat-xyz" in out
    assert "research_sup" in out
    assert "N/A" not in out
    assert "$1.2345" in out


def test_estimate_renders_breakdown_and_total() -> None:
    payload = _dump(
        CostEstimationResponse(
            estimated_cost=3.5,
            breakdown=CostBreakdown(
                model_costs=1.25,
                coordination_overhead=0.75,
                memory_operations=0.5,
                total_cost=3.5,
            ),
            confidence_score=0.8,
            cost_factors={},
            recommendations=[],
        )
    )
    out = _invoke(["estimate", "q"], post=payload).output
    assert "$1.2500" in out
    assert "$0.7500" in out
    assert "$3.5000" in out


def test_status_renders_router_metrics() -> None:
    payload = _dump(
        RouterStatus(
            status="healthy",
            uptime_seconds=1,
            total_routes=4242,
            average_latency_ms=87.5,
            success_rate=0.99,
            active_supervisors=1,
            performance_metrics={},
            model_availability={},
            learning_metrics={},
        )
    )
    out = _invoke(["status"], get=payload).output
    assert "4242" in out
    assert "87.5" in out


def test_query_prints_results_not_whole_envelope() -> None:
    payload = _dump(
        IntelligentQueryResponse(
            execution_id="e",
            query_id="q",
            status="completed",
            routing_decision={"routing_marker": "R"},
            supervisor_type="s",
            selected_agents=[],
            estimated_cost=0.0,
            estimated_quality=0.0,
            results={"answer": "forty-two"},
            quality_scores={},
            confidence=1.0,
            routing_time_ms=0.0,
            execution_time_seconds=0.0,
            total_time_seconds=0.0,
            started_at="now",
        )
    )
    out = _invoke(["query", "q"], post=payload).output
    assert "forty-two" in out
    assert "routing_marker" not in out


def _jwt(sub: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).rstrip(b"=")
    return f"h.{body.decode()}.s"


@pytest.mark.asyncio
async def test_create_project_defaults_user_id_to_token_subject() -> None:
    sent: dict = {}

    async def fake_request(self, method, path, json_data=None, **kw):
        sent.update(json_data)
        raise APIError(418, "stop")

    with (
        patch("src.cli.client.config.auth_token", _jwt("user-123")),
        patch.object(ResearchAPIClient, "_request", fake_request),
        pytest.raises(APIError),
    ):
        async with ResearchAPIClient() as c:
            await c.create_project("t", "q", ["AI"], user_id=None)
    assert sent["user_id"] == "user-123"


@pytest.mark.asyncio
async def test_create_project_without_identity_fails_before_request() -> None:
    with (
        patch("src.cli.client.config.auth_token", None),
        patch.object(ResearchAPIClient, "_request", AsyncMock()) as req,
        pytest.raises(APIError, match="user"),
    ):
        async with ResearchAPIClient() as c:
            await c.create_project("t", "q", ["AI"], user_id=None)
    req.assert_not_called()


@pytest.mark.asyncio
async def test_ws_url_merges_query_and_log_omits_token(capsys) -> None:
    connect = AsyncMock(return_value=AsyncMock())
    with patch("src.cli.websocket_client.websockets.connect", new=connect):
        client = CLIWebSocketClient(base_url="ws://h:1", token=SECRET, verbose=True)
        client.console.print = lambda m, *a, **k: logged.append(str(m))
        logged: list[str] = []
        assert await client.connect("/ws/cli/p?format=logs")
    url = connect.call_args.args[0]
    assert url.count("?") == 1
    assert "format=logs" in url and f"token={SECRET}" in url
    assert SECRET not in "".join(logged)
