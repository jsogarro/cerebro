"""The production image must contain every executable its entrypoint and CMD run.

The production stage copies only site-packages and src/ from the builder, so
console scripts in /usr/local/bin (alembic, uvicorn) exist only if copied.
Static check: no docker daemon required.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text()
ENTRYPOINT = (REPO_ROOT / "docker/entrypoint.sh").read_text()


def _production_stage() -> str:
    return re.split(r"^FROM .* as production\s*$", DOCKERFILE, flags=re.M)[1]


def _cmd_executable(stage: str) -> str:
    cmd = re.findall(r"^CMD (\[.*\])\s*$", stage, flags=re.M)[-1]
    return json.loads(cmd)[0]


def _entrypoint_executables() -> list[str]:
    return re.findall(r"^\s*if (\w+) ", ENTRYPOINT, flags=re.M)


def _is_available(exe: str, stage: str) -> bool:
    copied = re.search(
        rf"^COPY --from=builder /usr/local/bin/{re.escape(exe)}\s", stage, re.M
    )
    return bool(copied) or exe == "python"


def test_entrypoint_runs_alembic() -> None:
    assert "alembic" in _entrypoint_executables()


def test_production_stage_provides_entrypoint_and_cmd_executables() -> None:
    stage = _production_stage()
    needed = [*_entrypoint_executables(), _cmd_executable(stage)]
    missing = [exe for exe in needed if not _is_available(exe, stage)]
    assert not missing, f"production stage lacks executables: {missing}"


def test_production_stage_copies_alembic_config() -> None:
    stage = _production_stage()
    assert "COPY alembic/ ./alembic/" in stage
    assert "COPY alembic.ini ." in stage
