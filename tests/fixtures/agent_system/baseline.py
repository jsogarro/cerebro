"""Small, static records used to document current Wave 0 failure coverage."""

FAILURE_TAXONOMY = (
    ("specification", "No canonical workflow/version or idempotency key is accepted."),
    (
        "coordination",
        "Routing collaboration modes translate lossy to supervisor modes.",
    ),
    ("verification_termination", "Completion/verification statuses are process-local."),
    ("tool", "No typed, durable tool-invocation record is exposed by legacy routes."),
    ("provider", "Fast path requires a configured provider and otherwise fails."),
    ("persistence", "Checkpoints degrade gracefully when persistence is unavailable."),
    (
        "human_control",
        "Legacy execution has no approval or durable cancel-reason contract.",
    ),
)

PUBLIC_ROUTE_CHARACTERIZATION_MANIFEST = {
    (
        "POST",
        "/api/v1/query/research",
    ): "test_query_api_baseline.py::test_research_pins_placeholder_metrics_and_ignored_execution_options",
    (
        "POST",
        "/api/v1/query/analyze",
    ): "test_query_api_baseline.py::test_analyze_and_synthesize_translate_to_research_context",
    (
        "POST",
        "/api/v1/query/synthesize",
    ): "test_query_api_baseline.py::test_analyze_and_synthesize_translate_to_research_context",
    (
        "GET",
        "/api/v1/query/execution/{execution_id}/status",
    ): "test_query_api_baseline.py::test_execution_status_results_and_resume_pin_legacy_error_details",
    (
        "GET",
        "/api/v1/query/execution/{execution_id}/results",
    ): "test_query_api_baseline.py::test_execution_status_results_and_resume_pin_legacy_error_details",
    (
        "POST",
        "/api/v1/query/execution/{project_id}/resume",
    ): "test_query_api_baseline.py::test_execution_status_results_and_resume_pin_legacy_error_details",
    (
        "POST",
        "/api/v1/query/literature",
    ): "../test_agent_query_metrics_api.py::TestIntelligentQueryAPI::test_convenience_endpoints",
    (
        "POST",
        "/api/v1/query/methodology",
    ): "test_query_api_baseline.py::test_methodology_and_comparison_wrappers_pin_exact_context",
    (
        "POST",
        "/api/v1/query/comparison",
    ): "test_query_api_baseline.py::test_methodology_and_comparison_wrappers_pin_exact_context",
    (
        "GET",
        "/api/v1/query/routing/strategies",
    ): "test_query_api_baseline.py::test_routing_recommendation_is_local_heuristic_and_invalid_context_is_500",
    (
        "GET",
        "/api/v1/query/routing/recommend",
    ): "test_query_api_baseline.py::test_routing_recommendation_is_local_heuristic_and_invalid_context_is_500",
    (
        "GET",
        "/api/v1/agents",
    ): "test_agent_api_baseline.py::test_list_info_and_health_preserve_legacy_discovery_and_status_shapes",
    (
        "GET",
        "/api/v1/agents/{agent_type}",
    ): "test_agent_api_baseline.py::test_list_info_and_health_preserve_legacy_discovery_and_status_shapes",
    (
        "POST",
        "/api/v1/agents/{agent_type}/execute",
    ): "test_agent_api_baseline.py::test_execute_and_convenience_routes_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/chain",
    ): "test_agent_api_baseline.py::test_chain_mixture_and_workflows_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/mixture",
    ): "test_agent_api_baseline.py::test_chain_mixture_and_workflows_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/{agent_type}/validate",
    ): "test_agent_api_baseline.py::test_agent_validation_is_local_heuristic_not_agent_execution",
    (
        "GET",
        "/api/v1/agents/{agent_type}/metrics",
    ): "../test_agent_api.py::TestAgentAPI::test_agent_metrics",
    (
        "GET",
        "/api/v1/agents/{agent_type}/health",
    ): "test_agent_api_baseline.py::test_list_info_and_health_preserve_legacy_discovery_and_status_shapes",
    (
        "GET",
        "/api/v1/agents/system/stats",
    ): "../test_agent_api.py::TestAgentAPI::test_system_stats",
    (
        "GET",
        "/api/v1/agents/executions/active",
    ): "../test_agent_api.py::TestAgentAPI::test_active_executions",
    (
        "POST",
        "/api/v1/agents/literature-review/search",
    ): "test_agent_api_baseline.py::test_execute_and_convenience_routes_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/citation/format",
    ): "test_agent_api_baseline.py::test_execute_and_convenience_routes_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/synthesis/combine",
    ): "test_agent_api_baseline.py::test_synthesis_combine_fails_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/workflows/literature-analysis",
    ): "test_agent_api_baseline.py::test_chain_mixture_and_workflows_fail_closed_without_authority",
    (
        "POST",
        "/api/v1/agents/workflows/comprehensive-research",
    ): "test_agent_api_baseline.py::test_chain_mixture_and_workflows_fail_closed_without_authority",
    (
        "GET",
        "/api/v1/agents/health/summary",
    ): "../test_agent_query_metrics_api.py::TestPerformanceAndMetrics::test_system_health_summary",
    (
        "GET",
        "/api/v1/agents/performance/comparison",
    ): "../test_agent_query_metrics_api.py::TestPerformanceAndMetrics::test_performance_comparison",
}


def failure_taxonomy_by_name() -> dict[str, str]:
    """Return a fresh mapping so individual tests cannot share mutable state."""

    return dict(FAILURE_TAXONOMY)
