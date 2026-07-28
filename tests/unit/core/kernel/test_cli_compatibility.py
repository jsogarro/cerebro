"""CLI compatibility commands remain thin adapters over migrated HTTP routes."""

from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from src.api.routes import agent_api
from src.cli.commands import agents
from src.cli.main import cli
from src.models.agent_api_models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentType,
    ChainOfAgentsRequest,
    ChainOfAgentsResponse,
)


class _KernelAgentBackend:
    async def execute_single_agent(
        self,
        agent_type: AgentType,
        request: AgentExecutionRequest,
    ) -> AgentExecutionResponse:
        return AgentExecutionResponse(
            execution_id="cli-agent-execution",
            agent_type=agent_type,
            status="completed",
            output={"answer": f"kernel handled {request.query}"},
            confidence=0.9,
            quality_score=0.9,
            execution_time_seconds=0.1,
            started_at=datetime(2026, 7, 27, tzinfo=UTC),
        )

    async def execute_chain_of_agents(
        self,
        request: ChainOfAgentsRequest,
    ) -> ChainOfAgentsResponse:
        return ChainOfAgentsResponse(
            execution_id="cli-chain-execution",
            status="completed",
            agent_chain=request.agent_chain,
            intermediate_results=[],
            final_result={"answer": f"kernel chained {request.query}"},
            overall_confidence=0.9,
            total_execution_time_seconds=0.1,
            agent_execution_times=[0.05 for _ in request.agent_chain],
            chain_quality_score=0.9,
            quality_improvement=0.0,
            started_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


class _KernelHTTPClient:
    calls: list[tuple[str, str, dict]] = []
    backend = _KernelAgentBackend()

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, path, payload):
        self.calls.append(("post", path, payload))
        if path.endswith("/execute"):
            agent_type = AgentType(path.split("/")[-2])
            response = await agent_api.execute_agent(
                agent_type,
                AgentExecutionRequest(**payload),
                background_tasks=None,
                execution_service=self.backend,
            )
        elif path == "/api/v1/agents/chain":
            response = await agent_api.execute_chain_of_agents(
                ChainOfAgentsRequest(**payload),
                execution_service=self.backend,
            )
        else:
            raise AssertionError(f"unexpected endpoint: {path}")
        return response.model_dump(mode="json")


@pytest.mark.parametrize(
    ("arguments", "expected_call", "expected_output"),
    [
        (
            ["agents", "execute", "literature-review", "CLI direct query"],
            (
                "post",
                "/api/v1/agents/literature-review/execute",
                {"query": "CLI direct query", "parameters": {}},
            ),
            "kernel handled CLI direct query",
        ),
        (
            [
                "agents",
                "chain",
                "CLI chain query",
                "-a",
                "literature-review",
                "-a",
                "synthesis",
            ],
            (
                "post",
                "/api/v1/agents/chain",
                {
                    "query": "CLI chain query",
                    "agent_chain": ["literature-review", "synthesis"],
                },
            ),
            "kernel chained CLI chain query",
        ),
    ],
)
def test_cli_agent_commands_preserve_kernel_endpoint_payload_and_output(
    monkeypatch,
    arguments,
    expected_call,
    expected_output,
):
    _KernelHTTPClient.calls = []
    monkeypatch.setattr(agents, "ResearchAPIClient", _KernelHTTPClient)

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0, result.output
    assert _KernelHTTPClient.calls == [expected_call]
    assert expected_output in result.output
