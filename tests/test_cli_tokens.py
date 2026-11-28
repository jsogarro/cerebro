"""Tests for `research-cli tokens` subcommands."""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from src.cli.main import cli


def test_cli_imports_without_server_secret_key() -> None:
    env = {key: value for key, value in os.environ.items() if key != "SECRET_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", "from src.cli.main import cli; cli(['--help'])"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "tokens" in result.stdout


def test_cli_tokens_create_list_revoke(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cli.commands.tokens as tokens_module

    created_payload = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "claude-code",
        "token_prefix": "crb_pat_abcdef12",
        "token": "crb_pat_abcdef1234567890_secret_value",
        "scopes": ["mcp:read"],
        "expires_at": "2026-12-27T00:00:00+00:00",
        "revoked_at": None,
        "last_used_at": None,
        "created_at": "2026-11-27T00:00:00+00:00",
    }
    listed_payload = [
        {key: val for key, val in created_payload.items() if key != "token"}
    ]

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.create_personal_access_token.return_value = created_payload
    fake_client.list_personal_access_tokens.return_value = listed_payload
    fake_client.revoke_personal_access_token.return_value = None

    monkeypatch.setattr(
        tokens_module, "ResearchAPIClient", lambda **_kwargs: fake_client
    )

    runner = CliRunner()

    create_res = runner.invoke(
        cli,
        ["tokens", "create", "--name", "claude-code", "--expires-in-days", "30"],
    )
    assert create_res.exit_code == 0, create_res.output
    assert created_payload["token"] in create_res.output
    fake_client.create_personal_access_token.assert_awaited_once_with(
        name="claude-code", expires_in_days=30, scopes=["mcp:read"]
    )

    list_res = runner.invoke(cli, ["tokens", "list"])
    assert list_res.exit_code == 0, list_res.output
    assert "claude-code" in list_res.output
    assert created_payload["token"] not in list_res.output

    revoke_res = runner.invoke(
        cli, ["tokens", "revoke", "11111111-1111-1111-1111-111111111111"]
    )
    assert revoke_res.exit_code == 0, revoke_res.output
    fake_client.revoke_personal_access_token.assert_awaited_once_with(
        "11111111-1111-1111-1111-111111111111"
    )
