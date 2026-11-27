"""Deployment contracts: MASR runs in-process in the api container only."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PRODUCTION_COMPOSE_PATH = REPO_ROOT / "docker-compose.production.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _production_compose() -> dict:
    return yaml.safe_load(PRODUCTION_COMPOSE_PATH.read_text(encoding="utf-8"))


def test_default_api_has_no_standalone_masr_dependency_or_url() -> None:
    api = _compose()["services"]["api"]
    dependencies = api.get("depends_on", {})
    environment = api.get("environment", [])

    assert "masr-router" not in dependencies
    assert all(not item.startswith("MASR_SERVICE_URL=") for item in environment)


def test_default_api_keeps_thompson_dark_and_provider_key_optional() -> None:
    environment = _compose()["services"]["api"]["environment"]

    assert "ADAPTIVE_ROUTING_ENABLED=${ADAPTIVE_ROUTING_ENABLED:-false}" in environment
    assert "GEMINI_API_KEY=${GEMINI_API_KEY:-}" in environment


def test_production_default_topology_has_no_standalone_masr_dependency() -> None:
    compose = _production_compose()

    for service_name in ("api", "nginx"):
        service = compose["services"][service_name]
        dependencies = service.get("depends_on", {})
        environment = service.get("environment", [])
        assert "masr-router" not in dependencies
        assert all(not item.startswith("MASR_SERVICE_URL=") for item in environment)


def test_neither_compose_file_defines_a_standalone_masr_service() -> None:
    assert "masr-router" not in _compose()["services"]
    assert "masr-router" not in _production_compose()["services"]
