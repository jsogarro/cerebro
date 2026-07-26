"""Tests for the shared, process-level dotenv loading boundary."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.core.environment import PROJECT_ROOT, load_environment


def _write_dotenv(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_process_home_project_and_default_precedence(tmp_path: Path) -> None:
    project_env = _write_dotenv(
        tmp_path / "project.env",
        "\n".join(
            [
                "PROCESS_WINS=project-value",
                "HOME_WINS=project-value",
                "PROJECT_ONLY=project-value",
            ]
        ),
    )
    home_env = _write_dotenv(
        tmp_path / "home.env",
        "\n".join(
            [
                "PROCESS_WINS=home-value",
                "HOME_WINS=home-value",
                "HOME_ONLY=home-value",
            ]
        ),
    )
    environ = {"PROCESS_WINS": "process-value"}

    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )

    assert environ == {
        "PROCESS_WINS": "process-value",
        "HOME_WINS": "home-value",
        "PROJECT_ONLY": "project-value",
        "HOME_ONLY": "home-value",
    }
    assert environ.get("UNSET_VALUE", "default-value") == "default-value"


def test_missing_files_leave_environment_unchanged(tmp_path: Path) -> None:
    environ = {"EXISTING": "process-value"}

    load_environment(
        project_env_path=tmp_path / "missing-project.env",
        home_env_path=tmp_path / "missing-home.env",
        environ=environ,
    )

    assert environ == {"EXISTING": "process-value"}


def test_explicit_test_opt_out_does_not_read_dotenv(
    tmp_path: Path,
) -> None:
    project_env = _write_dotenv(
        tmp_path / "project.env",
        "MUST_NOT_LOAD=project-value\n",
    )
    environ = {"CEREBRO_DISABLE_DOTENV": "true"}

    load_environment(
        project_env_path=project_env,
        home_env_path=tmp_path / "missing-home.env",
        environ=environ,
    )

    assert "MUST_NOT_LOAD" not in environ


def test_explicit_empty_values_are_authoritative(tmp_path: Path) -> None:
    project_env = _write_dotenv(
        tmp_path / "project.env",
        "\n".join(
            [
                "PROCESS_EMPTY=project-value",
                "HOME_EMPTY=project-value",
                "PROJECT_EMPTY=",
                "IGNORED_WITHOUT_VALUE",
            ]
        ),
    )
    home_env = _write_dotenv(
        tmp_path / "home.env",
        "\n".join(
            [
                "PROCESS_EMPTY=home-value",
                "HOME_EMPTY=",
            ]
        ),
    )
    environ = {"PROCESS_EMPTY": ""}

    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )

    assert environ["PROCESS_EMPTY"] == ""
    assert environ["HOME_EMPTY"] == ""
    assert environ["PROJECT_EMPTY"] == ""
    assert "IGNORED_WITHOUT_VALUE" not in environ


def test_repeated_calls_are_idempotent(tmp_path: Path) -> None:
    project_env = _write_dotenv(tmp_path / "project.env", "VALUE=project-value\n")
    home_env = _write_dotenv(tmp_path / "home.env", "VALUE=home-value\n")
    environ: dict[str, str] = {}

    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )
    first_result = dict(environ)
    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )

    assert environ == first_result == {"VALUE": "home-value"}


def test_interpolation_uses_injected_mapping_not_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERPOLATION_SOURCE", "host-value")
    project_env = _write_dotenv(
        tmp_path / "project.env",
        "EXPANDED_VALUE=${INTERPOLATION_SOURCE}\n",
    )
    environ = {"INTERPOLATION_SOURCE": "injected-value"}

    load_environment(
        project_env_path=project_env,
        home_env_path=tmp_path / "missing-home.env",
        environ=environ,
    )

    assert environ["EXPANDED_VALUE"] == "injected-value"


def test_interpolation_resolves_after_cross_file_precedence_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCESS_SECRET", "host-must-not-leak")
    project_env = _write_dotenv(
        tmp_path / "project.env",
        "\n".join(
            [
                "PROJECT_SEGMENT=project",
                "HOME_SECRET=project-loses",
                "PROCESS_SECRET=project-loses",
                "HOME_ONLY_REFERENCE=${HOME_ONLY}",
                "HOME_OVERRIDDEN_REFERENCE=${HOME_SECRET}",
                "PROCESS_REFERENCE=${PROCESS_SECRET}",
            ]
        ),
    )
    home_env = _write_dotenv(
        tmp_path / "home.env",
        "\n".join(
            [
                "HOME_ONLY=home-only",
                "HOME_SECRET=home-wins",
                "CROSS_FILE_REFERENCE=${PROJECT_SEGMENT}/home",
            ]
        ),
    )
    environ = {"PROCESS_SECRET": "injected-process-wins"}

    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )

    assert environ["HOME_ONLY_REFERENCE"] == "home-only"
    assert environ["HOME_OVERRIDDEN_REFERENCE"] == "home-wins"
    assert environ["PROCESS_REFERENCE"] == "injected-process-wins"
    assert environ["CROSS_FILE_REFERENCE"] == "project/home"


def test_cross_file_interpolation_cycle_terminates_without_host_state(
    tmp_path: Path,
) -> None:
    project_env = _write_dotenv(tmp_path / "project.env", "FIRST=${SECOND}\n")
    home_env = _write_dotenv(tmp_path / "home.env", "SECOND=${FIRST}\n")
    environ: dict[str, str] = {}

    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environ,
    )

    assert environ == {"FIRST": "", "SECOND": ""}


def test_foreign_cwd_and_decoy_dotenv_do_not_affect_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.core.environment as environment

    fake_project = tmp_path / "fake-project"
    fake_home = tmp_path / "fake-home"
    foreign_cwd = tmp_path / "foreign-cwd"
    _write_dotenv(
        fake_project / ".env",
        'export DOTENV_SYNTAX_VALUE="project quoted value" # comment\n',
    )
    home_env = _write_dotenv(
        fake_home / ".env",
        "HOME_SYNTAX_VALUE='home quoted value'\n",
    )
    _write_dotenv(foreign_cwd / ".env", "DOTENV_SYNTAX_VALUE=decoy-value\n")
    environ: dict[str, str] = {}
    monkeypatch.setattr(environment, "PROJECT_ENV_PATH", fake_project / ".env")
    monkeypatch.chdir(foreign_cwd)

    load_environment(
        home_env_path=home_env,
        environ=environ,
    )

    assert environ == {
        "DOTENV_SYNTAX_VALUE": "project quoted value",
        "HOME_SYNTAX_VALUE": "home quoted value",
    }


def test_default_project_root_is_derived_from_module_location() -> None:
    import src.core.environment as environment

    assert Path(environment.__file__).resolve().parents[2] == PROJECT_ROOT
    assert environment.PROJECT_ENV_PATH == PROJECT_ROOT / ".env"


def test_loader_does_not_disclose_values(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_value = "loader-nondisclosure-sentinel"
    project_env = _write_dotenv(
        tmp_path / "project.env", f"NONDISCLOSURE_KEY={fake_value}\n"
    )
    environ: dict[str, str] = {}

    with caplog.at_level(logging.DEBUG):
        load_environment(
            project_env_path=project_env,
            home_env_path=tmp_path / "missing-home.env",
            environ=environ,
        )

    captured = capsys.readouterr()
    assert environ["NONDISCLOSURE_KEY"] == fake_value
    assert fake_value not in captured.out
    assert fake_value not in captured.err
    assert fake_value not in caplog.text
