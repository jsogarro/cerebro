"""Primary Settings integration with the shared dotenv boundary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.core.config import Settings
from src.core.environment import load_environment


def _write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_primary_settings_has_no_cwd_relative_env_file() -> None:
    assert Settings.model_config.get("env_file") is None


def test_primary_settings_uses_process_home_project_precedence(
    tmp_path: Path,
) -> None:
    project = _write(
        tmp_path / "project.env",
        "MASR_DEFAULT_STRATEGY=cost_efficient\nMASR_CACHE_MAX_SIZE=111\n",
    )
    home = _write(
        tmp_path / "home.env",
        "MASR_DEFAULT_STRATEGY=quality_focused\nMASR_CACHE_MAX_SIZE=222\n",
    )
    host_cache_size = os.environ.get("MASR_CACHE_MAX_SIZE")
    environment = {"MASR_DEFAULT_STRATEGY": "speed_first"}

    load_environment(
        project_env_path=project,
        home_env_path=home,
        environ=environment,
    )
    loaded = Settings(_env_file=None, **environment)

    assert loaded.MASR_DEFAULT_STRATEGY == "speed_first"
    assert loaded.MASR_CACHE_MAX_SIZE == 222
    assert os.environ.get("MASR_CACHE_MAX_SIZE") == host_cache_size


def test_primary_settings_ignores_foreign_cwd_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEREBRO_DISABLE_DOTENV", "true")
    monkeypatch.delenv("MASR_DEFAULT_STRATEGY", raising=False)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _write(foreign / ".env", "MASR_DEFAULT_STRATEGY=adaptive\n")
    monkeypatch.chdir(foreign)

    loaded = Settings(_env_file=None)

    assert loaded.MASR_DEFAULT_STRATEGY == "balanced"


def test_primary_settings_allows_no_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEREBRO_DISABLE_DOTENV", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    loaded = Settings(_env_file=None)

    assert loaded.GEMINI_API_KEY is None
    assert loaded.OPENROUTER_API_KEY is None
    assert loaded.DEEPSEEK_API_KEY is None
