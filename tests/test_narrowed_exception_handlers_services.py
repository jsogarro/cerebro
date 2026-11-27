"""Handlers in services and ai_brain catch only the failures they can handle.

Each test pairs an error that is still tolerated with one that now propagates.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from src.ai_brain.experimentation.core import adaptive_allocation_engine as engine_mod
from src.ai_brain.experimentation.eval import adaptive_routing_promotion as gate
from src.services.cache.cache_manager import CacheManager
from src.services.exporters.docx_exporter import DOCXExporter
from src.services.gemini_service import GeminiService
from src.services.report_generator import ReportGenerator
from src.services.report_storage import ReportStorageService
from src.services.template_renderer import TemplateRenderer


def _cache_manager(pipeline_error: Exception) -> CacheManager:
    pipe = MagicMock()
    pipe.execute = AsyncMock(side_effect=pipeline_error)
    redis = MagicMock()
    redis.pipeline.return_value = pipe
    return CacheManager(redis)


class TestCacheManager:
    async def test_redis_error_is_a_miss(self) -> None:
        cache = _cache_manager(RedisError("down"))
        assert await cache.get("k") is None
        assert cache.metrics["errors"] == 1

    async def test_unexpected_error_propagates(self) -> None:
        cache = _cache_manager(RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            await cache.get("k")

    async def test_batch_set_unexpected_error_propagates(self) -> None:
        cache = _cache_manager(RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            await cache.batch_set({"k": {"a": 1}})

    async def test_warm_cache_generator_error_propagates(self) -> None:
        async def failing_source():
            yield "k", {"a": 1}
            raise RuntimeError("source failed")

        cache = CacheManager(MagicMock())
        cache.batch_set = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await cache.warm_cache(failing_source(), batch_size=10)


def _gemini(cache_client: MagicMock) -> GeminiService:
    service = GeminiService.__new__(GeminiService)
    service._cache_enabled = True
    service.cache_client = cache_client
    return service


class TestGeminiCache:
    async def test_redis_error_is_a_miss(self) -> None:
        client = MagicMock(get=AsyncMock(side_effect=RedisError("down")))
        assert await _gemini(client)._get_cached_response("k") is None

    async def test_unexpected_read_error_propagates(self) -> None:
        client = MagicMock(get=AsyncMock(side_effect=RuntimeError("bug")))
        with pytest.raises(RuntimeError):
            await _gemini(client)._get_cached_response("k")

    async def test_unexpected_write_error_propagates(self) -> None:
        client = MagicMock(set=AsyncMock(side_effect=RuntimeError("bug")))
        service = _gemini(client)
        service.config = SimpleNamespace(cache_ttl=1)
        with pytest.raises(RuntimeError):
            await service._set_cached_response("k", {"a": 1})


def test_docx_style_setup_failure_propagates() -> None:
    styles = MagicMock()
    styles.add_style.side_effect = ValueError("style exists")
    exporter = DOCXExporter.__new__(DOCXExporter)
    with pytest.raises(ValueError):
        exporter._setup_document_styles(SimpleNamespace(styles=styles))  # type: ignore[arg-type]


async def test_save_outputs_unexpected_error_propagates(tmp_path) -> None:
    generator = ReportGenerator.__new__(ReportGenerator)
    generator.settings = SimpleNamespace(report_storage_path=str(tmp_path))
    generator.format_config = SimpleNamespace(get_file_extension=lambda _f: ".bin")
    bad_output = SimpleNamespace(is_binary=True, content=123, encoding="utf-8")
    with pytest.raises(AttributeError):
        await generator._save_outputs(  # type: ignore[arg-type]
            SimpleNamespace(id="r1"), {"pdf": bad_output}
        )


def _storage() -> ReportStorageService:
    return ReportStorageService.__new__(ReportStorageService)


class TestReportStorage:
    async def _content(self, error: Exception):
        service = _storage()
        report_format = SimpleNamespace(
            get_content=MagicMock(side_effect=error), mime_type="text/html"
        )
        service.format_repo = SimpleNamespace(
            get_by_report_and_format=AsyncMock(return_value=report_format)
        )
        return await service.retrieve_report_content(
            "r",
            "html",
            organization_id="o",  # type: ignore[arg-type]
        )

    async def test_missing_content_is_none(self) -> None:
        assert await self._content(FileNotFoundError("gone")) is None

    async def test_unexpected_content_error_propagates(self) -> None:
        with pytest.raises(LookupError):
            await self._content(LookupError("bad encoding"))

    async def test_statistics_unexpected_error_propagates(self) -> None:
        service = _storage()
        service.report_repo = SimpleNamespace(
            get_report_statistics=AsyncMock(return_value={}),
            get_storage_paths=AsyncMock(side_effect=RuntimeError("bug")),
        )
        with pytest.raises(RuntimeError):
            await service.get_storage_statistics(organization_id="o")

    async def _integrity(self, error: Exception) -> dict:
        service = _storage()
        fmt = SimpleNamespace(id=1, format_type="html", file_path=None, file_hash="h")
        service.retrieve_report = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(formats=[fmt])
        )
        service.format_repo = SimpleNamespace(
            verify_format_integrity=AsyncMock(side_effect=error)
        )
        return await service.verify_report_integrity("r", organization_id="o")  # type: ignore[arg-type]

    async def test_integrity_database_error_is_recorded_per_format(self) -> None:
        result = await self._integrity(SQLAlchemyError("db"))
        assert result["errors"]

    async def test_integrity_unexpected_error_propagates(self) -> None:
        with pytest.raises(RuntimeError):
            await self._integrity(RuntimeError("bug"))


def test_template_validation_unexpected_error_propagates() -> None:
    renderer = TemplateRenderer.__new__(TemplateRenderer)
    renderer.env = MagicMock()
    renderer.env.get_template.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        renderer.validate_template("t.html")


class TestAllocationUpdates:
    def _engine(self, error: Exception) -> engine_mod.AdaptiveAllocationEngine:
        engine = engine_mod.AdaptiveAllocationEngine()
        engine.active_experiments["e"] = SimpleNamespace(  # type: ignore[assignment]
            strategy=engine_mod.AllocationStrategy.ADAPTIVE_BANDIT
        )
        engine.bandit_optimizers["e"] = MagicMock()
        engine._calculate_optimal_allocation = AsyncMock(side_effect=error)  # type: ignore[method-assign]
        return engine

    async def test_numeric_failure_skips_the_experiment(self) -> None:
        assert await self._engine(ZeroDivisionError()).update_allocations() == []

    async def test_unexpected_error_propagates(self) -> None:
        with pytest.raises(RuntimeError):
            await self._engine(RuntimeError("bug")).update_allocations()


class TestPromotionGateMain:
    def test_unreadable_criteria_yields_a_report(self, tmp_path) -> None:
        output = tmp_path / "report.json"
        code = gate.main(
            ["--criteria", str(tmp_path / "missing.json"), "--output", str(output)]
        )
        assert output.exists()
        assert isinstance(code, int)

    def test_unexpected_error_propagates(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            gate, "evaluate_promotion", AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            gate.main(["--output", str(tmp_path / "report.json")])
