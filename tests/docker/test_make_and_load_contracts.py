"""Make targets and the load test must point at things that exist."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml
from starlette.routing import Match

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
LOCUSTFILE = REPO_ROOT / "tests" / "load" / "locustfile.py"


def _k8s_names() -> tuple[str, str]:
    kustomization = yaml.safe_load((REPO_ROOT / "k8s/kustomization.yaml").read_text())
    deployment = yaml.safe_load((REPO_ROOT / "k8s/deployment-api.yaml").read_text())
    return kustomization["namespace"], deployment["metadata"]["name"]


def test_make_paths_exist() -> None:
    paths = re.findall(r"\b(?:tests|examples|scripts|k8s)/[\w./-]+", MAKEFILE)
    missing = sorted({p for p in paths if not (REPO_ROOT / p).exists()})
    assert not missing


def test_k8s_targets_use_the_manifest_names() -> None:
    namespace, deployment = _k8s_names()
    for target in ("k8s-status", "k8s-logs"):
        body = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", MAKEFILE, re.M)
        assert body, target
        assert f"-n {namespace}" in body.group(1)
    assert f"deployment/{deployment}" in MAKEFILE


def test_make_services_are_defined_in_the_compose_file() -> None:
    services: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.production.yml"):
        compose = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
        services |= set(compose["services"])
    used = re.findall(
        r"compose\.sh .*?(?:logs -f|exec) (?:-\w+ )*([\w-]+)$", MAKEFILE, re.M
    )
    assert used
    assert set(used) <= services


def test_make_does_not_call_compose_v1() -> None:
    assert "docker-compose " not in MAKEFILE.replace("docker-compose.", "")


def _locust_requests() -> list[tuple[str, str]]:
    requests = []
    for node in ast.walk(ast.parse(LOCUSTFILE.read_text(encoding="utf-8"))):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "client"
            and node.args
        ):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant):
            path = arg.value
        else:
            path = "".join(
                part.value if isinstance(part, ast.Constant) else "x"
                for part in arg.values
            )
        requests.append((node.func.attr.upper(), path))
    return requests


def test_locust_requests_hit_mounted_routes() -> None:
    from src.api.main import app

    requests = _locust_requests()
    assert requests
    unmounted = [
        (method, path)
        for method, path in requests
        if not any(
            route.matches({"type": "http", "path": path, "method": method})[0]
            == Match.FULL
            for route in app.routes
        )
    ]
    assert not unmounted


def test_production_api_persists_source_snapshots_on_a_volume() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    directory = re.search(r"^ENV SOURCE_SNAPSHOT_DIR=(\S+)$", dockerfile, re.M)
    assert directory, "production image must default SOURCE_SNAPSHOT_DIR"
    assert 'chown app:app "$SOURCE_SNAPSHOT_DIR"' in dockerfile

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )
    api = compose["services"]["api"]
    mounts = {v.split(":")[1]: v.split(":")[0] for v in api["volumes"]}
    volume = mounts[directory.group(1)]
    assert volume in compose["volumes"]


def test_kubernetes_api_mounts_a_shared_claim_at_the_snapshot_directory() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    directory = re.search(r"^ENV SOURCE_SNAPSHOT_DIR=(\S+)$", dockerfile, re.M)
    assert directory
    deployment = yaml.safe_load((REPO_ROOT / "k8s/deployment-api.yaml").read_text())
    spec = deployment["spec"]["template"]["spec"]
    mount = next(
        m
        for m in spec["containers"][0]["volumeMounts"]
        if m["mountPath"] == directory.group(1)
    )
    volume = next(v for v in spec["volumes"] if v["name"] == mount["name"])
    claim = yaml.safe_load((REPO_ROOT / "k8s/pvc-source-snapshots.yaml").read_text())
    assert volume["persistentVolumeClaim"]["claimName"] == claim["metadata"]["name"]
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]
