"""Contracts for the default local five-service Compose stack.

These tests deliberately inspect the repository entrypoints that developers
run. A rendered service list alone is not enough: a container can be declared,
exit successfully without serving anything, or prevent nginx from loading its
configuration before a request reaches it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
ENTRYPOINT_PATH = REPO_ROOT / "docker" / "entrypoint.sh"
NGINX_PATH = REPO_ROOT / "docker" / "nginx" / "nginx.conf"

EXPECTED_DEFAULT_SERVICES = {
    "postgres",
    "redis",
    "api",
    "web",
    "nginx",
}


def _compose_services() -> dict[str, dict]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"]


def _default_services() -> dict[str, dict]:
    return {
        name: service
        for name, service in _compose_services().items()
        if not service.get("profiles")
    }


def _command_text(command: object) -> str:
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


def _nginx_upstream_targets() -> dict[str, list[tuple[str, int]]]:
    upstream_block = re.compile(r"upstream\s+(\w+)\s*\{(.*?)\}", re.DOTALL)
    upstream_server = re.compile(r"^\s*server\s+([A-Za-z0-9_.-]+):(\d+)", re.MULTILINE)
    content = NGINX_PATH.read_text(encoding="utf-8")
    return {
        name: [(host, int(port)) for host, port in upstream_server.findall(body)]
        for name, body in upstream_block.findall(content)
    }


def test_default_topology_contains_exactly_five_startable_services() -> None:
    """The default profile must be the complete local application stack."""

    assert set(_default_services()) == EXPECTED_DEFAULT_SERVICES


def test_compose_resources_are_project_scoped_for_parallel_checkouts() -> None:
    """Separate worktrees must not collide with another Compose project."""

    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    for service_name, service in compose["services"].items():
        assert "container_name" not in service, (
            f"{service_name} fixes container_name and can collide with another "
            "Cerebro checkout"
        )

    assert "name" not in compose["networks"]["research-network"]
    for volume in compose["volumes"].values():
        assert "name" not in (volume or {})


def test_default_services_have_real_process_or_endpoint_healthchecks() -> None:
    """Every default service must prove the process it claims to represent."""

    services = _default_services()
    expected_checks = {
        "postgres": ("pg_isready", "research_db"),
        "redis": ("redis-cli", "ping"),
        "api": ("curl", "http://localhost:8000/health"),
        "web": ("wget", "http://127.0.0.1:8080/"),
        "nginx": ("wget", "http://localhost/health"),
    }

    for service_name, expected_fragments in expected_checks.items():
        healthcheck = services[service_name].get("healthcheck")
        assert healthcheck, f"{service_name} has no healthcheck"
        check_text = _command_text(healthcheck["test"])
        assert check_text != "exit 0", f"{service_name} has a fake healthcheck"
        for fragment in expected_fragments:
            assert fragment in check_text, (
                f"{service_name} healthcheck {check_text!r} does not prove {fragment!r}"
            )


def test_api_uses_the_migration_entrypoint_and_explicit_uvicorn_command() -> None:
    """The API container must run migrations before its real ASGI process."""

    api = _compose_services()["api"]

    assert api["entrypoint"] == ["/entrypoint.sh"]
    assert api["command"] == [
        "uvicorn",
        "src.api.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]


def test_development_image_installs_the_mcp_extra_at_build_time() -> None:
    """The development image supplies MCP dependencies before container start."""

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert 'RUN uv pip install -e ".[dev,mcp]"' in dockerfile


def test_nginx_does_not_expose_mcp_or_define_an_mcp_upstream() -> None:
    """nginx must not provide a public MCP route or upstream."""

    targets = _nginx_upstream_targets()
    expected_targets = {
        ("api", 8000),
        ("web", 8080),
    }
    actual_targets = {
        target for upstream_targets in targets.values() for target in upstream_targets
    }

    assert actual_targets == expected_targets
    assert "temporal-ui" not in NGINX_PATH.read_text(encoding="utf-8")

    nginx = NGINX_PATH.read_text(encoding="utf-8")
    assert "mcp_server" not in targets
    assert not re.search(r"location\s+/mcp(?:/|\s|\{)", nginx)
    assert "proxy_pass http://mcp_server" not in nginx


def _run_entrypoint(
    tmp_path: Path, *, migration_exit_code: int = 0
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events_path = tmp_path / "events"

    (fake_bin / "alembic").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' migration >> '{events_path}'\n"
        f"exit {migration_exit_code}\n",
        encoding="utf-8",
    )
    (fake_bin / "server").write_text(
        f"#!/bin/sh\nprintf '%s\\n' server >> '{events_path}'\n",
        encoding="utf-8",
    )
    for executable in (fake_bin / "alembic", fake_bin / "server"):
        executable.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        ["sh", str(ENTRYPOINT_PATH), "server"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_migration_entrypoint_reports_failure_and_never_starts_application(
    tmp_path: Path,
) -> None:
    """A failed migration must retain its status and block application start."""

    result = _run_entrypoint(tmp_path, migration_exit_code=19)
    events = (tmp_path / "events").read_text(encoding="utf-8").splitlines()

    assert result.returncode == 19
    assert events == ["migration"]
    assert "Database migrations failed" in result.stderr


def test_migration_entrypoint_logs_completion_before_starting_application(
    tmp_path: Path,
) -> None:
    """Successful migrations must be observable before the exec handoff."""

    result = _run_entrypoint(tmp_path)
    events = (tmp_path / "events").read_text(encoding="utf-8").splitlines()

    assert result.returncode == 0
    assert events == ["migration", "server"]
    assert "Database migrations completed" in result.stdout
    assert "Starting application" in result.stdout
