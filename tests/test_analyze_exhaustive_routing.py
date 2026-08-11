"""Regression test for the /query/analyze exhaustive routing branch.

`intelligent_analysis_query` routes `RoutingStrategy.QUALITY_FOCUSED` when
`analysis_type == "exhaustive"`, but the `AnalysisRequest.analysis_type` field
pattern did not allow "exhaustive" — so the value was rejected at validation
(422) and the branch was dead. Adding "exhaustive" to the pattern makes the
branch reachable so callers can explicitly request quality-focused analysis.
"""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.ai_brain.router.masr import RoutingStrategy
from src.api.routes import query_api
from src.api.routes.query_api import AnalysisRequest, intelligent_analysis_query
from src.middleware.tenant_context import TenantContext

# The query routes require an authenticated tenant.
TENANT = TenantContext(
    user_id="user-1", organization_id="11111111-1111-1111-1111-111111111111"
)


def test_exhaustive_analysis_type_is_accepted() -> None:
    # Fails on the pre-fix pattern (raises ValidationError).
    request = AnalysisRequest(query="deep dive", analysis_type="exhaustive")
    assert request.analysis_type == "exhaustive"


async def test_exhaustive_routes_quality_focused() -> None:
    captured = {}

    async def _capture(
        intelligent_request,
        background_tasks,
        execution_service=None,
        tenant_context=None,
    ):
        captured["req"] = intelligent_request
        return AsyncMock()

    request = AnalysisRequest(query="deep dive", analysis_type="exhaustive")
    with patch.object(query_api, "intelligent_research_query", new=_capture):
        await intelligent_analysis_query(request, AsyncMock(), tenant_context=TENANT)

    assert captured["req"].routing_strategy == RoutingStrategy.QUALITY_FOCUSED


async def test_non_exhaustive_defers_routing_to_masr() -> None:
    captured = {}

    async def _capture(
        intelligent_request,
        background_tasks,
        execution_service=None,
        tenant_context=None,
    ):
        captured["req"] = intelligent_request
        return AsyncMock()

    request = AnalysisRequest(query="normal", analysis_type="comprehensive")
    with patch.object(query_api, "intelligent_research_query", new=_capture):
        await intelligent_analysis_query(request, AsyncMock(), tenant_context=TENANT)

    # Non-exhaustive requests leave strategy unset so MASR auto-selects.
    assert captured["req"].routing_strategy is None


def test_invalid_analysis_type_still_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(query="x", analysis_type="bogus")
