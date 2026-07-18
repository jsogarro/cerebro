# Real-Time Monitoring for the A/B Testing Dashboard

## Status: unreachable in the running app

This document describes the **one** real monitoring component that exists in the
codebase: `RealTimeDashboard`, defined in
`src/ai_brain/experimentation/monitoring/real_time_dashboard.py`.

Its only consumer is `src/api/routes/experiment_agent_api.py`, and that router is
**never mounted** in `src/api/main.py`. The A/B-testing monitoring surface — the
`RealTimeDashboard` and every endpoint that touches it — is therefore
**currently unreachable in the running Cerebro app**. Treat everything below as a
description of code that exists but is not wired into the live API.

The rest of this file has been rewritten to match the source. Earlier drafts
described a large monitoring subsystem (a separate WebSocket manager, a metrics
pipeline, server-side Plotly rendering, a dedicated Prometheus exporter, an
alert/notification service, and a `/ws/experiments` endpoint) that **does not
exist anywhere in `src/`**. See [What does not exist](#what-does-not-exist).

## What actually exists

A single module, `src/ai_brain/experimentation/monitoring/real_time_dashboard.py`,
containing:

| Symbol | Line | Role |
|---|---|---|
| `DashboardMetric` (Enum) | 26 | Metric identifiers (quality_score, latency_ms, total_cost, success_rate, p_value, effect_size, statistical_power, …) |
| `ExperimentSnapshot` (dataclass) | 42 | Point-in-time snapshot of one experiment's per-variant metrics |
| `DashboardConfig` (dataclass) | 57 | Update interval, history window, alert thresholds, metrics to show |
| `RealTimeDashboard` (class) | 78 | The monitoring engine: registers experiments, records snapshots, builds update payloads, pushes them over WebSocket |
| `get_dashboard()` | 606 | Returns a process-wide singleton `RealTimeDashboard` |

There are no other monitoring modules. The four files named in the old draft
(`websocket_monitor.py`, `metrics_pipeline.py`, `dashboard.py`, `alerts.py`) do
not exist.

## Data model

`ExperimentSnapshot` (dataclass, line 42) is the unit of state:

```python
@dataclass
class ExperimentSnapshot:
    experiment_id: str
    timestamp: datetime
    variants: dict[str, dict[str, float]]   # variant -> {metric_name: value}
    sample_sizes: dict[str, int]            # variant -> n
    winning_variant: str | None = None
    confidence_level: float = 0.0
    p_value: float | None = None
    effect_size: float | None = None
    recommendation: str = "Continue experiment"
    estimated_completion: datetime | None = None
```

`RealTimeDashboard` keeps snapshots in plain in-memory dicts —
`self.experiment_history: dict[str, list[ExperimentSnapshot]]` and
`self.active_experiments: set[str]`. There is no database persistence and no
external metrics store; history is trimmed in memory to
`DashboardConfig.history_window_minutes` (default 60) by a background cleanup task.

## How it works

`RealTimeDashboard.__init__` (line 86) constructs its own
`ConnectionManager()` and `EventPublisher()` (Cerebro's existing WebSocket
primitives) and starts two background tasks via a `BackgroundTaskTracker`
(line 107): a periodic dashboard refresh loop and a periodic history-cleanup loop.

The lifecycle of a monitored experiment:

1. **Register** — `register_experiment(experiment_id, experiment_config)`
   (line 126) adds the id to `active_experiments` and broadcasts an
   `experiment_registered` event.
2. **Record** — `update_experiment_metrics(experiment_id, variant_metrics,
   sample_sizes, statistical_analysis=None)` (line 145) builds an
   `ExperimentSnapshot`, folds in any supplied statistical analysis
   (`p_value`, `effect_size`, `confidence_level`, `winning_variant`), appends it
   to history, then generates and broadcasts a `experiment_update` payload.
3. **Periodic refresh** — `_update_all_experiments` (line 185) runs every
   `update_interval_seconds` (default 5). **It currently feeds every active
   experiment with `random`-generated mock data**
   (`_update_experiment_with_mock_data`, line 192); the in-code comment notes
   this "would be replaced with actual data from AgentFrameworkExperimentor."
   There is no real experimentor integration on this path yet.

### Update payload and "charts"

`_generate_dashboard_update` (line 218) assembles a dict with `metrics`,
`charts`, `alerts`, and a `recommendation`. The `charts` entries
(`_generate_time_series_chart` line 264, `_generate_distribution_chart` line 303,
`_generate_statistical_chart` line 331) return **plain Python dicts shaped like
Plotly JSON** (`{"data": [...traces...], "layout": {...}}`).

There is **no server-side Plotly**: the module does not import `plotly`,
`pandas` (except an optional lazy import inside `export_experiment_data`), or any
rendering library. It emits Plotly-schema data structures and leaves rendering to
whatever client consumes them. The confidence intervals in
`_generate_statistical_chart` are explicitly mock values (`ci_lower =
improvement - 5`, in-code comment: "would be calculated properly").

### Alerts and recommendations

Alerts and recommendations are computed inline on the dashboard object, not by a
separate alert manager or notification service:

- `_check_for_alerts` (line 402) returns a list of dicts based on
  `DashboardConfig` thresholds: low sample size
  (`min_sample_size_alert`, default 50), statistical significance reached
  (`max_p_value_alert`, default 0.05), and small effect size
  (`min_effect_size_alert`, default 0.05).
- `_generate_recommendation` (line 444) returns a human-readable string derived
  from `p_value`, `effect_size`, and sample count.

Both are pure functions over a snapshot — no external delivery, no email/Slack,
no persisted alert log.

## WebSocket delivery (and its current limitation)

`RealTimeDashboard` uses Cerebro's existing WebSocket primitives directly:

- **`ConnectionManager`** (`src/api/websocket/connection_manager.py`, class at
  line 99). Clients connect through `connect_dashboard_client(client_id,
  websocket)` (line 470), which calls `connection_manager.connect(websocket,
  client_id)` and then sends an initial-state `WSMessage`
  (`WSMessageType.INFO`) built by `_get_dashboard_state` (line 504).
  Disconnect goes through `disconnect_dashboard_client` (line 490).
- **`EventPublisher`** (`src/api/services/event_publisher.py`).
  `_broadcast_to_dashboard` (line 497) calls
  `event_publisher.publish_event(event_type="dashboard_update", data=message,
  target_clients=[client_id])` for each connected dashboard client.

**Important caveat:** `EventPublisher.publish_event` (event_publisher.py:118) is
a **logging-only compatibility shim** — its own docstring states it "does NOT
actually deliver to `target_clients`." So the periodic/`update_experiment_metrics`
broadcasts are currently logged, not delivered to clients. Only the initial
snapshot sent in `connect_dashboard_client` (via
`connection_manager.connections[client_id].send_message(...)`) reaches a
connected socket. Real per-client delivery would require routing through
`ConnectionManager`'s real broadcast methods (`broadcast_to_project`,
`broadcast_to_user`, `broadcast_to_all`) rather than the `publish_event` shim.

`ConnectionManager` exposes `connect`, `disconnect`, `broadcast_to_project`,
`broadcast_to_user`, and `broadcast_to_all` (plus subscription helpers like
`handle_subscription_request` and stats accessors). `send_message` is **not** a
`ConnectionManager` method — it lives on the per-connection `WebSocketConnection`
class, which is why per-client delivery goes through
`connection_manager.connections[client_id].send_message(...)`. `ConnectionManager`
also has **no** `add_namespace` and **no** `register_handler` method — the
namespace/handler registration API described in the old draft does not exist.

## The one consumer (unmounted)

`src/api/routes/experiment_agent_api.py` is the only code that calls
`get_dashboard()`. Its router is declared with prefix
`/api/v1/experiments/agent-framework` (experiment_agent_api.py:113-114) and
includes a WebSocket endpoint `@router.websocket("/dashboard")` (line 449) plus
POST/GET routes that call `dashboard.register_experiment(...)` and
`dashboard.export_experiment_data(...)`.

None of these are reachable, because `main.py` does not `include_router` this
module. `experiment_agent_api.py` is one of the unmounted route modules in
`src/api/routes/` (alongside `experiments.py`, `benchmarks.py`, `costs.py`,
`improvement.py`, `memory.py`, `qa.py`, and the dead `masr.py` twin). To make the
dashboard reachable it would need to be mounted, and `publish_event` swapped for a
real `ConnectionManager` broadcast.

The **live** WebSocket surface in the running app is only:
`/ws`, `/ws/projects/{project_id}`, `/ws/cli/{project_id}`, and `GET /ws/health`
(plus the supervisor and TalkHier coordination sockets under
`/api/v1/supervisors/*` and `/api/v1/talkhier/*`). There is no
`/ws/experiments` endpoint.

## What does not exist

Removed from earlier drafts because none of it is present in `src/`:

- **Modules** — `websocket_monitor.py`, `metrics_pipeline.py`, `dashboard.py`,
  `alerts.py`.
- **Classes** — `ExperimentWebSocketManager`, `MetricsPipeline`,
  `ExperimentMetric`, `ExperimentDashboard`, `ExperimentMonitor`,
  `StatisticalAnalysisDisplay`, `ExperimentAlertManager`,
  `DecisionTriggerManager`, `NotificationService`, `PerformanceMonitor`,
  `ExperimentSystemHealth`, `ExperimentWebSocketIntegration`,
  `PrometheusExporter`.
- **Server-side Plotly rendering** — the real module emits Plotly-schema dicts
  but imports no plotting library.
- **A dedicated experiment Prometheus exporter** — there are no
  `cerebro_experiment_*` metrics. Cerebro's real Prometheus surface lives in
  `src/core/observability.py` and exposes only LLM metrics
  (`llm_call_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total`,
  `llm_request_cost_drift_ratio`, `llm_cost_drift_events_total`), served at
  `/metrics`.
- **`ConnectionManager.add_namespace` / `register_handler`** — not part of the
  class API.
- **`ws://localhost:8000/ws/experiments`** — not a route. The real WS routes are
  listed above.

## Where real observability lives

For monitoring that is actually wired into the running app, see
`src/core/observability.py` (Prometheus LLM metrics at `/metrics`), the
`LLMCostDriftMiddleware` cost-drift instrumentation, structlog structured
logging, and opt-in Langfuse tracing (`LANGFUSE_ENABLED`, default off). Those are
the live observability surfaces; the A/B experiment dashboard described here is
not yet among them.
