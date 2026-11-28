import copy
import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "verify_deployment_assets", ROOT / "scripts" / "verify_deployment_assets.py"
)
verify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify)

pytestmark = pytest.mark.skipif(
    shutil.which("kustomize") is None, reason="kustomize not installed"
)


@pytest.fixture(scope="module")
def rendered() -> list[dict[str, Any]]:
    return verify.render(ROOT / "k8s")


def _kind(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind)


def test_committed_manifests_are_valid(rendered: list[dict[str, Any]]) -> None:
    assert verify.validate(rendered) == []


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
    problems = verify.validate(_break(rendered, kind, mutate))
    assert any(expected in p for p in problems), problems
