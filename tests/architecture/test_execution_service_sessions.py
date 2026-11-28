"""The execution service opens no session without a tenant.

Under forced row-level security a bare ``session_factory()`` reads nothing and
cannot insert, and nothing about the call looks wrong in review. Every session
is therefore opened through ``_tenant_session``, except the two cross-tenant
scans that connect as the maintenance role.
"""

import ast
from pathlib import Path

SERVICE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "api"
    / "services"
    / "direct_execution_service.py"
)
MAINTENANCE_SCANS = frozenset({"reconcile_expired_runs", "restore_active_executions"})


def _calls_of(attribute: str) -> list[str]:
    """Names of the functions that call ``self.<attribute>(...)``."""
    callers: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function = "<module>"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._enter(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._enter(node)

        def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            outer, self.function = self.function, node.name
            self.generic_visit(node)
            self.function = outer

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == attribute
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
            ):
                callers.append(self.function)
            self.generic_visit(node)

    Visitor().visit(ast.parse(SERVICE.read_text()))
    return callers


def test_no_session_is_opened_from_the_application_factory_directly() -> None:
    assert _calls_of("session_factory") == []


def test_only_the_cross_tenant_scans_use_the_maintenance_factory() -> None:
    callers = set(_calls_of("maintenance_session_factory"))

    assert callers == MAINTENANCE_SCANS
