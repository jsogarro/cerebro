"""``research-cli authority register`` stores a validated authority per tenant."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.api.services.execution_authority_resolver import (
    serialize_execution_authority_binding,
)
from src.cli.commands import authority as authority_commands
from src.cli.main import cli
from tests.integration.crash_fixtures import ORG_ID, make_binding


def _file(tmp_path: Path, binding: Any) -> Path:
    path = tmp_path / "authority.json"
    path.write_text(
        json.dumps(
            {
                "authority_id": binding.authority_id,
                "authority_version": binding.authority_version,
                "configuration": serialize_execution_authority_binding(binding),
            }
        )
    )
    return path


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str]]:
    calls: list[tuple[Any, str]] = []

    async def store(binding: Any, organization_id: str) -> None:
        calls.append((binding, organization_id))

    monkeypatch.setattr(authority_commands, "_store", store)
    return calls


def test_register_stores_the_binding_under_the_given_organization(
    tmp_path: Path, stored: list[tuple[Any, str]]
) -> None:
    binding = make_binding("cli-run", authority_id="cli-authority")
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, binding)),
            "--organization-id",
            ORG_ID,
        ],
    )

    assert result.exit_code == 0, result.output
    assert stored[0][0].authority_id == "cli-authority"
    assert stored[0][0].run.run_id == "cli-run"
    assert stored[0][1] == ORG_ID


def test_register_rejects_a_file_that_is_not_a_valid_binding(
    tmp_path: Path, stored: list[tuple[Any, str]]
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"authority_id": "a", "authority_version": "1"}))

    result = CliRunner().invoke(
        cli,
        ["authority", "register", "--file", str(path), "--organization-id", ORG_ID],
    )

    assert result.exit_code != 0
    assert stored == []


def test_register_rejects_a_non_uuid_organization(
    tmp_path: Path, stored: list[tuple[Any, str]]
) -> None:
    binding = make_binding("cli-run-2", authority_id="cli-authority-2")
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, binding)),
            "--organization-id",
            "not-a-uuid",
        ],
    )

    assert result.exit_code != 0
    assert stored == []
    assert str(uuid.UUID(ORG_ID)) == ORG_ID
