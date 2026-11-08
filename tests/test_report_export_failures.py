"""Export failures must fail the format and the report, not store placeholders."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.routes.reports import _generate_report_task
from src.models.report import (
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
)
from src.services.exporters import DOCXExportError, PDFExportError
from src.services.report_config import ReportFormatConfig, ReportSettings
from src.services.report_output_generator import ReportOutputGenerator


def _generator(**settings: bool) -> ReportOutputGenerator:
    report_settings = ReportSettings(**settings)
    return ReportOutputGenerator(
        report_settings,
        ReportFormatConfig(report_settings),
        MagicMock(),
    )


def _report() -> Report:
    return Report(
        id="r1",
        title="Title & 100% {x}",
        query="Query _ # $",
        configuration=ReportConfiguration(),
    )


async def test_pdf_exporter_failure_is_raised_not_replaced_by_placeholder() -> None:
    generator = _generator(enable_pdf_generation=True)
    generator.generate_html = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(content="<html></html>")
    )
    exporter = MagicMock()
    exporter.export_to_pdf.side_effect = PDFExportError("no backend")
    generator._pdf_exporter = exporter

    with pytest.raises(PDFExportError):
        await generator.generate_pdf(_report())


async def test_disabled_pdf_generation_is_an_error_not_a_placeholder() -> None:
    generator = _generator(enable_pdf_generation=False)

    with pytest.raises(PDFExportError):
        await generator.generate_pdf(_report())


async def test_docx_exporter_failure_is_raised_not_replaced_by_placeholder() -> None:
    generator = _generator()
    exporter = MagicMock()
    exporter.export_to_docx.side_effect = DOCXExportError("no backend")
    generator._docx_exporter = exporter

    with pytest.raises(DOCXExportError):
        await generator.generate_docx(_report())


@pytest.mark.parametrize("parallel", [True, False])
async def test_one_failed_format_fails_the_whole_request(parallel: bool) -> None:
    generator = _generator(
        parallel_generation=parallel,
        enable_pdf_generation=False,
    )

    with pytest.raises(PDFExportError):
        await generator.generate_formats(
            _report(), [ReportFormat.MARKDOWN, ReportFormat.PDF]
        )


async def test_report_task_marks_report_failed_and_stores_nothing() -> None:
    report_id, user_id, org_id = uuid4(), uuid4(), uuid4()
    session = AsyncMock()
    repo = MagicMock()
    repo.update_report_status = AsyncMock()
    storage = MagicMock()
    storage.store_report = AsyncMock()
    generator = MagicMock()
    generator._extract_input_data = AsyncMock()
    generator._build_report_structure = AsyncMock()
    generator._generate_formats = AsyncMock(side_effect=PDFExportError("no backend"))

    @asynccontextmanager
    async def session_ctx():
        yield session

    request = ReportGenerationRequest(
        project_id=None,
        workflow_data={},
        configuration=ReportConfiguration(),
        formats=[ReportFormat.PDF],
        save_to_storage=True,
        notify_completion=False,
    )
    with (
        patch("src.api.routes.reports.get_session_factory", lambda: session_ctx),
        patch(
            "src.api.routes.reports.get_report_services",
            return_value=(generator, storage, repo, MagicMock()),
        ),
    ):
        await _generate_report_task(
            generator, report_id, request, user_id, None, org_id
        )

    storage.store_report.assert_not_awaited()
    statuses = [c.args[1] for c in repo.update_report_status.await_args_list]
    assert statuses == ["generating", "failed"]
    assert "no backend" in repo.update_report_status.await_args_list[-1].args[2]
