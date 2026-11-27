"""Procedural memory reports persistence failures instead of claiming success."""

from pathlib import Path

import pytest

from src.ai_brain.memory.procedural_memory import (
    ProceduralMemoryManager,
    Procedure,
)


def manager_at(path: Path) -> ProceduralMemoryManager:
    return ProceduralMemoryManager({"storage_path": str(path)})


@pytest.mark.asyncio
async def test_store_reports_false_when_persist_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    memory = manager_at(blocker / "procs.json")

    assert await memory.store_procedure(Procedure(id="p")) is False
    assert memory.store_count == 0


@pytest.mark.asyncio
async def test_update_performance_reports_false_when_persist_fails(
    tmp_path: Path,
) -> None:
    ok = manager_at(tmp_path / "procs.json")
    assert await ok.store_procedure(Procedure(id="p", usage_count=1))
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ok.storage_path = str(blocker / "procs.json")

    assert await ok.update_procedure_performance("p", 80.0, True) is False


@pytest.mark.asyncio
async def test_initialize_starts_empty_on_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "procs.json"
    path.write_text("{not json")
    memory = manager_at(path)

    await memory.initialize()

    assert memory.procedures == {}


@pytest.mark.asyncio
async def test_programming_errors_propagate(tmp_path: Path) -> None:
    memory = manager_at(tmp_path / "procs.json")

    with pytest.raises(AttributeError):
        await memory.retrieve_procedures(None)  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        await memory.learn_from_episode(
            {"steps": [{"a": 1}], "context": None}, 0.9, True
        )
