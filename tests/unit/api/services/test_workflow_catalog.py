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


def _with_workflow(entry, **changes):
    return entry.model_copy(
        update={"workflow": entry.workflow.model_copy(update=changes)}
    )


def test_default_policy_pointer_must_match_the_entrys_policy():
    (entry,) = default_workflow_entries()
    bad = _with_workflow(entry, default_routing_policy_version="9")
    with pytest.raises(WorkflowCatalogError, match="default routing policy"):
        ExecutableWorkflowCatalog([bad], registered_workers=REGISTERED)


def test_entry_must_name_exactly_one_domain():
    (entry,) = default_workflow_entries()
    bad = entry.model_copy(update={"domains": ("research", "content")})
    with pytest.raises(WorkflowCatalogError, match="one domain"):
        ExecutableWorkflowCatalog([bad], registered_workers=REGISTERED)


def test_budget_parallelism_cannot_exceed_the_worker_count():
    (entry,) = default_workflow_entries()
    policy = entry.routing_policy.model_copy(update={"max_parallel_tasks": 2})
    budget = entry.budget_ceiling.model_copy(update={"max_parallel_tasks": 2})
    bad = entry.model_copy(update={"routing_policy": policy, "budget_ceiling": budget})
    with pytest.raises(WorkflowCatalogError, match="budget disagrees"):
        ExecutableWorkflowCatalog([bad], registered_workers=REGISTERED)


def test_output_schema_with_a_remote_reference_is_rejected():
    (entry,) = default_workflow_entries()
    bad = _with_workflow(
        entry, output_schema={"$ref": "https://example.com/schema.json"}
    )
    with pytest.raises(WorkflowCatalogError, match="local definitions"):
        ExecutableWorkflowCatalog([bad], registered_workers=REGISTERED)


def test_resolve_without_a_version_returns_the_newest_enabled():
    (entry,) = default_workflow_entries()
    newer = _with_workflow(entry, workflow_version="2")
    catalog = ExecutableWorkflowCatalog([entry, newer], registered_workers=REGISTERED)
    resolved = catalog.resolve(DEFAULT_RESEARCH_WORKFLOW_ID, None)
    assert resolved.workflow.workflow_version == "2"


def test_catalog_never_lists_a_disabled_entry_even_when_it_is_newest():
    (entry,) = default_workflow_entries()
    newer = _with_workflow(entry, workflow_version="2").model_copy(
        update={"enabled": False}
    )
    catalog = ExecutableWorkflowCatalog([entry, newer], registered_workers=REGISTERED)
    assert [e.workflow.workflow_version for e in catalog.enabled()] == ["1"]
    assert (
        catalog.resolve(DEFAULT_RESEARCH_WORKFLOW_ID, None).workflow.workflow_version
        == "1"
    )


def test_entry_shape_carries_what_routing_must_honour():
    (entry,) = default_workflow_entries()
    shape = entry.shape()
    assert shape.domains == entry.domains
    assert shape.worker_types == entry.routing_policy.worker_types
    assert shape.provider_allowlist == entry.routing_policy.provider_allowlist
    assert shape.collaboration_mode.value == entry.routing_policy.collaboration_mode


def test_provider_allowlist_uses_dispatchable_provider_names():
    (entry,) = default_workflow_entries()
    assert set(entry.routing_policy.provider_allowlist) <= {
        "deepseek",
        "gemini",
        "llama",
        "openrouter",
    }
