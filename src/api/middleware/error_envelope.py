"""Standard API error envelope helpers."""

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

ERROR_CODES_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "AUTHENTICATION_REQUIRED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMIT_EXCEEDED",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
}


def build_error_payload(
    *,
    code: str,
    message: str,
    details: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the standard API error response body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }


def _detail_to_error(
    detail: Any,
    status_code: int,
) -> tuple[str, str, Any]:
    if isinstance(detail, dict):
        code = str(
            detail.get("code")
            or detail.get("error_code")
            or ERROR_CODES_BY_STATUS.get(status_code, "API_ERROR")
        )
        message = str(detail.get("message") or detail.get("error") or "Request failed")
        details = detail.get("details", {})
        return code, message, details

    if isinstance(detail, str):
        return ERROR_CODES_BY_STATUS.get(status_code, "API_ERROR"), detail, {}

    return ERROR_CODES_BY_STATUS.get(status_code, "API_ERROR"), "Request failed", detail


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Render FastAPI HTTP exceptions with the standard error envelope."""
    code, message, details = _detail_to_error(exc.detail, exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_payload(code=code, message=message, details=details),
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Render request validation errors with the standard error envelope."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=build_error_payload(
            code="VALIDATION_ERROR",
            message="Invalid request",
            details={"errors": jsonable_encoder(exc.errors())},
        ),
    )


async def internal_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Render unexpected errors with the standard error envelope."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=build_error_payload(
            code="INTERNAL_SERVER_ERROR",
            message="Internal server error",
        ),
    )
