import copy
import importlib.util
import os
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "verify_deployment_assets", ROOT / "scripts" / "verify_deployment_assets.py"
)
verify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify)

ZERO = "sha256:" + "0" * 64
GOOD = "sha256:" + "a" * 64
IMAGE = "gcr.io/p/api"


@pytest.fixture(scope="module")
def rendered() -> list[dict[str, Any]]:
    if shutil.which("kustomize") is None:
        reason = "kustomize is not installed: the k8s manifests are NOT being validated"
        if os.environ.get("CI"):
            pytest.fail(reason)
        warnings.warn(reason, stacklevel=1)
        pytest.skip(reason)
    return verify.render(ROOT / "k8s")


def _kind(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind)


def test_committed_manifests_are_valid(rendered: list[dict[str, Any]]) -> None:
    assert verify.validate(rendered, allow_placeholder_digest=True) == []


def test_placeholder_digest_is_refused_unless_allowed(
    rendered: list[dict[str, Any]],
) -> None:
    problems = verify.validate(rendered)
    assert any("placeholder digest" in p for p in problems), problems


def _break(docs: list[dict[str, Any]], kind: str, mutate: Any) -> list[dict[str, Any]]:
    docs = copy.deepcopy(docs)
    mutate(_kind(docs, kind))
    return docs


MUTATIONS = {
    "tag image": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].update(
            image="gcr.io/p/api:v1"
        ),
        "not pinned by digest",
    ),
    "empty digest": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].update(
            image=f"{IMAGE}@"
        ),
        "not pinned by digest",
    ),
    "short digest": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].update(
            image=f"{IMAGE}@sha256:abc"
        ),
        "not pinned by digest",
    ),
    "non-hex digest": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].update(
            image=f"{IMAGE}@sha256:{'g' * 64}"
        ),
        "not pinned by digest",
    ),
    "zero digest": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].update(
            image=f"{IMAGE}@{ZERO}"
        ),
        "placeholder digest",
    ),
    "service selector": (
        "Service",
        lambda d: d["spec"]["selector"].update(app="nope"),
        "matches no Deployment",
    ),
    "service port": (
        "Service",
        lambda d: d["spec"]["ports"][0].update(targetPort=9),
        "is no container port",
    ),
    "ingress service": (
        "Ingress",
        lambda d: d["spec"]["rules"][0]["http"]["paths"][0]["backend"][
            "service"
        ].update(name="ghost"),
        "not rendered",
    ),
    "hpa target": (
        "HorizontalPodAutoscaler",
        lambda d: d["spec"]["scaleTargetRef"].update(name="ghost"),
        "not rendered",
    ),
    "pvc claim": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["volumes"][-1][
            "persistentVolumeClaim"
        ].update(claimName="ghost"),
        "PVC 'ghost'",
    ),
    "namespace": (
        "Service",
        lambda d: d["metadata"].update(namespace="elsewhere"),
        "is not a rendered Namespace",
    ),
    "ingress exposes metrics": (
        "Ingress",
        lambda d: d["spec"]["rules"][0]["http"]["paths"][0].update(
            path="/", pathType="Prefix"
        ),
        "exposes /metrics",
    ),
    "ingress default backend": (
        "Ingress",
        lambda d: d["spec"].update(
            defaultBackend={"service": {"name": "x", "port": {"number": 80}}}
        ),
        "exposes /metrics",
    ),
    "no limits": (
        "Deployment",
        lambda d: d["spec"]["template"]["spec"]["containers"][0].pop("resources"),
        "lacks resource requests and limits",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_check_can_fail(rendered: list[dict[str, Any]], name: str) -> None:
    kind, mutate, expected = MUTATIONS[name]
    problems = verify.validate(
        _break(rendered, kind, mutate), allow_placeholder_digest=False
    )
    assert any(expected in p for p in problems), problems


@pytest.mark.parametrize(
    ("path", "path_type"),
    [
        ("/*", "ImplementationSpecific"),
        ("/*", None),
        ("/", None),
        ("/metrics/", "Exact"),
        ("/metrics/x", "Exact"),
        ("/metrics/x", "Prefix"),
        ("/metrics", "ImplementationSpecific"),
        ("/metrics/*", None),
    ],
)
def test_ingress_paths_reaching_metrics_are_rejected(
    rendered: list[dict[str, Any]], path: str, path_type: str | None
) -> None:
    def mutate(d: dict[str, Any]) -> None:
        entry = d["spec"]["rules"][0]["http"]["paths"][0]
        entry["path"] = path
        entry.pop("pathType", None)
        if path_type:
            entry["pathType"] = path_type

    problems = verify.validate(
        _break(rendered, "Ingress", mutate), allow_placeholder_digest=True
    )
    assert any("exposes /metrics" in p for p in problems), problems


@pytest.mark.parametrize(
    ("path", "path_type"),
    [("/api", "Prefix"), ("/health", "Exact"), ("/metrics-x", "Exact")],
)
def test_ingress_paths_not_reaching_metrics_are_accepted(
    rendered: list[dict[str, Any]], path: str, path_type: str
) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["spec"]["rules"][0]["http"]["paths"][0].update(path=path, pathType=path_type)

    problems = verify.validate(
        _break(rendered, "Ingress", mutate), allow_placeholder_digest=True
    )
    assert not any("exposes /metrics" in p for p in problems), problems
