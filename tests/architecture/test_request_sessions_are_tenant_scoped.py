"""A request handler that opens the shared session must also resolve the tenant.

Under forced row-level security a session with no tenant reads nothing and
cannot insert, and nothing in the handler looks wrong. A handler that depends
on ``SessionDep`` or ``get_session`` therefore also has to depend on
``get_tenant_context`` (directly, through an alias, or through a dependency
that does), unless it only touches tables that carry no row policy; those are
listed below with the tables they touch.

This is a static check on the route modules. Background jobs and the lifespan
are covered by the integration suites that run as ``cerebro_app``.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
MODULES = (
    *sorted((SRC / "api" / "routes").glob("*.py")),
    *sorted((SRC / "api" / "auth").glob("*.py")),
    SRC / "middleware" / "auth_middleware.py",
)
TENANT = "get_tenant_context"
SESSION_DEP = "SessionDep"

# Handlers whose session reaches only tables without a row policy.
UNPOLICED = {
    "admin_security.py": "audit_logs, security_alerts, users",
    "auth_router.py": "users",
    "auth_middleware.py": "users",
}
# Handlers that set the tenant themselves rather than through the dependency.
SETS_TENANT_ITSELF = {("users.py", "delete_user_gdpr")}


def _depends_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id == SESSION_DEP:
        return "get_session"
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and node.args
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    return None


def _depends_in(node: ast.AST) -> set[str]:
    return {t for n in ast.walk(node) if (t := _depends_target(n))}


def _aliases(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level ``Name = Annotated[T, Depends(f)]`` or ``SessionDep`` aliases."""
    return {
        target.id: _depends_in(stmt.value)
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Name)
    }


def _functions(tree: ast.Module) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def _direct_dependencies(
    fn: ast.AsyncFunctionDef | ast.FunctionDef, aliases: dict[str, set[str]]
) -> set[str]:
    found = _depends_in(fn.args)
    found |= {
        dep
        for n in ast.walk(fn.args)
        if isinstance(n, ast.Name)
        for dep in aliases.get(n.id, ())
    }
    for decorator in fn.decorator_list:
        found |= _depends_in(decorator)
    return found


def _closure(name: str, functions: dict, aliases: dict, seen: set[str]) -> set[str]:
    if name in seen or name not in functions:
        return set()
    seen.add(name)
    direct = _direct_dependencies(functions[name], aliases)
    for dep in list(direct):
        direct |= _closure(dep, functions, aliases, seen)
    return direct


def _is_handler(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in {"get", "post", "put", "patch", "delete", "websocket"}
        for d in fn.decorator_list
    )


def _scan_tree(tree: ast.Module, module_name: str) -> tuple[list[str], list[str]]:
    checked: list[str] = []
    violations: list[str] = []
    functions, aliases = _functions(tree), _aliases(tree)
    for name, fn in functions.items():
        if not _is_handler(fn):
            continue
        if (module_name, name) in SETS_TENANT_ITSELF:
            continue
        closure = _closure(name, functions, aliases, set())
        if "get_session" in closure:
            label = f"{module_name}:{name}"
            checked.append(label)
            if TENANT not in closure:
                violations.append(label)
    return checked, violations


def _scan_modules() -> tuple[list[str], list[str]]:
    checked: list[str] = []
    violations: list[str] = []
    for path in MODULES:
        if path.name in UNPOLICED:
            continue
        tree = ast.parse(path.read_text())
        mod_checked, mod_violations = _scan_tree(tree, path.name)
        checked.extend(mod_checked)
        violations.extend(mod_violations)
    return checked, violations


def test_every_handler_that_uses_the_session_resolves_the_tenant() -> None:
    checked, violations = _scan_modules()
    assert checked, "guard matched zero session-using handlers"
    assert any(h.startswith("tokens_router.py:") for h in checked)
    assert violations == []


def test_guard_catches_unscoped_session_dependencies() -> None:
    sample = """
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

SessionAlias = Annotated[AsyncSession, SessionDep]

@router.get("/default")
async def unscoped_default(db: AsyncSession = SessionDep): ...

@router.get("/annotated")
async def unscoped_annotated(db: Annotated[AsyncSession, SessionDep]): ...

@router.get("/alias")
async def unscoped_alias(db: SessionAlias): ...

@router.get("/depends")
async def unscoped_depends(db: AsyncSession = Depends(get_session)): ...

@router.get("/scoped")
async def scoped(
    db: AsyncSession = SessionDep,
    tenant=Depends(get_tenant_context),
): ...
"""
    checked, violations = _scan_tree(ast.parse(sample), "sample.py")
    assert set(checked) == {
        "sample.py:unscoped_default",
        "sample.py:unscoped_annotated",
        "sample.py:unscoped_alias",
        "sample.py:unscoped_depends",
        "sample.py:scoped",
    }
    assert set(violations) == {
        "sample.py:unscoped_default",
        "sample.py:unscoped_annotated",
        "sample.py:unscoped_alias",
        "sample.py:unscoped_depends",
    }


def test_the_exemptions_still_name_real_modules() -> None:
    names = {p.name for p in MODULES}
    assert set(UNPOLICED) | {m for m, _ in SETS_TENANT_ITSELF} <= names


def test_a_handler_that_sets_the_tenant_itself_really_does() -> None:
    for module, handler in SETS_TENANT_ITSELF:
        tree = ast.parse(next(p for p in MODULES if p.name == module).read_text())
        calls = {
            n.func.id
            for n in ast.walk(_functions(tree)[handler])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "set_postgres_tenant_context" in calls, handler
