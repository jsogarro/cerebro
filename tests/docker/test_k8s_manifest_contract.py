"""The Kubernetes manifests must be able to run the current app."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.core.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
K8S = REPO_ROOT / "k8s"
JWT_SECRET_NAME = "research-platform-jwt-keys"


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def test_api_mounts_the_jwt_keys_where_config_expects_them() -> None:
    (deployment,) = _docs(K8S / "deployment-api.yaml")
    spec = deployment["spec"]["template"]["spec"]
    mounts = {m["mountPath"]: m for m in spec["containers"][0]["volumeMounts"]}
    private_dir = str(Path(settings.JWT_PRIVATE_KEY_PATH).parent)
    public_dir = str(Path(settings.JWT_PUBLIC_KEY_PATH).parent)

    assert private_dir == public_dir
    mount = mounts[private_dir]
    assert mount["readOnly"] is True

    volume = next(v for v in spec["volumes"] if v["name"] == mount["name"])
    assert volume["secret"]["secretName"] == JWT_SECRET_NAME
    assert volume["secret"]["defaultMode"] == 0o400


def test_jwt_secret_provides_the_files_config_names_without_real_keys() -> None:
    (external,) = [
        d
        for d in _docs(K8S / "external-secrets.yaml")
        if d["metadata"]["name"] == JWT_SECRET_NAME
    ]
    keys = {item["secretKey"] for item in external["spec"]["data"]}

    assert keys == {
        Path(settings.JWT_PRIVATE_KEY_PATH).name,
        Path(settings.JWT_PUBLIC_KEY_PATH).name,
    }
    assert "BEGIN" not in (K8S / "external-secrets.yaml").read_text(encoding="utf-8")


def test_no_manifest_references_a_missing_dockerfile_or_image() -> None:
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    deployed_images = {
        c["image"].rsplit(":", 1)[0]
        for path in K8S.glob("deployment-*.yaml")
        for d in _docs(path)
        for c in d["spec"]["template"]["spec"]["containers"]
    }
    for override in kustomization["images"]:
        assert override["name"] in deployed_images, override
    for resource in kustomization["resources"]:
        assert (K8S / resource).is_file(), resource

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    )
    for name, service in compose["services"].items():
        build = service.get("build")
        if build:
            dockerfile = REPO_ROOT / build.get("context", ".") / build["dockerfile"]
            assert dockerfile.is_file(), f"{name}: {build['dockerfile']} is missing"


def test_no_manifest_targets_the_removed_worker() -> None:
    text = "\n".join(p.read_text() for p in K8S.glob("*.yaml"))
    assert "research-worker" not in text
    assert "research-platform-worker" not in text
