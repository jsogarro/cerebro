"""Acceptance tests for supply-chain pinning.

These tests run unconditionally post-refactor and ASSERT that supply-chain
pinning has not regressed. There is no auto-detection: stripping digests from
a Dockerfile MUST fail the suite, not silently disable it.

Override `CEREBRO_DOCKER_HARDENED=0` to suppress only in genuinely pre-refactor
branches. Default (and CI) leaves it active.

The EXPECTED_DIGESTS dict is INTENTIONALLY a second source of truth alongside
the Dockerfiles themselves. This is a tripwire: a digest update requires
deliberate edits to both sites, surfacing the bump in PR review. A
runtime-parser approach would only mirror whatever the Dockerfiles already say,
defeating the tripwire purpose.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ["Dockerfile", "docker/Dockerfile.masr", "cerebro/web/Dockerfile"]

HARDENED = os.environ.get("CEREBRO_DOCKER_HARDENED", "1") != "0"

# Per-image expected digests captured at refactor time. Each digest is a
# multi-arch OCI image index (verified via `docker buildx imagetools inspect`),
# covering linux/amd64, linux/arm64, and the other official platforms — pinning
# to these does NOT break per-arch resolution.
#
# Update process: bump a digest here AND in the corresponding Dockerfile in the
# same commit. CI will fail if the two diverge, surfacing accidental drift.
EXPECTED_DIGESTS = {
    "python:3.11-slim": "sha256:6d85378d88a19cd4d76079817532d62232be95757cb45945a99fec8e8084b9c2",
    "node:20-alpine": "sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293",
    "nginx:alpine": "sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de",
    "ghcr.io/astral-sh/uv:latest": "sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347",
}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def _all_image_refs(content: str) -> list[str]:
    """Return external image references only.

    Stage aliases (`FROM base as development`, `COPY --from=builder`) are
    intra-Dockerfile and never need pinning. We collect the set of stage names
    first, then exclude any reference that matches a known stage.
    """
    stage_names: set[str] = set()
    for line in content.splitlines():
        m = re.search(r"^\s*FROM\s+\S+\s+AS\s+(\w+)", line, re.IGNORECASE)
        if m:
            stage_names.add(m.group(1))

    refs: list[str] = []
    for line in content.splitlines():
        m = re.search(r"^\s*FROM\s+(\S+)", line, re.IGNORECASE)
        if m and m.group(1) not in stage_names:
            refs.append(m.group(1))
        m = re.search(r"^\s*COPY\s+--from=(\S+)", line, re.IGNORECASE)
        if m and m.group(1) not in stage_names:
            refs.append(m.group(1))
    return refs


def _has_digest(image_ref: str) -> bool:
    return "@sha256:" in image_ref


# --------------------------------------------------------------------------- #
# Baseline characterization (always runs, locks current state)
# --------------------------------------------------------------------------- #


class TestSupplyChainBaseline:
    def test_dockerfiles_exist(self) -> None:
        for path in DOCKERFILES:
            assert (REPO_ROOT / path).exists(), f"Missing Dockerfile: {path}"

    def test_baseline_image_set_unchanged(self) -> None:
        all_refs: set[str] = set()
        for path in DOCKERFILES:
            for ref in _all_image_refs(_read(path)):
                base = ref.split("@")[0]
                all_refs.add(base)
        expected = {"python:3.11-slim", "node:20-alpine", "nginx:alpine", "ghcr.io/astral-sh/uv:latest"}
        assert all_refs == expected, f"Image set drifted: {all_refs} != {expected}"


# --------------------------------------------------------------------------- #
# Post-refactor acceptance gates (gated on CEREBRO_DOCKER_HARDENED=1)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not HARDENED, reason="Pre-refactor: digests not yet pinned")
class TestSupplyChainPinned:
    @pytest.mark.parametrize("path", DOCKERFILES)
    def test_every_image_ref_has_sha256_digest(self, path: str) -> None:
        refs = _all_image_refs(_read(path))
        unpinned = [r for r in refs if not _has_digest(r)]
        assert not unpinned, f"{path}: unpinned refs: {unpinned}"

    @pytest.mark.parametrize("path", DOCKERFILES)
    def test_pinned_digests_match_expected(self, path: str) -> None:
        for ref in _all_image_refs(_read(path)):
            base, _, digest = ref.partition("@")
            if base in EXPECTED_DIGESTS:
                expected = EXPECTED_DIGESTS[base]
                assert digest == expected, f"{path}: {base} pinned to {digest}, expected {expected}"


# --------------------------------------------------------------------------- #
# Build hygiene (.dockerignore) acceptance gate
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (REPO_ROOT / ".dockerignore").exists(),
    reason="Pre-refactor: .dockerignore not yet present",
)
class TestDockerignorePresent:
    @pytest.fixture(scope="class")
    def patterns(self) -> set[str]:
        path = REPO_ROOT / ".dockerignore"
        assert path.exists(), ".dockerignore missing"
        lines = [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return set(lines)

    @pytest.mark.parametrize(
        "required",
        [
            "**/__pycache__",
            "**/*.pyc",
            ".git/",
            ".venv/",
            ".env",
            ".coverage",
            "htmlcov/",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
            "node_modules/",
            "*.md",
        ],
    )
    def test_pattern_excluded(self, patterns: set[str], required: str) -> None:
        # Note: `tests/` is intentionally NOT excluded — the root Dockerfile's
        # development stage runs `COPY tests/ ./tests/` for in-image pytest runs.
        # Production stage uses selective COPY of src/ only, so tests/ never
        # ships to runtime images regardless.
        assert required in patterns, f".dockerignore missing required pattern: {required}"
