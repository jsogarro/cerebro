"""Validate the rendered ``k8s/`` kustomization without a cluster.

Renders with ``kustomize build`` and checks that the objects reference each
other correctly and that every container image is pinned by digest. Exits
nonzero on any problem; an unrenderable kustomization is a problem, never a
skip. Usage: verify_deployment_assets.py [--allow-placeholder-digest] [K8S_DIR]
"""

import re
import subprocess  # nosec B404 - runs the pinned kustomize binary only
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
# CD replaces this base placeholder; deploying it would pull nothing real.
PLACEHOLDER_DIGEST = "@sha256:" + "0" * 64


def _exposes_metrics(path: str, path_type: str | None) -> bool:
    """Whether a GCE Ingress path would route ``/metrics`` or a subpath."""
    wildcard = path.endswith("*")
    stem = path.removesuffix("*").rstrip("/")
    if path_type == "Exact" and not wildcard:
        return stem == "/metrics" or stem.startswith("/metrics/")
    if path_type == "Prefix":
        segments = [s for s in stem.split("/") if s]
        return not segments or segments[0] == "metrics"
    # ImplementationSpecific or unset: GCE matches by character prefix.
    return "/metrics".startswith(stem) or stem.startswith("/metrics")


# Kinds whose ``metadata.namespace`` must equal the Namespace object's name.
CLUSTER_SCOPED = {"Namespace", "ClusterSecretStore", "ClusterRole"}


def render(directory: Path) -> list[dict[str, Any]]:
    result = subprocess.run(  # nosec B603
        ["kustomize", "build", str(directory)],  # nosec B607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"kustomize build failed:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["spec"]["template"]["spec"]


def _labels(doc: dict[str, Any]) -> dict[str, str]:
    return doc["spec"]["template"]["metadata"].get("labels", {})


def _secret_names(docs: list[dict[str, Any]]) -> set[str]:
    names = {d["metadata"]["name"] for d in docs if d["kind"] == "Secret"}
    for d in docs:
        if d["kind"] == "ExternalSecret":
            names.add(d["spec"]["target"]["name"])
    return names


def _check_references(docs: list[dict[str, Any]]) -> Iterable[str]:
    named = {(d["kind"], d["metadata"]["name"]) for d in docs}
    secrets = _secret_names(docs)
    for dep in (d for d in docs if d["kind"] == "Deployment"):
        who = f"Deployment/{dep['metadata']['name']}"
        spec = _pod_spec(dep)
        if ("ServiceAccount", spec.get("serviceAccountName")) not in named:
            yield f"{who}: serviceAccountName {spec.get('serviceAccountName')!r} not rendered"
        for vol in spec.get("volumes", []):
            if "persistentVolumeClaim" in vol:
                claim = vol["persistentVolumeClaim"]["claimName"]
                if ("PersistentVolumeClaim", claim) not in named:
                    yield f"{who}: PVC {claim!r} not rendered"
            if "secret" in vol and vol["secret"]["secretName"] not in secrets:
                yield f"{who}: Secret {vol['secret']['secretName']!r} not rendered"
        for c in spec["containers"]:
            for src in c.get("envFrom", []):
                if "configMapRef" in src and (
                    ("ConfigMap", src["configMapRef"]["name"]) not in named
                ):
                    yield f"{who}: ConfigMap {src['configMapRef']['name']!r} not rendered"
                if "secretRef" in src and src["secretRef"]["name"] not in secrets:
                    yield f"{who}: Secret {src['secretRef']['name']!r} not rendered"


def _check_networking(docs: list[dict[str, Any]]) -> Iterable[str]:
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    for dep in deployments:
        selector = dep["spec"]["selector"]["matchLabels"]
        if not selector.items() <= _labels(dep).items():
            yield f"Deployment/{dep['metadata']['name']}: selector does not match pod labels"
    for name, svc in services.items():
        selector = svc["spec"]["selector"]
        matched = [d for d in deployments if selector.items() <= _labels(d).items()]
        if not matched:
            yield f"Service/{name}: selector matches no Deployment pod"
            continue
        ports = {
            p["containerPort"]
            for d in matched
            for c in _pod_spec(d)["containers"]
            for p in c.get("ports", [])
        }
        for p in svc["spec"]["ports"]:
            if p["targetPort"] not in ports:
                yield f"Service/{name}: targetPort {p['targetPort']} is no container port"
    for ing in (d for d in docs if d["kind"] == "Ingress"):
        who = f"Ingress/{ing['metadata']['name']}"
        if "defaultBackend" in ing["spec"]:
            yield f"{who}: defaultBackend exposes /metrics"
        for rule in ing["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                backend = path["backend"]["service"]
                # /metrics is scraped in-cluster; the public Ingress must not route it.
                if _exposes_metrics(path["path"], path.get("pathType")):
                    yield f"{who}: path {path['path']!r} exposes /metrics"
                svc = services.get(backend["name"])
                if svc is None:
                    yield f"Ingress/{ing['metadata']['name']}: Service {backend['name']!r} not rendered"
                elif backend["port"]["number"] not in {
                    p["port"] for p in svc["spec"]["ports"]
                }:
                    yield f"Ingress/{ing['metadata']['name']}: port {backend['port']['number']} not on Service {backend['name']!r}"
    targets = {("Deployment", d["metadata"]["name"]) for d in deployments}
    for hpa in (d for d in docs if d["kind"] == "HorizontalPodAutoscaler"):
        ref = hpa["spec"]["scaleTargetRef"]
        if (ref["kind"], ref["name"]) not in targets:
            yield f"HPA/{hpa['metadata']['name']}: target {ref['kind']}/{ref['name']} not rendered"


def _check_workloads(
    docs: list[dict[str, Any]], allow_placeholder_digest: bool
) -> Iterable[str]:
    for dep in (d for d in docs if d["kind"] == "Deployment"):
        who = f"Deployment/{dep['metadata']['name']}"
        for c in _pod_spec(dep)["containers"]:
            image = c["image"]
            if not DIGEST.search(image):
                yield f"{who}: image {image!r} is not pinned by digest"
            elif image.endswith(PLACEHOLDER_DIGEST) and not allow_placeholder_digest:
                yield f"{who}: image {image!r} carries the placeholder digest"
            if not {"requests", "limits"} <= set(c.get("resources", {})):
                yield f"{who}: container {c['name']} lacks resource requests and limits"


def _check_identity(docs: list[dict[str, Any]]) -> Iterable[str]:
    seen: set[tuple[str, str, str | None]] = set()
    namespaces = {d["metadata"]["name"] for d in docs if d["kind"] == "Namespace"}
    for d in docs:
        meta = d["metadata"]
        key = (d["kind"], meta["name"], meta.get("namespace"))
        if key in seen:
            yield f"{d['kind']}/{meta['name']}: rendered twice"
        seen.add(key)
        if d["kind"] not in CLUSTER_SCOPED and meta.get("namespace") not in namespaces:
            yield f"{d['kind']}/{meta['name']}: namespace {meta.get('namespace')!r} is not a rendered Namespace"


def validate(
    docs: list[dict[str, Any]], allow_placeholder_digest: bool = False
) -> list[str]:
    problems: list[str] = []
    for i, d in enumerate(docs):
        if not (
            d.get("apiVersion") and d.get("kind") and d.get("metadata", {}).get("name")
        ):
            problems.append(f"document {i}: missing apiVersion, kind or metadata.name")
    if problems:
        return problems
    for check in (_check_identity, _check_references, _check_networking):
        problems.extend(check(docs))
    problems.extend(_check_workloads(docs, allow_placeholder_digest))
    return problems


def main(argv: list[str]) -> int:
    # The unedited base carries a placeholder digest; only the pre-deploy check
    # of that base may pass the flag. The edited overlay must not.
    allow = "--allow-placeholder-digest" in argv
    args = [a for a in argv[1:] if a != "--allow-placeholder-digest"]
    directory = Path(args[0]) if args else Path("k8s")
    problems = validate(render(directory), allow_placeholder_digest=allow)
    for problem in problems:
        print(f"ERROR {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
