"""User privacy and account data routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem
from src.models.db.research_project import ResearchProject
from src.models.db.session import get_session
from src.models.db.user import User

router = APIRouter(prefix="/users", tags=["users"])


async def get_memory_system(request: Request) -> MultiTierMemorySystem | None:
    """Return the app-level memory system when one is attached."""

    memory_system = getattr(request.app.state, "memory_system", None)
    if isinstance(memory_system, MultiTierMemorySystem):
        return memory_system
    return None


@router.delete("/{user_id}/gdpr")
async def delete_user_gdpr(
    user_id: UUID,
    db: AsyncSession = Depends(get_session),
    memory_system: MultiTierMemorySystem | None = Depends(get_memory_system),
) -> dict[str, Any]:
    """Delete user data from database records and memory tiers."""

    user_id_text = str(user_id)
    project_result = await db.execute(
        delete(ResearchProject).where(ResearchProject.user_id == user_id_text)
    )
    user_result = await db.execute(delete(User).where(User.id == user_id))

    memory_deleted = (
        await memory_system.purge_user_data(user_id_text)
        if memory_system is not None
        else {"working_memory": 0, "episodic_memory": 0}
    )

    return {
        "user_id": user_id_text,
        "deleted": {
            # SQLAlchemy 2.x: Result.rowcount is valid for DELETE/UPDATE; mypy stub gap.
            "users": user_result.rowcount or 0,  # type: ignore[attr-defined]
            "research_projects": project_result.rowcount or 0,  # type: ignore[attr-defined]
            "memory": memory_deleted,
        },
    }


__all__ = ["get_memory_system", "router"]
