"""Every setting declared in ``src/core/config.py`` must be read somewhere.

A declared-but-unread setting is worse than a missing one when it looks like an
operational control: ``MASR_ENABLED``, ``TALKHIER_ENABLED`` and
``MEMORY_SYSTEM_ENABLED`` appear in the production env example, so an operator
would reasonably reach for one during an incident, and it would do nothing.

A setting counts as read when its name appears in ``src/`` outside
``config.py``, or when it feeds a top-level section of ``get_ai_brain_config()``
that some caller actually indexes (``get_ai_brain_config()["providers"]``).
Sections nobody indexes do not count: assembling a value into a dictionary that
is never consulted is not a read.

It is a **ratchet**: ``KNOWN_UNREAD`` may only shrink. Wire a setting or delete
it, then delete its line here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
CONFIG = SRC / "core" / "config.py"
AGGREGATE = "get_ai_brain_config"

KNOWN_UNREAD: frozenset[str] = frozenset(
    {
        "AGENTS_DEFAULT_TIMEOUT",
        "AGENTS_ENABLE_DYNAMIC_SPAWNING",
        "AGENTS_MAX_CONCURRENT",
        "AGENTS_MAX_RETRIES",
        "AGENTS_MAX_SUPERVISORS",
        "AI_BRAIN_ENABLED",
        "AI_BRAIN_MODE",
        "API_HOST",
        "API_PORT",
        "API_WORKERS",
        "CHECK_PASSWORD_BREACHES",
        "CLOUD_RUN_CPU_LIMIT",
        "CLOUD_RUN_ENABLED",
        "CLOUD_RUN_MAX_INSTANCES",
        "CLOUD_RUN_MEMORY_LIMIT",
        "CLOUD_RUN_MIN_INSTANCES",
        "CLOUD_RUN_REGION",
        "COST_DAILY_BUDGET_LIMIT",
        "COST_ENABLE_BUDGET_ALERTS",
        "COST_OPTIMIZATION_ENABLED",
        "DEV_ENABLE_AI_BRAIN_DEBUG",
        "DEV_ENABLE_MEMORY_DEBUG",
        "DEV_ENABLE_ROUTING_DEBUG",
        "DEV_MOCK_MEMORY",
        "DEV_MOCK_PROVIDERS",
        "ENABLE_CACHE",
        "ENABLE_METRICS",
        "ENABLE_MFA",
        "ENABLE_TRACING",
        "EPISODIC_MEMORY_ENABLED",
        "EPISODIC_MEMORY_MAX_SESSION_DURATION_HOURS",
        "EPISODIC_MEMORY_RETENTION_DAYS",
        "FINE_TUNING_AUTO_TRIGGER",
        "FINE_TUNING_ENABLED",
        "FINE_TUNING_MIN_EXAMPLES",
        "FINE_TUNING_QUALITY_THRESHOLD",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "HIERARCHICAL_AGENTS_ENABLED",
        "HIERARCHICAL_ENABLE_AUTO_SCALING",
        "HIERARCHICAL_MAX_DEPTH",
        "HIERARCHICAL_SCALE_DOWN_THRESHOLD",
        "HIERARCHICAL_SCALE_UP_THRESHOLD",
        "JWT_ALGORITHM",
        "MASR_ENABLED",
        "MAX_SESSIONS_PER_USER",
        "MCP_PORT",
        "MCP_TOOLS_ENABLED",
        "MEMORY_CONSOLIDATION_INTERVAL",
        "MEMORY_ENABLE_CROSS_TIER",
        "MEMORY_MAX_RECALL_ITEMS",
        "MEMORY_SYSTEM_ENABLED",
        "MFA_ISSUER",
        "MODEL_ROUTER_ENABLE_FALLBACK",
        "MODEL_ROUTER_HEALTH_CHECK_INTERVAL",
        "MODEL_ROUTER_MAX_RETRIES",
        "MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT",
        "MULTI_DOMAIN_MERGE_STRATEGY",
        "OPENROUTER_VALIDATE_SLUGS_ON_STARTUP",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "PASSWORD_HISTORY_LIMIT",
        "PERF_ENABLE_PROFILING",
        "PERF_ENABLE_QUERY_OPTIMIZATION",
        "PERF_METRICS_RETENTION_DAYS",
        "PERF_SLOW_QUERY_THRESHOLD_MS",
        "PROCEDURAL_MEMORY_ENABLED",
        "PROCEDURAL_MEMORY_MIN_CONFIDENCE",
        "PROCEDURAL_MEMORY_MIN_USAGE_PROMOTION",
        "PROCEDURAL_MEMORY_STORAGE_PATH",
        "RATE_LIMIT_PERIOD",
        "RATE_LIMIT_REQUESTS",
        "SELF_IMPROVEMENT_ENABLED",
        "SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION",
        "SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD",
        "SELF_IMPROVEMENT_REFLECTION_FREQUENCY",
        "SEMANTIC_MEMORY_COLLECTION_NAME",
        "SEMANTIC_MEMORY_EMBEDDING_DIMENSION",
        "SEMANTIC_MEMORY_EMBEDDING_MODEL",
        "SEMANTIC_MEMORY_ENABLED",
        "SEMANTIC_MEMORY_VECTOR_DB_URL",
        "SESSION_EXPIRE_HOURS",
        "SESSION_SECRET_KEY",
        "TALKHIER_CONSENSUS_THRESHOLD",
        "TALKHIER_ENABLED",
        "TALKHIER_ENABLE_HIERARCHICAL_REFINEMENT",
        "TALKHIER_MAX_REFINEMENT_ROUNDS",
        "TASK_TIMEOUT_SECONDS",
        "TEMPORAL_HOST",
        "TEMPORAL_NAMESPACE",
        "WORKER_CONCURRENCY",
        "WORKING_MEMORY_CLEANUP_INTERVAL",
        "WORKING_MEMORY_CONVERSATION_TTL",
        "WORKING_MEMORY_DEFAULT_TTL",
        "WORKING_MEMORY_ENABLED",
        "WORKING_MEMORY_MAX_MEMORY_MB",
        "WORKING_MEMORY_MAX_MESSAGES_IN_CONTEXT",
    }
)


def _declared_settings(tree: ast.Module) -> set[str]:
    settings_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.isupper()
    }


def _aggregate_sections(tree: ast.Module) -> dict[str, set[str]]:
    """Map each top-level key of ``get_ai_brain_config()`` to the settings in it."""
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == AGGREGATE
    )
    returned = next(
        node.value for node in ast.walk(method) if isinstance(node, ast.Return)
    )
    assert isinstance(returned, ast.Dict)
    return {
        key.value: {
            node.attr
            for node in ast.walk(value)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        for key, value in zip(returned.keys, returned.values, strict=True)
        if isinstance(key, ast.Constant)
    }


def unread_settings() -> set[str]:
    tree = ast.parse(CONFIG.read_text())
    others = "\n".join(path.read_text() for path in SRC.rglob("*.py") if path != CONFIG)
    read = _declared_settings(tree) & set(re.findall(r"\b[A-Z][A-Z0-9_]*\b", others))
    for section, names in _aggregate_sections(tree).items():
        if re.search(rf"{AGGREGATE}\(\)\[[\"']{section}[\"']\]", others):
            read |= names
    return _declared_settings(tree) - read


def test_no_new_unread_settings() -> None:
    new = sorted(unread_settings() - KNOWN_UNREAD)
    assert not new, (
        f"{len(new)} setting(s) in src/core/config.py have no read site: {new}. "
        "Read them where they take effect or delete them. (Baseline: "
        f"{len(KNOWN_UNREAD)} known unread settings.)"
    )


def test_unread_baseline_only_shrinks() -> None:
    stale = sorted(KNOWN_UNREAD - unread_settings())
    assert not stale, (
        f"{len(stale)} baseline entr(ies) are now read or deleted: {stale}. "
        "Remove them from KNOWN_UNREAD so the ratchet keeps its ground."
    )
