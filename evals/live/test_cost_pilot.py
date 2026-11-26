"""Live agent cost pilot. It spends real money on the configured OpenRouter key.

Required environment: OPENROUTER_API_KEY, ENABLE_LIVE_EVAL=1, OPENROUTER_ENABLED,
EVAL_RUN_DATABASE_URL, EVAL_ORGANIZATION_ID, PILOT_MODEL, PILOT_TRIALS,
PILOT_CONCURRENCY, PILOT_PER_RUN_CAP_USD and LIVE_EVAL_BUDGET_USD. PILOT_CASES
(comma-separated case ids) narrows the cases; PILOT_TASK_TIMEOUT_SECONDS
defaults to 240.
"""

import json
import os
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.config import get_settings
from src.core.contracts import ProviderModelRoute
from src.evals.cost_pilot import build_report, preflight
from src.evals.pilot_runner import (
    PilotCase,
    database_run_reader,
    run_pilot,
    settled_key_usage,
)
from src.evals.trial_setup import build_harvest_service

pytestmark = [pytest.mark.live_eval, pytest.mark.cost_pilot]

CASES = Path(__file__).with_name("cost_pilot_cases.json")
REPORT = Path("evals/out/cost_pilot.json")
SEED = 20261125


@pytest.mark.timeout(7200)
@pytest.mark.asyncio
async def test_agent_cost_pilot(live_eval_cost_meter, gemini_fallback_guard) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    trials = int(os.environ["PILOT_TRIALS"])
    concurrency = int(os.environ["PILOT_CONCURRENCY"])
    per_run_cap = Decimal(os.environ["PILOT_PER_RUN_CAP_USD"])
    budget = Decimal(os.environ["LIVE_EVAL_BUDGET_USD"])
    task_timeout = int(os.environ.get("PILOT_TASK_TIMEOUT_SECONDS", "240"))
    organization_id = os.environ["EVAL_ORGANIZATION_ID"]
    route = ProviderModelRoute(provider="openrouter", model=os.environ["PILOT_MODEL"])
    cases = [PilotCase(**case) for case in json.loads(CASES.read_text())["cases"]]
    if os.environ.get("PILOT_CASES"):
        wanted = set(os.environ["PILOT_CASES"].split(","))
        cases = [case for case in cases if case.case_id in wanted]
        assert {case.case_id for case in cases} == wanted, "unknown PILOT_CASES id"

    preflight(
        runs=len(cases) * trials,
        concurrency=concurrency,
        per_run_cap=per_run_cap,
        budget=budget,
        eval_database_url=settings.EVAL_RUN_DATABASE_URL,
        app_database_url=settings.DATABASE_URL,
        research_runs=trials * sum(case.domain == "research" for case in cases),
        mcp_tool_path_enabled=settings.MCP_TOOL_PATH_ENABLED,
        openrouter_enabled=settings.OPENROUTER_ENABLED,
    )
    api_key = os.environ["OPENROUTER_API_KEY"]

    async with httpx.AsyncClient(timeout=30) as http:
        before = await settled_key_usage(http, api_key)
        async with build_harvest_service(settings, model_route=route) as (
            service,
            resolver,
        ):
            samples = await run_pilot(
                service=service,
                resolver=resolver,
                cases=cases,
                trials=trials,
                concurrency=concurrency,
                per_run_cap=per_run_cap,
                task_timeout_seconds=task_timeout,
                model_route=route,
                organization_id=organization_id,
                read_run=database_run_reader(
                    service.session_factory, organization_id=organization_id
                ),
            )
        after = await settled_key_usage(http, api_key)

    report = build_report(
        samples,
        billed_usd=after - before,
        gemini_calls=sum(gemini_fallback_guard.values()),
        seed=SEED,
    )
    report["process_meter_usd"] = live_eval_cost_meter.total_cost_usd
    report["model"] = route.model
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    assert report["gemini_calls"] == 0, "a Gemini fallback ran: the pilot is invalid"
    assert after - before <= budget, f"billed ${after - before} exceeds ${budget}"
