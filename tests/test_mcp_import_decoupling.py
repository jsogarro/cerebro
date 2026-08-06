"""Importing an MCP tool must not drag in dependencies it does not use.

Packet 4-Char's safety net for the two network-calling tool paths could not
execute in the gate environment, and neither gate was about the code under
test:

- `src/agents/integrations/mcp_integration.py` imported `MCPClient` at module
  scope, and `MCPClient` -> `MCPServer` -> `fastmcp`. Importing the integration
  module therefore required the optional `[mcp]` extra even for a caller that
  injects its own client and never constructs an `MCPClient` at all.
- `src/mcp/tools/__init__.py` imported `StatisticsTool` alongside the other
  three tools, and Python runs a package's `__init__.py` before any submodule.
  `from src.mcp.tools.citation_tool import CitationTool` therefore required
  `scipy`, which `citation_tool.py` never imports.

Both are eager-import coupling, not real dependencies. These tests are the
reason the decoupling cannot quietly come back: each asserts that a module
imports in a subprocess whose `scipy`/`fastmcp` imports are poisoned, which is
a stronger statement than "it imports here" — the gate environment happens not
to have either package, so a plain import test would pass again the moment
someone installs one.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

_POISON = """
import builtins
_real_import = builtins.__import__

def _guarded(name, *args, **kwargs):
    root = name.split(".")[0]
    if root in {banned!r}:
        raise AssertionError(
            "importing " + {subject!r} + " pulled in " + root
        )
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _guarded
"""


def _import_with_banned(subject: str, banned: set[str]) -> None:
    """Import ``subject`` in a fresh process where ``banned`` roots raise."""

    script = textwrap.dedent(_POISON).format(banned=banned, subject=subject)
    script += f"\nimport {subject}\n"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.fail(f"importing {subject} failed:\n{completed.stderr}")


class TestIntegrationModuleDoesNotRequireFastmcp:
    def test_importing_mcp_integration_does_not_import_fastmcp(self) -> None:
        _import_with_banned(
            "src.agents.integrations.mcp_integration", {"fastmcp", "scipy"}
        )

    def test_constructing_the_integration_does_not_import_fastmcp(self) -> None:
        """Construction must stay lazy too, not only the import.

        `MCPIntegration()` with no injected client is the production shape
        (`src/agents/factory.py` constructs exactly that), so a deferred import
        that fires in `__init__` would decouple nothing.
        """

        script = textwrap.dedent(_POISON).format(
            banned={"fastmcp"}, subject="MCPIntegration()"
        )
        script += textwrap.dedent(
            """
            from src.agents.integrations.mcp_integration import MCPIntegration

            MCPIntegration(enable_fallback=False)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
        )
        assert completed.returncode == 0, completed.stderr


class TestNetworkToolsDoNotRequireScipy:
    @pytest.mark.parametrize(
        "subject",
        [
            "src.mcp.tools.academic_search_tool",
            "src.mcp.tools.citation_tool",
            "src.mcp.tools.knowledge_graph_tool",
        ],
    )
    def test_importing_a_network_tool_module_does_not_import_scipy(
        self, subject: str
    ) -> None:
        _import_with_banned(subject, {"scipy", "fastmcp"})

    def test_importing_the_tools_package_does_not_import_scipy(self) -> None:
        """The package itself must not eagerly pull the statistics dependency."""

        _import_with_banned("src.mcp.tools", {"scipy", "fastmcp"})

    @pytest.mark.parametrize(
        "name", ["AcademicSearchTool", "CitationTool", "KnowledgeGraphTool"]
    )
    def test_resolving_an_export_by_name_does_not_import_scipy(self, name: str) -> None:
        """The form `src/mcp/client.py` actually uses.

        Importing the package and importing a submodule both leave the lazy
        `__getattr__` untouched, so neither proves anything about it — a
        mutation that made `__getattr__` pull `statistics_tool` on every
        lookup survived both of the tests above. This is the one that fails.
        """

        script = textwrap.dedent(_POISON).format(
            banned={"scipy", "fastmcp"}, subject=f"src.mcp.tools.{name}"
        )
        script += f"\nfrom src.mcp.tools import {name}\nassert {name} is not None\n"
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
        )
        assert completed.returncode == 0, completed.stderr

    def test_the_package_still_exposes_statistics_tool(self) -> None:
        """Laziness must not become absence.

        `src.mcp.client` imports `StatisticsTool` from the package by name, so
        a decoupling that dropped the export would move the breakage rather
        than remove it.
        """

        pytest.importorskip("scipy")
        from src.mcp.tools import StatisticsTool

        assert StatisticsTool.__name__ == "StatisticsTool"
