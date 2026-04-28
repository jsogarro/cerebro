"""Characterization tests for Dockerfile public contracts.

These tests lock in the runtime behavior of each Dockerfile so that the
docker-hardening refactor (`.dockerignore` + SHA256 base-image pinning) cannot
silently change CMD, EXPOSE, USER, ENV, HEALTHCHECK, or COPY targets.

Tests are pure-Python parsers — they read the Dockerfile text and verify
structural invariants. No docker daemon required. Run via `pytest tests/docker/`.

If a test fails AFTER the refactor, the refactor changed observable behavior.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def _from_lines(content: str) -> list[str]:
    return [
        line.strip()
        for line in content.splitlines()
        if re.match(r"^\s*FROM\s+", line, re.IGNORECASE)
    ]


def _copy_from_lines(content: str) -> list[str]:
    return [
        line.strip()
        for line in content.splitlines()
        if re.match(r"^\s*COPY\s+--from=", line, re.IGNORECASE)
    ]


def _instruction_lines(content: str, instruction: str) -> list[str]:
    pattern = re.compile(rf"^\s*{instruction}\b", re.IGNORECASE)
    return [line.strip() for line in content.splitlines() if pattern.match(line)]


# --------------------------------------------------------------------------- #
# Root Dockerfile (multi-stage Python)
# --------------------------------------------------------------------------- #


class TestRootDockerfileContract:
    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read("Dockerfile")

    def test_has_four_stages(self, content: str) -> None:
        froms = _from_lines(content)
        assert len(froms) == 4, f"Expected 4 FROM stages, got {len(froms)}: {froms}"

    def test_stages_named_base_development_builder_production(self, content: str) -> None:
        names = re.findall(r"AS\s+(\w+)", content, re.IGNORECASE)
        assert names == ["base", "development", "builder", "production"], names

    def test_base_image_is_python_311_slim(self, content: str) -> None:
        froms = _from_lines(content)
        for f in [froms[0], froms[3]]:
            assert "python:3.11-slim" in f, f"Base image drift: {f}"

    def test_uv_copied_from_astral(self, content: str) -> None:
        copy_froms = _copy_from_lines(content)
        uv_lines = [line for line in copy_froms if "astral-sh/uv" in line]
        assert len(uv_lines) == 1, f"Expected exactly one uv COPY --from=, got {uv_lines}"
        assert "/uv /usr/local/bin/uv" in uv_lines[0]

    def test_production_runs_as_non_root_app_user(self, content: str) -> None:
        users = _instruction_lines(content, "USER")
        assert any(u.split()[1] == "app" for u in users), f"Production must run as 'app', got {users}"

    def test_workdir_is_app(self, content: str) -> None:
        workdirs = _instruction_lines(content, "WORKDIR")
        assert all(w.endswith("/app") for w in workdirs), workdirs

    def test_expose_8000(self, content: str) -> None:
        assert "EXPOSE 8000" in content

    def test_healthcheck_curls_health_endpoint(self, content: str) -> None:
        healthchecks = _instruction_lines(content, "HEALTHCHECK")
        assert len(healthchecks) == 1
        assert "/health" in content and "curl" in healthchecks[0].lower() or "curl" in content

    def test_production_cmd_unchanged(self, content: str) -> None:
        # Production stage uvicorn launch with 4 workers
        assert (
            'CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]'
            in content
        )

    def test_development_cmd_unchanged(self, content: str) -> None:
        assert (
            'CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]'
            in content
        )

    def test_required_env_vars_set(self, content: str) -> None:
        for var in ("PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "UV_SYSTEM_PYTHON=1"):
            assert var in content, f"Missing required ENV: {var}"

    def test_apt_runtime_deps_present_on_production(self, content: str) -> None:
        # production stage installs curl
        prod_section = content.split("FROM python:3.11-slim as production")[-1]
        assert "curl" in prod_section


# --------------------------------------------------------------------------- #
# docker/Dockerfile.masr (MASR service)
# --------------------------------------------------------------------------- #


class TestMasrDockerfileContract:
    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read("docker/Dockerfile.masr")

    def test_single_stage_named_base(self, content: str) -> None:
        names = re.findall(r"AS\s+(\w+)", content, re.IGNORECASE)
        assert names == ["base"], names

    def test_base_image_is_python_311_slim(self, content: str) -> None:
        froms = _from_lines(content)
        assert len(froms) == 1
        assert "python:3.11-slim" in froms[0]

    def test_uv_copied_from_astral(self, content: str) -> None:
        copy_froms = _copy_from_lines(content)
        uv_lines = [line for line in copy_froms if "astral-sh/uv" in line]
        assert len(uv_lines) == 1
        assert "/uv /usr/local/bin/uv" in uv_lines[0]

    def test_runs_as_non_root_masr_user(self, content: str) -> None:
        users = _instruction_lines(content, "USER")
        assert any(u.split()[1] == "masr" for u in users), users

    def test_workdir_is_app(self, content: str) -> None:
        workdirs = _instruction_lines(content, "WORKDIR")
        assert all(w.endswith("/app") for w in workdirs), workdirs

    def test_expose_9100(self, content: str) -> None:
        assert "EXPOSE 9100" in content

    def test_healthcheck_present(self, content: str) -> None:
        healthchecks = _instruction_lines(content, "HEALTHCHECK")
        assert len(healthchecks) == 1
        assert "9100/health" in content

    def test_cmd_starts_masr_service(self, content: str) -> None:
        assert 'CMD ["python", "-m", "src.ai_brain.router.masr_service"]' in content

    def test_required_env_vars_set(self, content: str) -> None:
        for var in (
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONUNBUFFERED=1",
            "UV_SYSTEM_PYTHON=1",
            "PYTHONPATH=/app",
        ):
            assert var in content, f"Missing required ENV: {var}"

    def test_apt_packages_unchanged(self, content: str) -> None:
        # MASR-specific deps must not be silently dropped
        for pkg in ("gcc", "g++", "curl", "git", "libopenblas-dev", "liblapack-dev", "netcat-traditional"):
            assert pkg in content, f"Missing apt package: {pkg}"

    def test_data_and_logs_dirs_created(self, content: str) -> None:
        assert "/app/data/masr" in content
        assert "/app/logs" in content


# --------------------------------------------------------------------------- #
# cerebro/web/Dockerfile (React frontend + Nginx)
# --------------------------------------------------------------------------- #


class TestWebDockerfileContract:
    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read("cerebro/web/Dockerfile")

    def test_two_stages_builder_then_runtime(self, content: str) -> None:
        froms = _from_lines(content)
        assert len(froms) == 2, f"Expected 2 stages, got {froms}"

    def test_builder_uses_node_20_alpine(self, content: str) -> None:
        froms = _from_lines(content)
        assert "node:20-alpine" in froms[0]

    def test_runtime_uses_nginx_alpine(self, content: str) -> None:
        froms = _from_lines(content)
        assert "nginx:alpine" in froms[1]

    def test_builder_named_builder(self, content: str) -> None:
        names = re.findall(r"AS\s+(\w+)", content, re.IGNORECASE)
        assert "builder" in names, names

    def test_npm_ci_install(self, content: str) -> None:
        assert "npm ci" in content
        assert "package.json package-lock.json" in content

    def test_vite_api_url_arg_preserved(self, content: str) -> None:
        assert "ARG VITE_API_URL" in content
        assert "ENV VITE_API_URL=$VITE_API_URL" in content

    def test_npm_build_invoked(self, content: str) -> None:
        assert "npm run build" in content

    def test_dist_copied_to_nginx_html(self, content: str) -> None:
        assert "COPY --from=builder /app/dist /usr/share/nginx/html" in content

    def test_default_nginx_assets_removed(self, content: str) -> None:
        assert "rm -rf /usr/share/nginx/html/*" in content

    def test_nginx_conf_copied(self, content: str) -> None:
        assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in content

    def test_expose_80(self, content: str) -> None:
        assert "EXPOSE 80" in content

    def test_cmd_starts_nginx_in_foreground(self, content: str) -> None:
        assert 'CMD ["nginx", "-g", "daemon off;"]' in content
