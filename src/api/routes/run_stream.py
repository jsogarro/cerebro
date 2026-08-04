"""Server-sent events for one run's durable event stream.

The HTTP counterpart to ``/ws/runs/{run_id}``. Both read the same persisted
``agent_run_events`` rows through the same tenant-scoped reader; only the
framing differs. SSE carries its resume position in the standard ``id:`` field
as well as in each frame's ``cursor``, so a browser ``EventSource`` resumes
automatically through ``Last-Event-ID`` while other clients can pass the
``cursor`` query parameter explicitly.

Entitlement is the caller's authenticated organization claim. A resume token
is a position, not a capability: it is honored only after the caller's own
token has been checked against the run, so replaying someone else's cursor
grants nothing.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from structlog import get_logger

from src.api.websocket.run_stream import (
    RunEventStreamReader,
    RunNotVisibleError,
    RunStreamAuthorizationError,
    RunStreamEntitlement,
)
from src.auth.models import TokenPayload
from src.core.contracts import RunEventCursor
from src.middleware.auth_middleware import get_current_token
from src.models.db.session import get_session_factory

logger = get_logger()
router = APIRouter()

SSE_DESTINATION = "sse"


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def stream_run_events(
    run_id: str,
    cursor: str | None = Query(
        None, description="Opaque resume token from a previous connection"
    ),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    token_payload: TokenPayload = Depends(get_current_token),
) -> StreamingResponse:
    """Stream a run's events as SSE, resuming from a cursor if one is given.

    Delivery is at-least-once. A reconnect, a relay retry, or a redelivery
    after a crash can present the same event again; clients must dedupe on
    each frame's ``idempotency_key``.
    """
    try:
        entitlement = RunStreamEntitlement.from_organization_claim(
            token_payload.organization_id, user_id=str(token_payload.sub)
        )
    except RunStreamAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(error)
        ) from error

    resume_token = cursor or last_event_id
    resume_cursor: RunEventCursor | None = None
    if resume_token:
        try:
            resume_cursor = RunEventCursor.decode(resume_token)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid resume cursor",
            ) from error

    try:
        session_factory = get_session_factory()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run event stream unavailable",
        ) from error

    reader = RunEventStreamReader(
        session_factory=session_factory,
        entitlement=entitlement,
        destination=SSE_DESTINATION,
    )

    # Authorize before returning the streaming response so an unentitled
    # caller gets a status code rather than an empty 200 stream. The same
    # error covers "no such run" and "another tenant's run", so run existence
    # outside the caller's organization is not observable.
    try:
        await reader.authorize(run_id)
    except RunNotVisibleError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        ) from error

    async def frames() -> AsyncIterator[str]:
        try:
            async for frame in reader.stream(run_id, cursor=resume_cursor):
                message = frame.to_message()
                yield (
                    f"id: {message['cursor']}\n"
                    f"event: {message['event_type']}\n"
                    f"data: {json.dumps(message, default=str)}\n\n"
                )
        except RunNotVisibleError:
            logger.info("Run stream closed: run no longer visible", run_id=run_id)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
