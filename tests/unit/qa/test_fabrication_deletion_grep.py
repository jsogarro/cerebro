"""Repo-wide grep-assertions for Packet 0 (fabrication deletion).

These are the assertions §4.0 of the citation-integrity plan requires:
"Mock Publisher", "PlagiarismDetector", and "originality_score" appear
nowhere in ``src/``. A test file or the ``__pycache__`` directory
regenerating a stale ``.pyc`` must not produce a false pass, so this reads
only ``*.py`` files, skips ``__pycache__``/bytecode, and is independent of
which test happens to run first.
"""

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3] / "src"

_FORBIDDEN_STRINGS = [
    "Mock Publisher",
    "PlagiarismDetector",
    "originality_score",
]

# src/qa/legacy_annotation.py is the one legitimate exception: its entire
# purpose is to recognize these exact fingerprints in already-persisted rows
# so it can annotate them (see its module docstring and
# tests/unit/qa/test_legacy_annotation.py). Excluding it here, rather than
# weakening the strings it matches on, keeps this assertion meaningful for
# every other file while still letting the detector name what it detects.
_ALLOWED_EXCEPTIONS = {SRC_ROOT / "qa" / "legacy_annotation.py"}


def _iter_source_files():
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path in _ALLOWED_EXCEPTIONS:
            continue
        yield path


def test_src_root_exists_and_is_scanned():
    """Guard against the scan silently covering zero files (e.g. a moved
    src/ directory), which would make every assertion below vacuously true."""
    files = list(_iter_source_files())
    assert len(files) > 100, (
        f"Expected to scan the full src/ tree, only found {len(files)} files "
        f"under {SRC_ROOT} -- the scan root may be wrong."
    )


def test_no_fabricated_verification_strings_anywhere_in_src():
    hits: dict[str, list[str]] = {s: [] for s in _FORBIDDEN_STRINGS}

    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for forbidden in _FORBIDDEN_STRINGS:
            if forbidden in text:
                hits[forbidden].append(str(path.relative_to(SRC_ROOT.parent)))

    failures = {k: v for k, v in hits.items() if v}
    assert not failures, (
        "Fabrication-deletion grep-assertion failed. Found forbidden strings:\n"
        + "\n".join(f"  {k!r}: {v}" for k, v in failures.items())
    )


def test_qa_routes_module_was_deleted():
    """X3: src/api/routes/qa.py fabricated results on every endpoint and was
    confirmed unmounted in src/api/main.py. The whole file was deleted."""
    assert not (SRC_ROOT / "api" / "routes" / "qa.py").exists()


def test_qa_router_is_not_importable():
    import importlib

    import pytest

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.api.routes.qa")


def test_main_app_does_not_reference_the_deleted_qa_router():
    main_source = (SRC_ROOT / "api" / "main.py").read_text(encoding="utf-8")
    assert "qa" not in [
        name.strip() for name in _extract_routes_import_names(main_source)
    ]


def _extract_routes_import_names(main_source: str) -> list[str]:
    """Pull the names imported from ``src.api.routes`` in main.py's
    multi-line ``from src.api.routes import (...)`` block."""
    start = main_source.index("from src.api.routes import (")
    end = main_source.index(")", start)
    block = main_source[start:end]
    names_section = block.split("(", 1)[1]
    return [n for n in names_section.replace("\n", "").split(",") if n.strip()]
