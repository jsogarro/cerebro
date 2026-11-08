"""The ``dev`` extra must install every third-party module tests ``importorskip``.

CI installs only ``.[dev]``. A test module guarded by ``pytest.importorskip("x")``
where ``x`` lives in another extra skips silently in CI while running locally, so
CI reports green on code it never executed.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTORSKIP = re.compile(r"importorskip\(\s*[\"']([\w.]+)[\"']")


def _dist_names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[\[<>=!~;\s]", r.strip(), maxsplit=1)[0].lower().replace("_", "-")
        for r in requirements
    }


def _dev_distributions() -> set[str]:
    """Distributions installed by ``.[dev]``, expanding self-referencing extras."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    installed = set(_dist_names(extras["dev"]))
    for req in extras["dev"]:
        m = re.match(rf"{re.escape(project['name'])}\[([^\]]+)\]", req)
        for extra in m.group(1).split(",") if m else ():
            installed |= _dist_names(extras[extra.strip()])
    return installed


def _importorskipped_modules() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in (ROOT / "tests").rglob("*.py"):
        if path == Path(__file__):
            continue
        for module in IMPORTORSKIP.findall(path.read_text()):
            found.setdefault(module.split(".")[0], []).append(
                str(path.relative_to(ROOT))
            )
    return found


def test_importorskip_scan_sees_the_known_optional_deps() -> None:
    """Guard the scan itself: an empty result would make the check vacuous."""
    assert {"fastmcp", "scipy"} <= set(_importorskipped_modules())


def test_dev_extra_covers_every_importorskipped_module() -> None:
    # Assumes the import name equals the distribution name (true for every
    # module skipped today); add a mapping here if that stops holding.
    dev = _dev_distributions()
    missing = {
        module: files
        for module, files in _importorskipped_modules().items()
        if module.lower().replace("_", "-") not in dev
    }
    assert not missing, (
        f"tests importorskip modules the [dev] extra does not install: {missing}. "
        "Add them to [project.optional-dependencies].dev in pyproject.toml, or CI "
        "will skip those tests silently."
    )
