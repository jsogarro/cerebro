"""The general live-eval script must not collect the cost pilot.

The pilot reads PILOT_* variables and spends real money, so it has to run only
when selected by its own command.
"""

import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT = "evals/live/test_cost_pilot.py"


def _collected(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-cov", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def _script_marker_expression() -> str:
    script = (REPO_ROOT / "scripts" / "run_live_evals.sh").read_text()
    match = re.search(r"^pytest evals/live -m (\"[^\"]+\"|\S+)", script, re.MULTILINE)
    assert match, "scripts/run_live_evals.sh has no `pytest evals/live -m ...` line"
    return shlex.split(match.group(1))[0]


def _documented_pilot_args() -> list[str]:
    guide = (REPO_ROOT / "docs" / "testing-guide.md").read_text()
    match = re.search(r"^pytest (\S*test_cost_pilot\.py.*)$", guide, re.MULTILINE)
    assert match, "docs/testing-guide.md documents no pilot pytest command"
    return shlex.split(match.group(1))


def test_live_eval_script_does_not_collect_the_cost_pilot() -> None:
    collected = _collected("evals/live", "-m", _script_marker_expression())

    assert "test_provider_health" in collected
    assert PILOT not in collected, "run_live_evals.sh collects the cost pilot"


def test_documented_pilot_command_collects_the_cost_pilot() -> None:
    collected = _collected(*_documented_pilot_args())

    assert PILOT in collected
