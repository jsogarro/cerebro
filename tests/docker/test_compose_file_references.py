"""Every file a compose file mounts must exist, and the smoke scripts must match the stack."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.production.yml"]
# The remaining declared secrets are supplied by the operator through the
# ``${VAR:?}`` interpolation, so only repo-relative paths are checked.
SMOKE_SCRIPTS = ["scripts/smoke_test.sh", "scripts/smoke_test_postgres.sh"]


def _load(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_bind_mount_sources_exist(name: str) -> None:
    missing = []
    for service, spec in _load(name)["services"].items():
        for volume in spec.get("volumes", []):
            source = str(volume).split(":", 1)[0]
            if source.startswith("./") and not (REPO_ROOT / source).exists():
                missing.append(f"{service}: {source}")
    assert not missing


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_secret_files_exist(name: str) -> None:
    missing = [
        f"{key}: {spec['file']}"
        for key, spec in _load(name).get("secrets", {}).items()
        if str(spec["file"]).startswith("./")
        and not (REPO_ROOT / spec["file"]).exists()
    ]
    assert not missing


def test_production_secrets_are_all_used_by_a_service() -> None:
    compose = _load("docker-compose.production.yml")
    used = {
        secret["source"] if isinstance(secret, dict) else secret
        for spec in compose["services"].values()
        for secret in spec.get("secrets", [])
    }
    assert set(compose.get("secrets", {})) == used


def test_production_api_sets_no_gunicorn_variables() -> None:
    api = _load("docker-compose.production.yml")["services"]["api"]
    assert not [e for e in api["environment"] if str(e).startswith("GUNICORN_")]


@pytest.mark.parametrize("script", SMOKE_SCRIPTS)
def test_query_research_smoke_step_sends_the_bearer_token(script: str) -> None:
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    step = text.split("/api/v1/query/research", 1)[1].split("check ", 1)[0]
    assert "Bearer ${TOKEN}" in step
    assert "(public)" not in text


def test_postgres_smoke_script_detects_the_api_service_by_compose_name() -> None:
    text = (REPO_ROOT / "scripts/smoke_test_postgres.sh").read_text(encoding="utf-8")
    assert "research-api" not in text
