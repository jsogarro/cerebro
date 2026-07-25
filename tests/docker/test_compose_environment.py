"""Contract tests for the development-only Docker Compose wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from src.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "compose.sh"


@pytest.fixture
def fake_compose_project(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    project = tmp_path / "fake-project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "compose.sh"
    shutil.copy2(WRAPPER, wrapper)
    wrapper.chmod(0o755)

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -eu",
                'printf "%s\\0" "$@" > "$FAKE_DOCKER_ARGS_PATH"',
                'exit "${FAKE_DOCKER_EXIT_CODE:-0}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    args_path = tmp_path / "docker-args"
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.defpath}",
        "HOME": str(fake_home),
        "FAKE_DOCKER_ARGS_PATH": str(args_path),
        "FAKE_DOCKER_EXIT_CODE": "0",
    }
    return project, fake_home, args_path, env


def _run_wrapper(
    wrapper: Path, args_path: Path, env: dict[str, str], *arguments: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    result = subprocess.run(
        [str(wrapper), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    raw_args = args_path.read_bytes() if args_path.exists() else b""
    docker_args = [value.decode("utf-8") for value in raw_args.split(b"\0") if value]
    return result, docker_args


def test_wrapper_passes_absolute_project_then_home_env_files(
    fake_compose_project: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    project, fake_home, args_path, env = fake_compose_project
    (project / ".env").write_text("PROJECT_ONLY=fake-project-value\n")
    (fake_home / ".env").write_text("HOME_ONLY=fake-home-value\n")

    result, docker_args = _run_wrapper(
        project / "scripts" / "compose.sh",
        args_path,
        env,
        "up",
        "--detach",
    )

    assert result.returncode == 0
    assert docker_args == [
        "compose",
        "--project-directory",
        str(project),
        "--env-file",
        str(project / ".env"),
        "--env-file",
        str(fake_home / ".env"),
        "up",
        "--detach",
    ]


def test_wrapper_conditionally_omits_missing_env_files_and_preserves_arguments(
    fake_compose_project: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    project, _, args_path, env = fake_compose_project

    result, docker_args = _run_wrapper(
        project / "scripts" / "compose.sh",
        args_path,
        env,
        "run",
        "--rm",
        "api",
        "argument with spaces",
    )

    assert result.returncode == 0
    assert docker_args == [
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "api",
        "argument with spaces",
    ]


def test_wrapper_preserves_docker_exit_status(
    fake_compose_project: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    project, _, args_path, env = fake_compose_project
    env["FAKE_DOCKER_EXIT_CODE"] = "37"

    result, docker_args = _run_wrapper(
        project / "scripts" / "compose.sh", args_path, env, "ps"
    )

    assert result.returncode == 37
    assert docker_args[-1] == "ps"


def test_wrapper_has_no_secret_file_execution_or_merge_logic() -> None:
    content = WRAPPER.read_text(encoding="utf-8")

    for forbidden in ("source ", "eval ", "set -x", "cp ", "mount ", "mktemp "):
        assert forbidden not in content


def test_default_rendered_api_environment_constructs_primary_settings() -> None:
    """The synthetic credential-free Compose defaults must boot Settings."""

    render_environment = os.environ.copy()
    for name in (
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "GEMINI_API_KEY",
        "ADAPTIVE_ROUTING_ENABLED",
        "MEMORY_INFORMED_ROUTING_ENABLED",
    ):
        render_environment.pop(name, None)
    render_environment["CEREBRO_DISABLE_DOTENV"] = "true"

    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                str(REPO_ROOT / "docker-compose.yml"),
                "config",
            ],
            cwd=REPO_ROOT,
            env=render_environment,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("Docker Compose is unavailable")

    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    api_environment = rendered["services"]["api"]["environment"]

    loaded = Settings(_env_file=None, **api_environment)

    assert "JWT_SECRET_KEY" not in api_environment
    assert loaded.ENVIRONMENT == "development"
    assert len(loaded.SECRET_KEY) >= 32
    assert loaded.GEMINI_API_KEY in (None, "")


def test_compose_contracts_do_not_advertise_ignored_jwt_secret() -> None:
    for compose_name in ("docker-compose.yml", "docker-compose.production.yml"):
        content = (REPO_ROOT / compose_name).read_text(encoding="utf-8")
        assert "JWT_SECRET_KEY" not in content
