"""J7: research-cli against the stack: health, create, status, results (csv), refine."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.e2e.journeys.harness import BASE_URL

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]


async def research_cli(token: str, *args: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as home:
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "RESEARCH_API_URL": BASE_URL,
            "RESEARCH_AUTH_TOKEN": token,
            "NO_COLOR": "1",
            "COLUMNS": "200",
        }
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", "from src.cli.main import cli; cli()", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


async def _created_project(account) -> str:
    created = await research_cli(
        account.token,
        "-f",
        "json",
        "projects",
        "create",
        "-t",
        "cli journey",
        "-q",
        "What is ML",
        "-d",
        "AI",
        "--depth",
        "survey",
    )
    assert created.returncode == 0, created.stdout + created.stderr
    return json.loads(created.stdout[created.stdout.index("{") :])["id"]


async def test_cli_health_reports_the_stack_healthy_and_ready(new_account):
    result = await research_cli((await new_account("cli")).token, "health")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "API is healthy" in result.stdout
    assert "API is ready" in result.stdout


async def test_cli_create_and_status_match_the_database(db, new_account):
    account = await new_account("cli")
    project_id = await _created_project(account)

    row = await db.fetchrow(
        "SELECT title, organization_id::text FROM research_projects WHERE id = $1::uuid",
        project_id,
    )
    assert (row["title"], row["organization_id"]) == ("cli journey", account.org_id)

    status = await research_cli(
        account.token, "-f", "json", "projects", "get", project_id
    )
    assert status.returncode == 0, status.stdout + status.stderr
    assert json.loads(status.stdout[status.stdout.index("{") :])["status"] == "pending"
    progress = await research_cli(account.token, "projects", "progress", project_id)
    assert progress.returncode == 0, progress.stdout + progress.stderr


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "P08: a project has no results to export (CLI exits 1: 'Results not "
        "found'); needs a launchable dry run (P02-A) and a durable results read "
        "surface (P08)"
    ),
)
async def test_cli_results_exports_csv(new_account):
    account = await new_account("cli")
    results = await research_cli(
        account.token,
        "-f",
        "csv",
        "projects",
        "results",
        await _created_project(account),
    )
    assert results.returncode == 0, results.stdout + results.stderr


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="P08: scope refine returns 501 (G27)"
)
async def test_cli_refine_updates_the_scope(new_account):
    account = await new_account("cli")
    refined = await research_cli(
        account.token,
        "projects",
        "refine",
        await _created_project(account),
        "-s",
        "region=EU",
    )
    assert refined.returncode == 0, refined.stdout + refined.stderr
