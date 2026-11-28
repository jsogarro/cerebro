"""Smoke tests proving both adaptive-routing evaluation scripts run end to end."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def test_run_adaptive_routing_eval_script_executes_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/run_adaptive_routing_eval.py")],
        cwd=REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "Adaptive Routing Offline Evaluation Report" in result.stdout
    output_file = Path("/tmp/adaptive_routing_eval_results.json")
    assert output_file.exists()
    payload = json.loads(output_file.read_text())
    assert payload["num_queries"] == 200
    assert "static_mae" in payload and "adaptive_mae" in payload


def test_evaluate_adaptive_routing_promotion_script_executes_cleanly(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "promotion_report.json"
    criteria_path = (
        REPO_ROOT / "config/adaptive_routing_promotion_criteria.example.json"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/evaluate_adaptive_routing_promotion.py"),
            "--criteria",
            str(criteria_path),
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # Example criteria lack a live corpus, so exit code 3 = INSUFFICIENT_EVIDENCE.
    assert result.returncode == 3, f"{result.stdout}\n{result.stderr}"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["status"] == "insufficient_evidence"
    assert report["activation_approved"] is False
