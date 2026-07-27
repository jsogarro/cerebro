"""Credential-free request mapping checks for ``research-cli agents``."""

import pytest
from click.testing import CliRunner

from src.cli.commands import agents
from src.cli.main import cli


class _Client:
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, path, payload):
        self.calls.append(("post", path, payload))
        return {"output": "fixture"}

    async def get(self, path):
        self.calls.append(("get", path, {}))
        return {"status": "healthy"}


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(agents, "ResearchAPIClient", _Client)
    return _Client


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["agents", "query", "query text", "--type", "analyze", "-d", "research"],
            (
                "post",
                "/api/v1/query/analyze",
                {"query": "query text", "domains": ["research"]},
            ),
        ),
        (
            ["agents", "route", "query text", "--strategy", "quality_focused"],
            (
                "post",
                "/api/v1/masr/route",
                {"query": "query text", "strategy": "quality_focused"},
            ),
        ),
        (
            ["agents", "estimate", "query text", "-d", "research"],
            (
                "post",
                "/api/v1/masr/estimate-cost",
                {"query": "query text", "domains": ["research"]},
            ),
        ),
        (
            [
                "agents",
                "execute",
                "literature-review",
                "query text",
                "--max-sources",
                "10",
            ],
            (
                "post",
                "/api/v1/agents/literature-review/execute",
                {"query": "query text", "parameters": {"max_sources": 10}},
            ),
        ),
        (
            [
                "agents",
                "chain",
                "query text",
                "-a",
                "literature-review",
                "-a",
                "synthesis",
            ],
            (
                "post",
                "/api/v1/agents/chain",
                {
                    "query": "query text",
                    "agent_chain": ["literature-review", "synthesis"],
                },
            ),
        ),
        (["agents", "status"], ("get", "/api/v1/masr/status", {})),
    ],
)
def test_cli_agents_commands_pin_request_mapping(arguments, expected):
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0, result.output
    assert _Client.calls == [expected]
