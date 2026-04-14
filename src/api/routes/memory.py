"""API routes for memory system."""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

router = APIRouter(prefix="/api/v1/memory")


@router.get("/working/{session_id}")
async def get_working_memory(session_id: str):
    """Get working memory for session."""
    return {"session_id": session_id, "context": {}}


@router.post("/events")
async def record_event(event_data: dict):
    """Record episodic event."""
    return {"status": "recorded", "event_id": "uuid"}


@router.get("/events")
async def get_events(user_id: str, limit: int = 20):
    """Get recent events."""
    return {"events": []}


@router.get("/entities")
async def get_entities(query: str):
    """Search semantic entities."""
    return {"entities": []}


@router.get("/suggestions")
async def get_suggestions(user_id: str, current_query: str):
    """Get query suggestions."""
    return {"suggestions": []}