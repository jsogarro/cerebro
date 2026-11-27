import pytest

from src.api import main
from src.api.services import workflow_catalog


@pytest.mark.asyncio
async def test_an_invalid_enabled_catalogue_entry_fails_startup(monkeypatch):
    (entry,) = workflow_catalog.default_workflow_entries()
    bad = entry.model_copy(
        update={
            "routing_policy": entry.routing_policy.model_copy(
                update={"worker_types": ("not_a_registered_worker",)}
            )
        }
    )
    monkeypatch.setattr(workflow_catalog, "default_workflow_entries", lambda: (bad,))
    workflow_catalog.get_workflow_catalog.cache_clear()
    monkeypatch.setattr(main, "require_document_key", lambda settings: None)
    try:
        with pytest.raises(workflow_catalog.WorkflowCatalogError):
            async with main.lifespan(main.app):
                pass
    finally:
        workflow_catalog.get_workflow_catalog.cache_clear()
