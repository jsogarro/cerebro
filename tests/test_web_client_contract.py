"""The web client may only call HTTP and WebSocket paths the server mounts.

Scans every ``.ts``/``.tsx`` file under ``cerebro/web/src`` for HTTP call sites
(``apiClient.<verb>``, ``apiClient.request``/``apiClient({...})``, ``fetch``,
``EventSource``, bare ``axios``) and compares them with the mounted OpenAPI
paths. A call whose path cannot be read statically fails the test rather than
being skipped.

Limits: only method and path are compared (parameter names and payload shapes
are not), and a path hidden behind a helper that takes it as an argument is
reported as unresolved.
"""

import re
from pathlib import Path

import pytest

from src.api.main import app

WEB_SRC = Path(__file__).resolve().parents[1] / "cerebro" / "web" / "src"
API_PREFIX = "/api/v1"
VERBS = "get|post|put|patch|delete|head|options"

# Surfaces the client renders but the server has no counterpart for yet (there
# is no run/workflow read API, memory explorer, or agent log feed). Each entry
# is a known gap, not a licence: the test fails if one becomes served, so this
# list can only shrink.
KNOWN_UNSERVED = {
    ("GET", "/runs"),
    ("POST", "/runs"),
    ("GET", "/runs/{}"),
    ("POST", "/runs/{}/cancel"),
    ("GET", "/runs/{}/tasks"),
    ("GET", "/runs/{}/evidence"),
    ("GET", "/runs/{}/artifacts"),
    ("GET", "/runs/{}/evaluations"),
    ("GET", "/workflows"),
    ("GET", "/workflows/{}"),
    ("GET", "/memory/nodes"),
    ("GET", "/agents/{}/logs"),
    # Mounted, but as an SSE stream; the client reads it as JSON.
    ("GET", "/runs/{}/events"),
}
SSE_MOUNTED_BUT_MISREAD = {("GET", "/runs/{}/events")}

_RUN_RESOURCE_CALL = re.compile(r"fetchRunCollection\(\s*runId,\s*'(\w+)'")
_WS_PATH = re.compile(r"\$\{host\}(/ws[\w/]*)")
# <call prefix> optionally followed by a generic, then the opening paren.
_CALLS = [
    (re.compile(rf"\bapiClient\s*\.\s*(?P<verb>{VERBS})\s*(?:<[^>(]*>)?\s*\("), "verb"),
    (re.compile(r"\bapiClient\s*\.\s*request\s*(?:<[^>(]*>)?\s*\("), "request"),
    (re.compile(r"\bapiClient\s*(?:<[^>(]*>)?\s*\("), "request"),
    (re.compile(r"\bfetch\s*\("), "fetch"),
    (re.compile(r"\bnew\s+EventSource\s*\("), "sse"),
    (re.compile(rf"\baxios\s*\.\s*(?P<verb>{VERBS})\s*(?:<[^>(]*>)?\s*\("), "verb"),
    (re.compile(r"\baxios\s*\("), "request"),
]


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<![:\w])//.*$", "", text, flags=re.M)


def _literal(text: str, start: int) -> str | None:
    """The string literal starting at ``start``, if it is the whole argument."""
    text_len = len(text)
    while start < text_len and text[start].isspace():
        start += 1
    if start >= text_len or text[start] not in "'\"`":
        return None
    quote = text[start]
    end = start + 1
    while end < text_len and text[end] != quote:
        end += 2 if text[end] == "\\" else 1
    rest = text[end + 1 :].lstrip()
    if end >= text_len or not rest or rest[0] not in ",)":
        return None  # concatenation or an expression, not a plain literal
    return text[start + 1 : end]


def _normalise(path: str) -> str:
    path = path.split("?")[0]
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{}", path)


def _request_object(text: str, start: int) -> tuple[str | None, str]:
    """``(url, method)`` from an ``apiClient.request({...})``-style config object."""
    window = text[start : start + 400]
    url = re.search(r"\burl\s*:\s*", window)
    method = re.search(rf"\bmethod\s*:\s*['\"]({VERBS})['\"]", window, re.I)
    return (
        _literal(window, url.end()) if url else None,
        method.group(1).upper() if method else "GET",
    )


def _scan() -> tuple[set[tuple[str, str]], list[str]]:
    calls: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    for source in sorted([*WEB_SRC.rglob("*.ts"), *WEB_SRC.rglob("*.tsx")]):
        text = _strip_comments(source.read_text())
        resources = _RUN_RESOURCE_CALL.findall(text)
        for pattern, kind in _CALLS:
            for match in pattern.finditer(text):
                where = f"{source.relative_to(WEB_SRC)}: {match.group(0).strip()}"
                if kind == "request":
                    raw, verb = _request_object(text, match.end())
                else:
                    raw = _literal(text, match.end())
                    verb = (
                        "GET"
                        if kind in {"fetch", "sse"}
                        else match.group("verb").upper()
                    )
                if raw is None:
                    unresolved.append(where)
                    continue
                if raw.startswith(API_PREFIX + "/"):
                    raw = raw[len(API_PREFIX) :]
                elif kind in {"fetch", "sse"} or not raw.startswith("/"):
                    unresolved.append(f"{where} (not a {API_PREFIX} path)")
                    continue
                paths = (
                    [raw.replace("${resource}", name) for name in resources]
                    if "${resource}" in raw
                    else [raw]
                )
                calls.update((verb, _normalise(p)) for p in paths)
        if "new WebSocket(" in text and source.name != "websocket.ts":
            unresolved.append(f"{source.relative_to(WEB_SRC)}: new WebSocket(")
    return calls, unresolved


def _served() -> set[tuple[str, str]]:
    served: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(API_PREFIX + "/"):
            continue
        for method in operations:
            served.add((method.upper(), _normalise(path[len(API_PREFIX) :])))
    return served


def test_client_discovers_call_sites() -> None:
    calls, _ = _scan()
    assert ("POST", "/research/projects") in calls
    assert ("GET", "/runs/{}/tasks") in calls


def test_every_client_call_path_is_statically_readable() -> None:
    _, unresolved = _scan()
    assert not unresolved, (
        f"cannot read these client call paths statically: {unresolved}"
    )


def test_client_only_calls_mounted_paths() -> None:
    calls, _ = _scan()
    unserved = calls - _served() - KNOWN_UNSERVED
    assert not unserved, (
        f"client calls paths the server does not mount: {sorted(unserved)}"
    )


@pytest.mark.parametrize("entry", sorted(KNOWN_UNSERVED))
def test_known_unserved_entries_are_still_called_and_unserved(
    entry: tuple[str, str],
) -> None:
    calls, _ = _scan()
    assert entry in calls, (
        f"{entry} is no longer called by the client; drop it from KNOWN_UNSERVED"
    )
    if entry not in SSE_MOUNTED_BUT_MISREAD:
        assert entry not in _served(), (
            f"{entry} is now mounted; drop it from KNOWN_UNSERVED"
        )


def test_websocket_path_is_mounted() -> None:
    match = _WS_PATH.search((WEB_SRC / "api" / "websocket.ts").read_text())
    assert match, "websocket.ts no longer builds its URL from ${host}/ws..."
    mounted = {getattr(route, "path", "") for route in app.routes}
    assert match.group(1) in mounted
