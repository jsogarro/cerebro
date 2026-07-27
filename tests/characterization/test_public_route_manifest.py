"""Executable completeness inventory for the public query and agent routes."""

import subprocess
import sys
from collections.abc import Mapping
from functools import cache
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIRouter

from src.api.routes import agent_api, query_api
from tests.fixtures.agent_system.baseline import (
    PUBLIC_ROUTE_CHARACTERIZATION_MANIFEST,
)

REPOSITORY_ROOT = Path(__file__).parents[2]


def _route_signatures(router: APIRouter) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }


def _manifest_fixture_node_ids(
    manifest: Mapping[tuple[str, str], str],
) -> set[str]:
    fixture_node_ids: set[str] = set()
    for fixture_node_id in manifest.values():
        relative_path, *node_names = fixture_node_id.split("::")
        fixture_path = (Path(__file__).parent / relative_path).resolve()

        assert fixture_path.is_file(), fixture_node_id
        assert node_names, fixture_node_id
        repository_path = fixture_path.relative_to(REPOSITORY_ROOT)
        fixture_node_ids.add("::".join((repository_path.as_posix(), *node_names)))
    return fixture_node_ids


@cache
def _collect_fixture_node_ids(fixture_paths: tuple[str, ...]) -> frozenset[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            *fixture_paths,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return frozenset(
        line
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    )


def _assert_manifest_fixtures_are_collected(
    manifest: Mapping[tuple[str, str], str],
) -> None:
    expected_node_ids = _manifest_fixture_node_ids(manifest)
    fixture_paths = tuple(
        sorted({node_id.split("::", maxsplit=1)[0] for node_id in expected_node_ids})
    )
    collected_node_ids = _collect_fixture_node_ids(fixture_paths)
    missing_node_ids = expected_node_ids - collected_node_ids

    assert not missing_node_ids, (
        "Manifest references uncollected pytest nodes: "
        + ", ".join(sorted(missing_node_ids))
    )


def test_every_public_query_and_agent_route_has_a_named_characterization_fixture() -> (
    None
):
    registered_routes = _route_signatures(query_api.router) | _route_signatures(
        agent_api.router
    )

    assert registered_routes == set(PUBLIC_ROUTE_CHARACTERIZATION_MANIFEST)
    _assert_manifest_fixtures_are_collected(PUBLIC_ROUTE_CHARACTERIZATION_MANIFEST)


def test_fixture_guard_rejects_a_nonexistent_class_qualified_node() -> None:
    nonexistent_class_reference = {
        (
            "GET",
            "/api/v1/agents/{agent_type}/metrics",
        ): "../test_agent_api.py::MissingTestAgentAPI::test_agent_metrics"
    }

    with pytest.raises(AssertionError, match="MissingTestAgentAPI"):
        _assert_manifest_fixtures_are_collected(nonexistent_class_reference)
