"""J8: a tiny spend cap stops a dry run at the cap, and the ledger matches."""

import pytest

from tests.e2e.journeys.harness import require_path_containing

pytestmark = pytest.mark.e2e


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P20: no cost/spend read surface (needs P08 runs and the P04 journal first)",
)
async def test_run_is_refused_at_the_daily_cap_and_ledger_matches(client, new_account):
    await require_path_containing(client, await new_account("alice"), "/costs")
    pytest.fail(
        "cap landed: set a tiny cap, start a dry run, assert refusal and ledger == journal"
    )
