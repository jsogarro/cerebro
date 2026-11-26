"""The extraction child's limits, reaping and I/O, exercised without test hooks.

Hostile scripts import the real ``_limit_self`` and then misbehave, so the
production limits and the production reaping are what is under test. Tests that
need Linux kernel features skip elsewhere.
"""

import ast
import asyncio
import io
import json
import os
import subprocess
import sys
import textwrap
import time
import zlib
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from src.core.acquisition import extract_child
from src.core.config import settings
from src.services import document_ingestion as di
from src.services.document_ingestion import (
    DocumentIngestionError,
    extract_pdf_sandboxed,
)
from tests.test_document_ingestion import _pdf


@pytest.fixture(autouse=True)
def _fresh_extraction_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contended semaphore binds to one event loop; each test has its own."""
    monkeypatch.setattr(
        di,
        "_EXTRACT_SLOTS",
        asyncio.Semaphore(di._EXTRACT_SLOTS._value),
    )


_ON_LINUX = pytest.mark.skipif(sys.platform != "linux", reason="needs Linux rlimits")


def linux_only(test: Any) -> Any:
    """Skip off Linux; CI fails the session if such a test is ever skipped."""
    return pytest.mark.requires_linux_sandbox(_ON_LINUX(test))


PREAMBLE = (
    "import ctypes, os, pathlib, resource, sys, time\n"
    "from src.core.acquisition.extract_child import _limit_self, guarded\n"
)


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "hostile.py"
    path.write_text(PREAMBLE + textwrap.dedent(body))
    return ["-E", "-s", "-B", str(path)]


async def _run(
    argv: list[str],
    data: bytes = b"",
    *,
    timeout: float = 20,
    max_out: int = 1 << 20,
) -> bytes:
    return await di._run_child(argv, data, timeout=timeout, max_out=max_out)


async def _code(awaitable: object) -> str:
    with pytest.raises(DocumentIngestionError) as caught:
        await awaitable  # type: ignore[misc]
    return caught.value.code


class _Spawns:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.procs: list[asyncio.subprocess.Process] = []
        real = asyncio.create_subprocess_exec

        async def spy(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            proc = await real(*args, **kwargs)  # type: ignore[arg-type]
            self.procs.append(proc)
            return proc

        monkeypatch.setattr(di.asyncio, "create_subprocess_exec", spy)

    def assert_reaped(self) -> None:
        assert self.procs and self.procs[0].returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(self.procs[0].pid, 0)


@linux_only
async def test_running_out_of_memory_is_its_own_error(tmp_path: Path) -> None:
    argv = _script(tmp_path, "_limit_self()\nguarded(lambda: bytearray(2 << 30))\n")
    assert await _code(_run(argv)) == "extraction_resource_limit"


@linux_only
async def test_cpu_exhaustion_is_distinct_from_a_timeout(tmp_path: Path) -> None:
    argv = _script(
        tmp_path,
        """
        import resource
        _limit_self()
        resource.setrlimit(resource.RLIMIT_CPU, (1, 3))
        while True:
            pass
        """,
    )
    started = time.monotonic()
    assert await _code(_run(argv, timeout=15)) == "extraction_cpu_limit"
    assert time.monotonic() - started < 10


@linux_only
async def test_forking_is_refused(tmp_path: Path) -> None:
    argv = _script(
        tmp_path,
        """
        if os.geteuid() == 0:
            os.setgid(65534)
            os.setuid(65534)
            ctypes.CDLL(None).prctl(4, 1, 0, 0, 0)
        _limit_self()
        try:
            os.fork()
        except OSError:
            print("blocked")
        """,
    )
    assert (await _run(argv)).strip() == b"blocked"


@linux_only
async def test_writing_a_file_is_refused(tmp_path: Path) -> None:
    argv = _script(
        tmp_path,
        f"""
        _limit_self()
        try:
            with open({str(tmp_path / "out")!r}, "wb") as handle:
                handle.write(b"x")
                handle.flush()
        except OSError:
            print("blocked")
        """,
    )
    assert (await _run(argv)).strip() == b"blocked"


async def _spawn_production_child() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-m",
        extract_child.__name__,
        "300",
        "2000000",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        env=di._CHILD_ENV,
    )


async def _until(check: object, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():  # type: ignore[operator]
            return True
        await asyncio.sleep(0.05)
    return False


@linux_only
async def test_the_production_child_applies_its_limits() -> None:
    proc = await _spawn_production_child()
    try:
        limits = Path(f"/proc/{proc.pid}/limits")
        assert await _until(lambda: "1073741824" in limits.read_text())
        text = limits.read_text()
        assert "45" in text and "50" in text
        assert Path(f"/proc/{proc.pid}/oom_score_adj").read_text().strip() == "1000"
    finally:
        proc.kill()
        await proc.wait()


@linux_only
async def test_the_production_child_cannot_be_read_through_proc() -> None:
    proc = await _spawn_production_child()

    def refused() -> bool:
        try:
            Path(f"/proc/{proc.pid}/environ").read_bytes()
        except PermissionError:
            return True
        return False

    try:
        assert await _until(refused)
    finally:
        proc.kill()
        await proc.wait()


async def test_the_child_environment_holds_no_parent_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("DATABASE_URL", "GEMINI_API_KEY", "DOCUMENT_ENCRYPTION_KEY"):
        monkeypatch.setenv(name, "sentinel")
    argv = _script(tmp_path, "print(sorted(os.environ))\n")
    names = set(ast.literal_eval((await _run(argv)).decode()))
    assert names - {"__CF_USER_TEXT_ENCODING"} == {"LANG", "PATH"}


async def test_the_child_does_not_import_the_application() -> None:
    code = (
        "import src.core.acquisition.extract_child as m, sys;"
        "print(sorted(k for k in sys.modules if k.startswith("
        "('langchain','src.services','src.core.config'))))"
    )
    result = subprocess.run(
        [sys.executable, "-E", "-s", "-B", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"


async def test_a_timeout_kills_and_reaps_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _Spawns(monkeypatch)
    argv = _script(tmp_path, "time.sleep(1e6)\n")
    assert await _code(_run(argv, timeout=1)) == "extraction_timeout"
    spawns.assert_reaped()


async def test_cancellation_kills_and_reaps_the_child_and_frees_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _Spawns(monkeypatch)
    argv = _script(tmp_path, "time.sleep(1e6)\n")
    task = asyncio.create_task(_run(argv, timeout=60))
    assert await _until(lambda: bool(spawns.procs))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    spawns.assert_reaped()
    assert not di._EXTRACT_SLOTS.locked()


async def test_output_past_the_cap_is_refused_and_the_child_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _Spawns(monkeypatch)
    argv = _script(
        tmp_path,
        "sys.stdout.buffer.write(b'x' * 300000); sys.stdout.flush(); time.sleep(1e6)\n",
    )
    assert await _code(_run(argv, max_out=100_000)) == "output_too_large"
    spawns.assert_reaped()


async def test_a_large_input_with_early_output_does_not_deadlock(
    tmp_path: Path,
) -> None:
    argv = _script(
        tmp_path,
        """
        sys.stdout.buffer.write(b"x" * (1 << 20))
        sys.stdout.flush()
        sys.stdin.buffer.read()
        """,
    )
    out = await _run(argv, b"y" * (20 << 20), timeout=30, max_out=2 << 20)
    assert len(out) == 1 << 20


async def test_a_dying_child_does_not_hang_the_writer(tmp_path: Path) -> None:
    argv = _script(tmp_path, "sys.exit(3)\n")
    assert await _run(argv, b"y" * (20 << 20), timeout=15) == b""


async def test_extraction_works_from_another_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    extracted, _ = await extract_pdf_sandboxed(_pdf(["alpha", "bravo"]))
    assert "bravo" in extracted.text


def test_the_reply_is_not_ascii_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    emoji = "\U0001f600" * 900
    monkeypatch.setattr(extract_child, "_limit_self", lambda: None)
    monkeypatch.setattr(
        extract_child.pdf_text,
        "extract_pdf_text",
        lambda *a, **k: extract_child.pdf_text.ExtractedText(
            text=emoji,
            pages=(extract_child.pdf_text.PageSpan(1, 0, 900),),
            text_sha256="0" * 64,
            source_sha256="0" * 64,
        ),
    )
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    monkeypatch.setattr(sys, "stdout", out)
    extract_child.main(["300", "2000000"])
    out.flush()
    written = out.buffer.getvalue()  # type: ignore[attr-defined]
    assert json.loads(written)["text"] == emoji
    assert len(written) < 900 * 4 + 400


async def test_a_flate_bomb_is_malformed_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    stream = DecodedStreamObject()
    stream.set_data(b"0" * 1000)
    stream._data = zlib.compress(b"0" * (90 << 20), 1)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    assert await _code(extract_pdf_sandboxed(out.getvalue())) in {
        "malformed_pdf",
        "no_text",
    }
    assert settings.DOCUMENT_MAX_PAGES  # the parent is still serving


async def test_only_one_extraction_runs_at_a_time(tmp_path: Path) -> None:
    argv = _script(tmp_path, "time.sleep(1)\n")
    started = time.monotonic()
    await asyncio.gather(_run(argv), _run(argv))
    assert time.monotonic() - started >= 1.9


async def test_waiting_for_the_slot_counts_against_the_deadline(
    tmp_path: Path,
) -> None:
    slow = _script(tmp_path, "time.sleep(3)\n")
    holder = asyncio.create_task(_run(slow, timeout=30))
    await asyncio.sleep(0.5)
    started = time.monotonic()
    assert await _code(_run(slow, timeout=1)) == "extraction_busy"
    assert time.monotonic() - started < 2
    await holder


@pytest.mark.parametrize("breaks", ["extraction", "reply"])
def test_running_out_of_memory_anywhere_exits_with_the_memory_code(
    monkeypatch: pytest.MonkeyPatch, breaks: str
) -> None:
    monkeypatch.setattr(extract_child, "_limit_self", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    if breaks == "extraction":
        monkeypatch.setattr(
            extract_child.pdf_text,
            "extract_pdf_text",
            lambda *a, **k: _raise(MemoryError),
        )
    else:
        monkeypatch.setattr(
            extract_child.pdf_text,
            "extract_pdf_text",
            lambda *a, **k: extract_child.pdf_text.ExtractedText(
                text="t",
                pages=(extract_child.pdf_text.PageSpan(1, 0, 1),),
                text_sha256="0" * 64,
                source_sha256="0" * 64,
            ),
        )
        monkeypatch.setattr(
            extract_child.json, "dumps", lambda *a, **k: _raise(MemoryError)
        )
    with pytest.raises(SystemExit) as caught:
        extract_child.main(["300", "2000000"])
    assert caught.value.code == extract_child.EXIT_MEMORY


def _raise(error: type[BaseException]) -> None:
    raise error
