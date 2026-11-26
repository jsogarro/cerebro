"""Same-uid secret isolation: non-dumpable process, one worker, no SA token."""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
linux_only = pytest.mark.requires_linux_sandbox
skip_off_linux = pytest.mark.skipif(
    sys.platform != "linux", reason="needs Linux /proc and prctl"
)

# /proc/<pid>/environ shows the environment the process was launched with, so
# the sentinel goes in via the launch env. The grandchild is a distinct process
# standing in for a compromised parser child.
HELPER = textwrap.dedent(
    """
    import os, subprocess, sys
    if sys.argv[1] == "harden":
        from src.core.process_hardening import make_non_dumpable
        make_non_dumpable()
    reader = (
        "import os,sys\\n"
        "try:\\n"
        "    data = open(f'/proc/{os.getppid()}/environ','rb').read()\\n"
        "    print('READ' if b'hunter2' in data else 'EMPTY')\\n"
        "except PermissionError:\\n"
        "    print('DENIED')\\n"
    )
    print(subprocess.run([sys.executable, "-c", reader],
          capture_output=True, text=True).stdout.strip())
    """
)


def _run(args: list[str], code: str) -> str:
    env = {
        **os.environ,
        "CEREBRO_DISABLE_DOTENV": "1",
        "PYTHONPATH": str(ROOT),
        "SENTINEL_SECRET": "hunter2",
    }
    done = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=True,
    )
    return done.stdout.strip().splitlines()[-1]


@linux_only
@skip_off_linux
def test_non_dumpable_process_blocks_same_uid_environ_read() -> None:
    assert _run(["harden"], HELPER) == "DENIED"


@linux_only
@skip_off_linux
def test_control_run_without_hardening_leaks_environ() -> None:
    # Proves the test above can fail: unhardened, the same read succeeds.
    assert _run(["plain"], HELPER) == "READ"


@linux_only
@skip_off_linux
def test_importing_api_main_makes_process_non_dumpable() -> None:
    code = (
        "import ctypes, src.api.main;"
        "print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))"  # PR_GET_DUMPABLE
    )
    assert _run([], code) == "0"


@linux_only
@skip_off_linux
def test_hardened_process_can_still_read_its_own_proc_and_serve_metrics() -> None:
    # prometheus' process collector reads /proc/self/{stat,fd,limits}.
    code = (
        "import os, src.api.main;"
        "from prometheus_client import generate_latest;"
        "assert os.listdir('/proc/self/fd');"
        "assert 'Uid:' in open('/proc/self/status').read();"
        "print(b'process_open_fds' in generate_latest())"
    )
    assert _run([], code) == "True"


def test_make_non_dumpable_is_noop_off_linux(monkeypatch) -> None:
    from src.core import process_hardening

    monkeypatch.setattr(sys, "platform", "darwin")
    process_hardening.make_non_dumpable()


def test_production_cmd_runs_a_single_uvicorn_process() -> None:
    cmd = re.findall(r"^CMD (\[.*\])$", (ROOT / "Dockerfile").read_text(), re.M)[-1]
    argv = yaml.safe_load(cmd)
    assert argv[argv.index("--workers") + 1] == "1"


def test_api_pod_does_not_mount_service_account_token() -> None:
    docs = yaml.safe_load_all((ROOT / "k8s/deployment-api.yaml").read_text())
    deployment = next(d for d in docs if d and d["kind"] == "Deployment")
    assert (
        deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    )


GUARD_CONFTEST = (
    "from tests.conftest import (\n"
    "    pytest_runtest_logreport, pytest_sessionfinish, pytest_terminal_summary)\n"
)
SKIPPED_SANDBOX_TEST = textwrap.dedent(
    """
    import pytest
    @pytest.mark.requires_linux_sandbox
    @pytest.mark.skip(reason="forced")
    def test_sandbox(): ...
    def test_plain(): ...
    """
)


def _pytest_in(
    tmp_path: Path, *, ci: bool, source: str
) -> subprocess.CompletedProcess[bytes]:
    (tmp_path / "conftest.py").write_text(GUARD_CONFTEST)
    (tmp_path / "test_x.py").write_text(source)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers = requires_linux_sandbox: x\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env.update(PYTHONPATH=str(ROOT), CEREBRO_DISABLE_DOTENV="1")
    if ci:
        env["CI"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )


def test_ci_guard_fails_session_when_a_sandbox_test_is_skipped(tmp_path) -> None:
    done = _pytest_in(tmp_path, ci=True, source=SKIPPED_SANDBOX_TEST)
    assert done.returncode != 0
    # A red CI run must say which control went vacuous.
    assert b"test_x.py::test_sandbox" in done.stdout


def test_guard_is_inert_outside_ci(tmp_path) -> None:
    assert _pytest_in(tmp_path, ci=False, source=SKIPPED_SANDBOX_TEST).returncode == 0


def test_guard_passes_when_nothing_sandbox_marked_is_skipped(tmp_path) -> None:
    assert _pytest_in(tmp_path, ci=True, source="def test_a(): ...\n").returncode == 0


def test_ci_runs_on_linux_as_non_root() -> None:
    # Never skipped in CI: a macOS or root run would make every sandbox
    # control vacuous (root bypasses the dumpable check).
    if not os.environ.get("CI"):
        pytest.skip("only meaningful in CI")
    assert sys.platform == "linux"
    assert os.geteuid() != 0
