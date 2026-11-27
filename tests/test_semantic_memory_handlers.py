"""Semantic memory narrows its embedding handlers and lets bugs surface."""

import pytest

from src.ai_brain.memory import semantic_memory
from src.ai_brain.memory.semantic_memory import SemanticItem, SemanticMemoryManager


class Encoder:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def encode(self, text: str) -> list[float]:
        raise self.error


@pytest.mark.asyncio
async def test_embedding_runtime_error_falls_back_to_hash_embedding() -> None:
    memory = SemanticMemoryManager({"embedding_dimension": 8})
    memory.embedding_model = Encoder(RuntimeError("torch"))  # type: ignore[assignment]

    assert len(await memory._generate_embedding("hello") or []) == 8


@pytest.mark.asyncio
async def test_embedding_bug_propagates() -> None:
    memory = SemanticMemoryManager({})
    memory.embedding_model = Encoder(KeyError("bug"))  # type: ignore[assignment]

    with pytest.raises(KeyError):
        await memory._generate_embedding("hello")


@pytest.mark.asyncio
async def test_model_load_oserror_is_absorbed_but_bugs_propagate(monkeypatch) -> None:
    def failing(error: Exception):
        def factory(name: str) -> None:
            raise error

        return factory

    monkeypatch.setattr(semantic_memory, "QdrantClient", None)
    monkeypatch.setattr(semantic_memory, "SentenceTransformer", failing(OSError("hub")))
    memory = SemanticMemoryManager({})
    await memory.initialize()
    assert memory.embedding_model is None

    monkeypatch.setattr(
        semantic_memory, "SentenceTransformer", failing(KeyError("bug"))
    )
    with pytest.raises(KeyError):
        await SemanticMemoryManager({}).initialize()


@pytest.mark.asyncio
async def test_update_item_does_not_hide_bad_updates() -> None:
    memory = SemanticMemoryManager({})
    memory.vector_client = None
    memory._fallback_storage = [SemanticItem(id="i")]

    with pytest.raises(AttributeError):
        await memory.update_item("i", None)  # type: ignore[arg-type]
