"""The committed pilot cases match the approved mix and the golden dataset."""

import json
from collections import Counter
from pathlib import Path

from src.evals.trial_setup import DOMAIN_WORKERS

ROOT = Path(__file__).resolve().parents[3]


def _cases() -> list[dict[str, str]]:
    path = ROOT / "evals" / "live" / "cost_pilot_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def test_cases_follow_the_approved_domain_mix() -> None:
    cases = _cases()

    assert Counter(case["domain"] for case in cases) == {
        "research": 4,
        "analytics": 2,
        "content": 2,
        "finance": 2,
    }
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["domain"] for case in cases} <= set(DOMAIN_WORKERS)


def test_research_cases_are_golden_queries_verbatim() -> None:
    golden_path = ROOT / "tests" / "fixtures" / "golden_dataset.json"
    golden = {
        f"research-{case['id']}": case["query"]
        for case in json.loads(golden_path.read_text(encoding="utf-8"))["cases"]
    }

    for case in _cases():
        if case["domain"] == "research":
            assert golden[case["case_id"]] == case["query"]
