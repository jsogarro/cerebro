"""#278: a module skipped at collection must fail the required Linux job.

The marker-based guard cannot see these: a module skipped by importorskip or
skip(allow_module_level=True) never runs, so its tests carry no markers.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = "CEREBRO_REQUIRED_LINUX_MODULES"

GUARD_CONFTEST = (
    "from tests.conftest import (\n"
    "    pytest_collectreport, pytest_runtest_logreport,\n"
    "    pytest_sessionfinish, pytest_terminal_summary)\n"
)
IMPORTORSKIP = 'import pytest\nMISSING = "no_such_module_xyz"\npytest.importorskip(MISSING)\ndef test_a(): ...\n'
MODULE_SKIP = textwrap.dedent(
    """
    import pytest
    pytest.skip("not here", allow_module_level=True)
    def test_a(): ...
    """
)
MARKED_SKIP = textwrap.dedent(
    """
    import pytest
    @pytest.mark.requires_linux_sandbox
    @pytest.mark.skip(reason="forced")
    def test_a(): ...
    """
)
PLAIN = "def test_a(): ...\n"


def _run(tmp_path: Path, files: dict[str, str], required: str | None, ci: bool = True):
    (tmp_path / "conftest.py").write_text(GUARD_CONFTEST)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers = requires_linux_sandbox: x\n"
    )
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    env = {k: v for k, v in os.environ.items() if k not in {"CI", REQUIRED_ENV}}
    env.update(PYTHONPATH=str(ROOT), CEREBRO_DISABLE_DOTENV="1")
    if ci:
        env["CI"] = "1"
    if required is not None:
        env[REQUIRED_ENV] = required
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )


def test_importorskip_of_a_required_module_fails(tmp_path) -> None:
    done = _run(tmp_path, {"test_req.py": IMPORTORSKIP}, "test_req.py")
    assert done.returncode != 0
    assert b"test_req.py" in done.stdout


def test_module_level_skip_of_a_required_module_fails(tmp_path) -> None:
    assert _run(tmp_path, {"test_req.py": MODULE_SKIP}, "test_req.py").returncode != 0


def test_marked_test_skip_still_fails_in_ci(tmp_path) -> None:
    assert _run(tmp_path, {"test_req.py": MARKED_SKIP}, "test_req.py").returncode != 0


def test_required_module_that_is_not_collected_fails_closed(tmp_path) -> None:
    done = _run(tmp_path, {"test_other.py": PLAIN}, "test_missing.py")
    assert done.returncode != 0
    assert b"test_missing.py" in done.stdout


def test_skip_of_an_unrequired_module_is_allowed(tmp_path) -> None:
    files = {"test_req.py": PLAIN, "test_opt.py": IMPORTORSKIP}
    assert _run(tmp_path, files, "test_req.py").returncode == 0


def test_required_module_that_runs_passes(tmp_path) -> None:
    assert _run(tmp_path, {"test_req.py": PLAIN}, "test_req.py").returncode == 0


def test_guard_is_inert_when_no_required_set_is_declared(tmp_path) -> None:
    files = {"test_opt.py": IMPORTORSKIP, "test_ok.py": PLAIN}
    assert _run(tmp_path, files, None).returncode == 0
