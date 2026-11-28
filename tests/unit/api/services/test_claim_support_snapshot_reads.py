"""The service reads a snapshot back only when it still matches the cited digest."""

from types import SimpleNamespace

import pytest

from src.api.services.claim_support_evaluation_service import (
    ClaimSupportEvaluationService,
)
from src.core.acquisition.snapshots import (
    SnapshotIntegrityError,
    SnapshotNotFoundError,
)
from src.core.contracts.redaction import snapshot_digest

DATA = b"Aspirin reduced the risk of stroke."


class _Store:
    def __init__(self, result: object) -> None:
        self.result = result

    def get(self, _uri: str) -> bytes:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


def _excerpt(store: _Store) -> str | None:
    service = ClaimSupportEvaluationService(None, store)  # type: ignore[arg-type]
    row = SimpleNamespace(content_sha256=snapshot_digest(DATA), locator="char:0-9")
    return service._excerpt(row, "file:///x")  # type: ignore[arg-type]


def test_bytes_that_do_not_match_the_cited_digest_give_no_excerpt() -> None:
    assert _excerpt(_Store(b"Aspirin did not reduce the risk.")) is None


@pytest.mark.parametrize(
    "error", [SnapshotIntegrityError("x"), SnapshotNotFoundError("x"), OSError("x")]
)
def test_a_store_refusal_gives_no_excerpt_instead_of_raising(error: Exception) -> None:
    assert _excerpt(_Store(error)) is None


def test_matching_bytes_give_the_cited_span() -> None:
    assert _excerpt(_Store(DATA)) == "Aspirin r"
