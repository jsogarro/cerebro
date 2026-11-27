import pytest

from src.api.services.workflow_catalog import (
    DEFAULT_RESEARCH_WORKFLOW_ID,
    ExecutableWorkflowCatalog,
    WorkflowCatalogError,
    default_workflow_entries,
)
from src.core.kernel.component_keys import AGENT_KEYS

REGISTERED = tuple(AGENT_KEYS)


def _entry(**policy_changes):
    (entry,) = default_workflow_entries()
    policy = entry.routing_policy.model_copy(update=policy_changes)
    return entry.model_copy(update={"routing_policy": policy})


def test_default_catalog_lists_the_single_worker_research_workflow():
    catalog = ExecutableWorkflowCatalog(
        default_workflow_entries(), registered_workers=REGISTERED
    )
    (entry,) = catalog.enabled()
    assert entry.workflow.workflow_definition_id == DEFAULT_RESEARCH_WORKFLOW_ID
    assert entry.routing_policy.worker_types == ("literature_review",)


def test_catalog_hash_is_stable_and_tracks_content():
    first = ExecutableWorkflowCatalog(
        default_workflow_entries(), registered_workers=REGISTERED
    )
    again = ExecutableWorkflowCatalog(
        default_workflow_entries(), registered_workers=REGISTERED
    )
    assert first.catalog_hash == again.catalog_hash
    changed = ExecutableWorkflowCatalog(
        [_entry(strategy="cost_efficient")], registered_workers=REGISTERED
    )
    assert changed.catalog_hash != first.catalog_hash


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"collaboration_mode": "debate"}, "unsupported collaboration mode"),
        ({"collaboration_mode": "nonsense"}, "unknown collaboration mode"),
        ({"worker_types": ("research_specialist",)}, "unregistered workers"),
        (
            {"worker_types": ("literature_review", "synthesis")},
            "takes exactly one worker",
        ),
        ({"provider_allowlist": ()}, "provider allowlist is empty"),
        ({"max_attempts_per_task": 2}, "retries are not admitted"),
        ({"task_timeout_seconds": 5}, "budget disagrees with policy"),
    ],
)
def test_enabled_entry_that_cannot_execute_fails_catalog_construction(changes, message):
    with pytest.raises(WorkflowCatalogError, match=message):
        ExecutableWorkflowCatalog([_entry(**changes)], registered_workers=REGISTERED)


def test_disabled_entry_is_not_validated_and_not_listed():
    bad = _entry(worker_types=("research_specialist",)).model_copy(
        update={"enabled": False}
    )
    catalog = ExecutableWorkflowCatalog([bad], registered_workers=REGISTERED)
    assert catalog.enabled() == ()
    assert catalog.resolve(DEFAULT_RESEARCH_WORKFLOW_ID, None) is None


def test_duplicate_workflow_version_is_rejected():
    with pytest.raises(WorkflowCatalogError, match="duplicate"):
        ExecutableWorkflowCatalog(
            default_workflow_entries() * 2, registered_workers=REGISTERED
        )


def test_resolve_matches_version_exactly_and_defaults_to_newest():
    catalog = ExecutableWorkflowCatalog(
        default_workflow_entries(), registered_workers=REGISTERED
    )
    assert catalog.resolve(DEFAULT_RESEARCH_WORKFLOW_ID, "1") is not None
    assert catalog.resolve(DEFAULT_RESEARCH_WORKFLOW_ID, "2") is None
    assert catalog.resolve("unknown", None) is None
