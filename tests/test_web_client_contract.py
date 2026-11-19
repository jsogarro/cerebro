"""The web client may only call HTTP and WebSocket paths the server mounts.

Scans the client's ``apiClient.<verb>(...)`` call sites and its WebSocket URL
and compares them with the mounted OpenAPI paths and WebSocket routes.
"""

import re
from pathlib import Path

import pytest

from src.api.main import app

WEB_API = Path(__file__).resolve().parents[1] / "cerebro" / "web" / "src" / "api"
API_PREFIX = "/api/v1"

_CALL = re.compile(
    r"apiClient\s*\.\s*(get|post|put|patch|delete)\s*(?:<[^>(]*>)?\(\s*"
    r"(?P<q>[`'\"])(?P<path>.*?)(?P=q)",
    re.S,
)
_RUN_RESOURCE_CALL = re.compile(r"fetchRunCollection\(\s*runId,\s*'(\w+)'")
_WS_PATH = re.compile(r"\$\{host\}(/ws[\w/]*)")

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
}
# GET /runs/{id}/events is mounted but streams SSE; the client reads JSON.
KNOWN_UNSERVED.add(("GET", "/runs/{}/events"))


def _normalise(path: str) -> str:
    path = path.split("?")[0]
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{}", path)


def _client_calls() -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    for source in sorted(WEB_API.glob("*.ts")):
        text = re.sub(r"^\s*//.*$", "", source.read_text(), flags=re.M)
        resources = _RUN_RESOURCE_CALL.findall(text)
        for match in _CALL.finditer(text):
            raw = match.group("path")
            if "${resource}" in raw:
                paths = [raw.replace("${resource}", name) for name in resources]
            else:
                paths = [raw]
            calls.update((match.group(1).upper(), _normalise(p)) for p in paths)
    return calls


def _served() -> set[tuple[str, str]]:
    served: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(API_PREFIX + "/"):
            continue
        for method in operations:
            served.add((method.upper(), _normalise(path[len(API_PREFIX) :])))
    return served


def test_client_discovers_call_sites() -> None:
    calls = _client_calls()
    assert ("POST", "/research/projects") in calls
    assert ("GET", "/runs/{}/tasks") in calls


def test_client_only_calls_mounted_paths() -> None:
    unserved = _client_calls() - _served() - KNOWN_UNSERVED
    assert not unserved, (
        f"client calls paths the server does not mount: {sorted(unserved)}"
    )


@pytest.mark.parametrize("entry", sorted(KNOWN_UNSERVED))
def test_known_unserved_entries_are_still_unserved_or_still_called(
    entry: tuple[str, str],
) -> None:
    served = entry in _served()
    called = entry in _client_calls()
    assert called, (
        f"{entry} is no longer called by the client; drop it from KNOWN_UNSERVED"
    )
    if entry == ("GET", "/runs/{}/events"):
        return  # mounted on purpose (SSE); tracked until the client stops reading JSON
    assert not served, f"{entry} is now mounted; drop it from KNOWN_UNSERVED"


def test_websocket_path_is_mounted() -> None:
    text = (WEB_API / "websocket.ts").read_text()
    match = _WS_PATH.search(text)
    assert match, "websocket.ts no longer builds its URL from ${host}/ws..."
    mounted = {getattr(route, "path", "") for route in app.routes}
    assert match.group(1) in mounted
