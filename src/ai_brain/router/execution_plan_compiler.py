"""Pure compilation of an MASR proposal into frozen execution authority."""

from src.core.contracts import (
    COLLABORATION_MODE_SUPPORT,
    CollaborationMode,
    CollaborationModeSupport,
    ExecutionPlan,
    RoutingDecision,
)
from src.models.execution_authority import ExecutionAuthorityBinding

from .masr import RoutingDecision as MASRRoutingDecision


class ExecutionPlanCompiler:
    """Reject proposal drift; authority bindings, never MASR, supply plan data."""

    def compile(
        self,
        proposal: MASRRoutingDecision,
        binding: ExecutionAuthorityBinding,
    ) -> ExecutionPlan:
        try:
            mode = CollaborationMode(proposal.collaboration_mode)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported collaboration mode") from exc
        if COLLABORATION_MODE_SUPPORT[mode] is not CollaborationModeSupport.IMPLEMENTED:
            raise ValueError("unsupported collaboration mode")
        allocation = proposal.agent_allocation
        if (
            proposal.routing_strategy.value != binding.routing_policy.strategy
            or mode.value != binding.routing_policy.collaboration_mode
        ):
            raise ValueError("MASR proposal does not match routing policy")
        if tuple(allocation.worker_types) != tuple(
            worker.worker_type for worker in binding.workers
        ):
            raise ValueError("MASR proposal worker types do not match authority")
        if allocation.supervisor_type != binding.supervisor_type:
            raise ValueError("MASR proposal supervisor does not match authority")
        proposal_domains = tuple(
            getattr(domain, "value", domain)
            for domain in proposal.complexity_analysis.domains
        )
        if proposal_domains != binding.domains:
            raise ValueError("MASR proposal domains do not match authority")
        proposal_routes = tuple(
            self._model_route(model)
            for model in (
                proposal.optimization_result.primary_model,
                *proposal.optimization_result.fallback_models,
            )
        )
        binding_routes = tuple(
            (route.provider, route.model)
            for route in (
                binding.provider_model_policy.primary,
                *binding.provider_model_policy.fallbacks,
            )
        )
        if proposal_routes != binding_routes:
            raise ValueError(
                "MASR proposal provider/model routes do not match authority"
            )
        compiled_at = binding.clock()
        return ExecutionPlan(
            execution_plan_id=binding.plan_id_factory(),
            plan_version=1,
            run_id=binding.run.run_id,
            workflow_definition_id=binding.workflow_definition.workflow_definition_id,
            workflow_definition_version=binding.workflow_definition.workflow_version,
            routing_policy_id=binding.routing_policy.routing_policy_id,
            routing_policy_version=binding.routing_policy.routing_policy_version,
            routing_decision=RoutingDecision(
                routing_decision_id=f"{binding.authority_id}-decision",
                strategy=binding.routing_policy.strategy,
                domains=binding.domains,
                collaboration_mode=mode,
                supervisor_id=binding.supervisor_id,
                supervisor_type=binding.supervisor_type,
                workers=binding.workers,
                edges=binding.edges,
                provider_model_policy=binding.provider_model_policy,
                budget=binding.budget,
                stop_conditions=binding.stop_conditions,
                evaluator_requirements=binding.evaluator_requirements,
            ),
            compiled_at=compiled_at,
            deadline=binding.deadline,
            amendment=None,
        )

    @staticmethod
    def _model_route(model: object) -> tuple[str, str]:
        """Return one proposal route without allowing a defaulted model name."""

        provider = getattr(model, "provider", None)
        model_name = getattr(
            model,
            "model_name",
            getattr(model, "name", getattr(model, "model", None)),
        )
        if not isinstance(provider, str) or not isinstance(model_name, str):
            raise ValueError("MASR proposal provider/model route is incomplete")
        return provider, model_name
