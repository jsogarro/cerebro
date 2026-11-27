"""Read-only listing of the trusted workflow catalogue."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.services.workflow_catalog import get_workflow_catalog
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_current_token

router = APIRouter()


class WorkflowSummary(BaseModel):
    workflow_id: str
    workflow_version: str
    name: str
    description: str
    task_types: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    routing_policy_id: str
    supports_dry_run: bool = True


class WorkflowCatalogResponse(BaseModel):
    catalog_hash: str
    workflows: list[WorkflowSummary]


@router.get("/workflows", response_model=WorkflowCatalogResponse)
async def list_workflows(
    _: TokenPayload = Depends(get_current_token),
) -> WorkflowCatalogResponse:
    """List launchable workflows; deployment configuration, never request input."""
    catalog = get_workflow_catalog()
    return WorkflowCatalogResponse(
        catalog_hash=catalog.catalog_hash,
        workflows=[
            WorkflowSummary(
                workflow_id=entry.workflow.workflow_definition_id,
                workflow_version=entry.workflow.workflow_version,
                name=entry.workflow.name,
                description=entry.workflow.description,
                task_types=list(entry.workflow.task_types),
                input_schema=entry.workflow.model_dump(mode="json")["input_schema"],
                output_schema=entry.workflow.model_dump(mode="json")["output_schema"],
                routing_policy_id=entry.routing_policy.routing_policy_id,
            )
            for entry in catalog.enabled()
        ],
    )
