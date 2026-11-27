import pytest
from pydantic import ValidationError

from src.api.services.workflow_catalog import (
    DEFAULT_RESEARCH_WORKFLOW_ID,
    ExecutableWorkflowCatalog,
    default_workflow_entries,
)
from src.api.services.workflow_launch_service import (
    WorkflowLaunchRejectedError,
    WorkflowLaunchRequest,
    WorkflowLaunchService,
)
from src.core.kernel.component_keys import AGENT_KEYS

service = WorkflowLaunchService(
    ExecutableWorkflowCatalog(
        default_workflow_entries(), registered_workers=tuple(AGENT_KEYS)
    )
)


def _request(**fields):
    return WorkflowLaunchRequest(
        workflow_id=DEFAULT_RESEARCH_WORKFLOW_ID,
        objective="why is the sky blue",
        **fields,
    )


def _select(request=None, *, requester="u1", key="k1"):
    return service.select(
        request or _request(), requester=requester, idempotency_key=key
    )


def _rejection(**kwargs) -> WorkflowLaunchRejectedError:
    with pytest.raises(WorkflowLaunchRejectedError) as caught:
        _select(**kwargs)
    return caught.value


def test_valid_request_selects_the_catalog_entry_and_records_dry_run():
    selection = _select(_request(dry_run=True))
    assert (
        selection.entry.workflow.workflow_definition_id == DEFAULT_RESEARCH_WORKFLOW_ID
    )
    assert selection.dry_run is True
    assert selection.budget == selection.entry.budget_ceiling


def test_missing_idempotency_key_is_400():
    rejection = _rejection(key=None)
    assert (rejection.code, rejection.http_status) == ("IDEMPOTENCY_KEY_REQUIRED", 400)


def test_unknown_workflow_is_404():
    request = WorkflowLaunchRequest(workflow_id="nope", objective="x")
    assert _rejection(request=request).http_status == 404


def test_debate_is_rejected_until_it_is_implemented():
    rejection = _rejection(request=_request(mode="debate"))
    assert (rejection.code, rejection.http_status) == ("MODE_UNSUPPORTED", 422)


def test_a_mode_other_than_the_workflows_is_rejected():
    assert _rejection(request=_request(mode="parallel")).code == "MODE_UNSUPPORTED"


def test_confirming_the_workflows_mode_is_accepted():
    assert _select(_request(mode="fast_path"))


def test_unauthorized_provider_policy_is_rejected():
    rejection = _rejection(request=_request(provider_policy_id="cheap-and-wild"))
    assert rejection.code == "PROVIDER_POLICY_NOT_ALLOWED"


def test_budget_hint_can_narrow_but_not_widen():
    ceiling = _select().budget
    narrowed = _select(_request(budget={"max_total_tokens": 10})).budget
    assert narrowed.max_total_tokens == 10
    assert narrowed.max_cost_usd == ceiling.max_cost_usd
    widened = _rejection(
        request=_request(budget={"max_total_tokens": ceiling.max_total_tokens + 1})
    )
    assert widened.code == "BUDGET_EXPANSION"


@pytest.mark.parametrize("field", ["user_id", "tenant_id", "organization_id"])
def test_ownership_fields_are_not_accepted_on_the_request(field):
    with pytest.raises(ValidationError):
        _request(**{field: "someone-else"})


def test_reserved_keys_inside_inputs_are_rejected():
    with pytest.raises(ValidationError, match="reserved launch keys"):
        _request(inputs={"authority_id": "a"})


def test_oversized_inputs_are_rejected():
    with pytest.raises(ValidationError, match="size limit"):
        _request(inputs={"blob": "x" * 70_000})


def test_digest_is_stable_and_binds_requester_query_dry_run_and_parent():
    base = _select().launch_digest
    assert _select().launch_digest == base
    assert _select(requester="u2").launch_digest != base
    assert _select(_request(dry_run=True)).launch_digest != base
    assert _select(_request(parent_run_id="r1")).launch_digest != base
    other = WorkflowLaunchRequest(
        workflow_id=DEFAULT_RESEARCH_WORKFLOW_ID, objective="b"
    )
    assert _select(other).launch_digest != base


def test_digest_ignores_the_idempotency_key_itself():
    assert _select(key="a").launch_digest == _select(key="b").launch_digest
