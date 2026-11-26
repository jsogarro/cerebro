"""GEMINI_* environment variables must reach a service built from an explicit key."""

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.core.environment as shared_environment

    monkeypatch.setattr(shared_environment, "PROJECT_ENV_PATH", tmp_path / "a" / ".env")
    monkeypatch.setattr(shared_environment, "HOME_ENV_PATH", tmp_path / "b" / ".env")


def _service(**kwargs):
    from src.services.gemini_service import GeminiService

    with patch("src.services.gemini_service.ChatGoogleGenerativeAI"):
        return GeminiService(**kwargs)


def test_env_model_and_concurrency_apply_when_api_key_is_passed(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")
    monkeypatch.setenv("GEMINI_MAX_CONCURRENT", "7")

    service = _service(api_key="explicit-key")

    assert service.api_key == "explicit-key"
    assert service.model_name == "gemini-env-model"
    assert service.config.max_concurrent == 7


def test_explicit_arguments_override_env(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")
    monkeypatch.setenv("GEMINI_MAX_CONCURRENT", "7")

    service = _service(
        api_key="explicit-key", model_name="explicit-model", max_concurrent=3
    )

    assert service.model_name == "explicit-model"
    assert service.config.max_concurrent == 3


def test_defaults_agree_when_env_is_unset(monkeypatch) -> None:
    from src.services.gemini_config import GeminiConfig

    for name in ("GEMINI_MODEL", "GEMINI_MAX_CONCURRENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    assert (
        GeminiConfig.from_env().max_concurrent
        == GeminiConfig(api_key="k").max_concurrent
    )
