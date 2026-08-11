"""Modules that are supposed to contain tests must actually collect some.

A test module that collects nothing reports no failures for exactly the same
reason a passing one does, and nothing in a normal run distinguishes them. That
is not hypothetical here: ``tests/test_mcp_tools.py`` carried
``pytest.importorskip("scipy")`` at **class-body** indentation, and because a
class body executes at *import*, it raised ``Skipped`` during collection of the
whole module — taking all four of its classes with it, three of which have
nothing to do with scipy. Eighteen tests were dormant on ``main`` for months.
The comment above that call said it skipped "the whole class cleanly", so even
a careful reader of that file would have believed it.

The specific bug is fixed at its source (the guard is now a class decorator).
This file guards the *class* of bug, because the next one will not look like a
class-body ``importorskip`` — a module-level import error, a stray
``pytestmark = pytest.mark.skip``, a renamed file that stops matching
``python_files``, or a syntax error inside a lazily-imported branch all produce
the same silence.

**Why a subprocess rather than an in-process collect.** Re-entering
``pytest.main`` inside a running session shares plugin and import state with the
outer run, so a module already imported by the outer collection can appear
collectable here when a clean interpreter would fail on it — which would make
this guard report health it has not established.

Deliberately a *floor*, not an exact count: pinning exact numbers turns every
added test into a failure here and trains people to update the number without
reading why it moved.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules with a history of collecting nothing, or which carry an optional
# dependency guard that could regress into one. Add to this list whenever a
# module is found dormant — the entry is the regression test.
MUST_COLLECT: tuple[tuple[str, int, str | None], ...] = (
    # 18 tests across 4 classes; dormant on main until 2026-08-11 because of a
    # class-body importorskip. Note the floor holds *without* scipy installed:
    # 4 of the 18 skip, and a skipped test is still a collected one. That is
    # exactly the distinction this file is about — a correct guard skips tests,
    # a broken one aborts collection.
    ("tests/test_mcp_tools.py", 18, None),
    # Module-level importorskip("fastmcp"). That is the *correct* form and not
    # the bug above, but the consequence is the same shape: without the
    # dependency this module collects nothing at all. So the floor is asserted
    # only when the dependency is actually present — otherwise this guard would
    # fail for a reason that is not a defect, and a guard that cries wolf gets
    # deleted. The honest cost is stated rather than hidden: when fastmcp is
    # absent these tests are silently not run, and nothing here can change that.
    ("tests/test_mcp_integration.py", 1, "fastmcp"),
)


def _collected_count(module: str) -> int:
    """Return how many tests a clean interpreter collects from ``module``."""

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            module,
            "--collect-only",
            "-q",
            "--no-cov",
            "-p",
            "no:randomly",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # `-q --collect-only` ends with a line like "tests/foo.py: 18". A module
    # that collects nothing prints no such line at all, which is precisely the
    # silence this guard exists to convert into a failure.
    for line in result.stdout.splitlines():
        if line.startswith(f"{module}:"):
            return int(line.split(":")[-1].strip())
    return 0


@pytest.mark.parametrize(
    ("module", "minimum", "requires"), MUST_COLLECT, ids=lambda v: str(v)
)
def test_the_module_collects_at_least_its_known_tests(
    module: str, minimum: int, requires: str | None
) -> None:
    assert (REPO_ROOT / module).is_file(), (
        f"{module} is pinned here as a module that must collect tests, but it "
        "does not exist. Either restore it or remove the entry — a guard "
        "pointing at a deleted file passes forever."
    )
    if requires is not None and importlib.util.find_spec(requires) is None:
        pytest.skip(
            f"{module} collects only with {requires!r} installed; its floor "
            "cannot be asserted here. Those tests do not run in this "
            "environment, which is the honest state, not a passing one."
        )

    collected = _collected_count(module)

    assert collected >= minimum, (
        f"{module} collected {collected} tests, expected at least {minimum}. "
        "A module that collects nothing reports no failures for the same "
        "reason a passing one does. Common causes: an importorskip or a raise "
        "at module or class-body indentation (both execute at import), a "
        "module-level pytestmark skip, or an import error in a branch only "
        "this module reaches."
    )
