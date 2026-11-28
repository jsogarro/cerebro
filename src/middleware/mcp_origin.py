"""Origin header validation for the Streamable HTTP ``/mcp`` endpoint.

Defends local and browser-adjacent MCP servers against DNS-rebinding and
cross-origin browser requests per the MCP Streamable HTTP specification.
Requests without an ``Origin`` header (CLI and non-browser MCP clients) pass
through untouched; requests carrying a disallowed or duplicate ``Origin``
receive ``403`` before CORS or authentication runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.api.middleware.error_envelope import build_error_payload
from src.core.config import settings
from src.models.db.audit_log import AuditEventType
from src.security.audit_middleware import audit_rejection

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _parse_origin(raw: str) -> tuple[str, str, int] | None:
    """Parse a strict ``scheme://host[:port]`` origin, or return ``None``."""
    if not raw or any(ch <= " " or ch == "\x7f" for ch in raw):
        return None
    if "?" in raw or "#" in raw:
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not raw.lower().startswith(f"{scheme}://"):
        return None
    if not parsed.hostname or parsed.netloc.endswith(":"):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path or parsed.query or parsed.fragment:
        return None
    effective_port = port if port is not None else _DEFAULT_PORTS[scheme]
    return (scheme, parsed.hostname.lower(), effective_port)


def is_allowed_mcp_origin(origin: str, cors_origins: Sequence[str]) -> bool:
    """Return whether ``origin`` is an allowed loopback or configured origin."""
    parsed = _parse_origin(origin)
    if parsed is None:
        return False
    scheme, host, _port = parsed
    if scheme in _DEFAULT_PORTS and host in _LOOPBACK_HOSTS:
        return True
    for configured in cors_origins:
        if configured == "*":
            continue
        allowed = _parse_origin(configured)
        if allowed is not None and allowed == parsed:
            return True
    return False


def _is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


class McpOriginMiddleware(BaseHTTPMiddleware):
    """Reject browser requests to ``/mcp`` with disallowed or duplicate ``Origin``."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_mcp_path(request.url.path):
            origins = request.headers.getlist("origin")
            if len(origins) > 1 or (
                len(origins) == 1
                and not is_allowed_mcp_origin(origins[0], settings.CORS_ORIGINS)
            ):
                audit_rejection(
                    request,
                    AuditEventType.UNAUTHORIZED_ACCESS,
                    "disallowed mcp origin",
                )
                return JSONResponse(
                    status_code=403,
                    content=build_error_payload(
                        code="FORBIDDEN",
                        message="Origin not allowed for /mcp",
                    ),
                )
        return await call_next(request)


__all__ = ["McpOriginMiddleware", "is_allowed_mcp_origin"]
