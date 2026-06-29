"""Request-level LLM cost drift tracking middleware."""

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.observability import (
    record_llm_request_cost_drift,
    reset_llm_request_cost_tracking,
    start_llm_request_cost_tracking,
)

DEFAULT_COST_DRIFT_THRESHOLD_RATIO = 0.2


class LLMCostDriftMiddleware(BaseHTTPMiddleware):
    """Compare MASR-estimated request cost with actual LLM provider cost."""

    def __init__(
        self,
        app: Any,
        threshold_ratio: float = DEFAULT_COST_DRIFT_THRESHOLD_RATIO,
    ) -> None:
        super().__init__(app)
        self.threshold_ratio = threshold_ratio

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        token = start_llm_request_cost_tracking()
        try:
            response = await call_next(request)
            record_llm_request_cost_drift(
                method=request.method,
                route=_route_label(request),
                threshold_ratio=self.threshold_ratio,
            )
            return response
        finally:
            reset_llm_request_cost_tracking(token)


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return request.url.path
