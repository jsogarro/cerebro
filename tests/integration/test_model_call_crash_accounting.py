"""A governed call killed at each barrier leaves a journal a replacement can settle.

The child process runs the real wrapper against the real journal and is
SIGKILLed while blocked at the barrier; a replacement then reconciles from a new
engine. What is asserted is what survives a real process death: the durable row,
one uncertain charge at most, and no second send.
"""

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.test_model_call_journal import (  # noqa: F401 - fixtures
    ORG_A,
    _owner,
    _repo,
    engine_fixture,
    factory_fixture,
    migrated_fixture,
    server_fixture,
    url_fixture,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

CHILD = Path(__file__).with_name("model_call_crash_child.py")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _crash_at(url: str, owner_id: str, barrier: str, log: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, str(CHILD), url, str(ORG_A), owner_id, barrier, str(log)],
        cwd=REPO_ROOT,
        env={
            "PYTHONPATH": str(REPO_ROOT),
            "PATH": "/usr/bin:/bin",
            # The repository imports the session module, which loads Settings.
            "ENVIRONMENT": os.environ["ENVIRONMENT"],
            "SECRET_KEY": os.environ["SECRET_KEY"],
        },
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        child.send_signal(signal.SIGKILL)
    finally:
        child.kill()
        child.wait(timeout=30)


@pytest.mark.parametrize(
    ("barrier", "sent", "released", "unknown", "known_tokens"),
    [
        ("before_marker", 0, 1, 0, 0),
        ("after_marker", 1, 0, 1, 0),
        # The response arrived but the settlement never committed: still unknown.
        ("after_response", 1, 0, 1, 0),
    ],
)
async def test_a_killed_call_reconciles_to_one_unknown_charge_at_most(
    engine: AsyncEngine,
    factory: Callable[[], AsyncSession],
    url: str,
    tmp_path: Path,
    barrier: str,
    sent: int,
    released: int,
    unknown: int,
    known_tokens: int,
) -> None:
    owner = await _owner(engine)
    log = tmp_path / "provider.log"
    _crash_at(url, owner.owner_id, barrier, log)

    assert (log.read_text().count("sent") if log.exists() else 0) == sent
    replacement = _repo(factory, owner)
    first = await replacement.reconcile_owner()
    repeat = await replacement.reconcile_owner()
    totals = await replacement.totals()

    assert (first.released_unsent, first.settled_unknown) == (released, unknown)
    assert (repeat.released_unsent, repeat.settled_unknown) == (0, 0)
    assert totals.uncertain_tokens == unknown * 400
    assert totals.known_tokens == known_tokens and totals.open_tokens == 0
    # No second send: reconciling never invokes a provider, and the call id is spent.
    assert (log.read_text().count("sent") if log.exists() else 0) == sent
