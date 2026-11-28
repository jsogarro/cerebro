"""``research-cli authority register`` stores a validated authority per tenant."""

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.api.services.execution_authority_resolver import (
    serialize_execution_authority_binding,
)
from src.cli.commands import authority as authority_commands
from src.cli.main import cli
from src.core.contracts import RunStatus
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
def stored(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, bool]]:
    calls: list[tuple[Any, str, bool]] = []

    async def store(
        binding: Any, organization_id: str, *, is_template: bool = False
    ) -> None:
        calls.append((binding, organization_id, is_template))

    monkeypatch.setattr(authority_commands, "_store", store)
    return calls


def test_register_stores_the_binding_under_the_given_organization(
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
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
    assert stored[0][2] is False


def test_register_template_flag_sets_is_template_true(
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
) -> None:
    binding = make_binding("cli-tpl-run", authority_id="cli-template")
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, binding)),
            "--organization-id",
            ORG_ID,
            "--template",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(stored) == 1
    assert stored[0][0].authority_id == "cli-template"
    assert stored[0][1] == ORG_ID
    assert stored[0][2] is True


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-5)])
def test_register_template_rejects_nonpositive_deadline_delta(
    tmp_path: Path,
    stored: list[tuple[Any, str, bool]],
    delta: timedelta,
) -> None:
    binding = make_binding("cli-bad-tpl", authority_id="cli-bad-template")
    invalid = dataclasses.replace(binding, deadline=binding.run.created_at + delta)
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, invalid)),
            "--organization-id",
            ORG_ID,
            "--template",
        ],
    )

    assert result.exit_code == 1
    assert stored == []


def test_register_template_rejects_non_created_run_status(
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
) -> None:
    binding = make_binding("cli-queued-tpl", authority_id="cli-queued-template")
    queued = dataclasses.replace(
        binding,
        run=binding.run.transition_to(
            RunStatus.QUEUED, at=binding.run.created_at + timedelta(seconds=1)
        ),
    )
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, queued)),
            "--organization-id",
            ORG_ID,
            "--template",
        ],
    )

    assert result.exit_code == 1
    assert stored == []


def test_register_template_rejects_naive_deadline(
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
) -> None:
    binding = make_binding("cli-naive-tpl", authority_id="cli-naive-template")
    path = _file(tmp_path, binding)
    document = json.loads(path.read_text())
    document["configuration"]["deadline"] = datetime(2026, 8, 5, 12, 0, 0).isoformat()
    path.write_text(json.dumps(document))

    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(path),
            "--organization-id",
            ORG_ID,
            "--template",
        ],
    )

    assert result.exit_code == 1
    assert stored == []


@pytest.mark.parametrize("extra_args", [[], ["--template"]])
def test_register_rejects_reserved_mint_version_prefix(
    tmp_path: Path,
    stored: list[tuple[Any, str, bool]],
    extra_args: list[str],
) -> None:
    binding = dataclasses.replace(
        make_binding("cli-mint-prefix", authority_id="cli-mint-auth"),
        authority_version="mint:00000000-0000-0000-0000-000000000001",
    )
    result = CliRunner().invoke(
        cli,
        [
            "authority",
            "register",
            "--file",
            str(_file(tmp_path, binding)),
            "--organization-id",
            ORG_ID,
            *extra_args,
        ],
    )

    assert result.exit_code == 1
    assert stored == []
    assert datetime.now(UTC).tzinfo is not None


def test_register_rejects_a_file_that_is_not_a_valid_binding(
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
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
    tmp_path: Path, stored: list[tuple[Any, str, bool]]
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
