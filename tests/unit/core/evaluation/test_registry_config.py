"""The default registry adds the support pass and citation checks only when pinned."""

import hashlib
import json
from pathlib import Path

import pytest

from src.core.config import settings
from src.core.evaluation.fixture_support import FixtureManifestError
from src.core.evaluation.registry import build_default_registry


def _pinned(tmp_path: Path, name: str, payload: object) -> tuple[str, str]:
    text = json.dumps(payload)
    path = tmp_path / name
    path.write_text(text)
    return str(path), hashlib.sha256(text.encode()).hexdigest()


def _ids() -> list[str]:
    return [e.evaluator_id for e in build_default_registry().evaluators()]


def test_a_configured_manifest_registers_the_support_pass_before_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, manifest_sha = _pinned(
        tmp_path, "m.json", {"manifest_id": "m", "entries": []}
    )
    corpus, corpus_sha = _pinned(tmp_path, "c.json", [])
    monkeypatch.setattr(settings, "CLAIM_SUPPORT_FIXTURE_MANIFEST", manifest)
    monkeypatch.setattr(settings, "CLAIM_SUPPORT_FIXTURE_MANIFEST_SHA256", manifest_sha)
    monkeypatch.setattr(settings, "CITATION_CORPUS", corpus)
    monkeypatch.setattr(settings, "CITATION_CORPUS_SHA256", corpus_sha)

    ids = _ids()

    assert "fixture.claim_support" in ids
    assert {i for i in ids if i.startswith("citation.")} == {
        "citation.reference_resolution",
        "citation.metadata_agreement",
        "citation.retraction_status",
        "citation.version_of_record",
    }


def test_a_manifest_that_does_not_match_its_pinned_hash_stops_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _ = _pinned(tmp_path, "m.json", {"manifest_id": "m", "entries": []})
    monkeypatch.setattr(settings, "CLAIM_SUPPORT_FIXTURE_MANIFEST", manifest)
    monkeypatch.setattr(settings, "CLAIM_SUPPORT_FIXTURE_MANIFEST_SHA256", "0" * 64)

    with pytest.raises(FixtureManifestError):
        build_default_registry()
