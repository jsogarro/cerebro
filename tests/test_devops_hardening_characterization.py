"""Characterization + acceptance tests for group-005 DevOps hardening.

Two test families:

* **Invariants** (no marker): assertions that hold *both* before and after the
  K8s securityContext / CI ``|| true`` refactor. They must pass on unmodified
  code (Feathers characterization style) and continue to pass after each
  Fowler step in Phase 4.
* **Acceptance** (``@pytest.mark.acceptance``): TDD-style specifications for
  the desired end state. They are *expected to FAIL on current code* and
  *expected to PASS after the refactor lands*. CI gates on
  ``pytest -m acceptance`` returning success once Phase 4 completes.

Both families read manifests directly from the repository — no mocks,
no fixtures of payloads — because the targets ARE the YAML files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = REPO_ROOT / "k8s"
DEPLOY_API = K8S_DIR / "deployment-api.yaml"
DEPLOY_WORKER = K8S_DIR / "deployment-worker.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _pod_spec(deployment: dict[str, Any]) -> dict[str, Any]:
    return deployment["spec"]["template"]["spec"]


def _container(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    for c in _pod_spec(deployment)["containers"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"container {name!r} not found in deployment")


# ---------------------------------------------------------------------------
# K8s deployment invariants — must hold pre AND post refactor
# ---------------------------------------------------------------------------


class TestK8sDeploymentInvariants:
    """Lock down the parts of the deployment manifests that MUST NOT change.

    Selectors, names, replicas, container ports, namespaces — these are the
    public contract consumed by Service/Ingress/HPA/NetworkPolicy.
    """

    def test_api_deployment_parses(self) -> None:
        doc = _load_yaml(DEPLOY_API)
        assert doc["kind"] == "Deployment"
        assert doc["apiVersion"] == "apps/v1"

    def test_worker_deployment_parses(self) -> None:
        doc = _load_yaml(DEPLOY_WORKER)
        assert doc["kind"] == "Deployment"
        assert doc["apiVersion"] == "apps/v1"

    def test_api_metadata_contract(self) -> None:
        doc = _load_yaml(DEPLOY_API)
        assert doc["metadata"]["name"] == "research-api"
        assert doc["metadata"]["namespace"] == "research-platform"
        assert doc["metadata"]["labels"]["app"] == "research-api"
        assert doc["metadata"]["labels"]["component"] == "api"

    def test_worker_metadata_contract(self) -> None:
        doc = _load_yaml(DEPLOY_WORKER)
        assert doc["metadata"]["name"] == "research-worker"
        assert doc["metadata"]["namespace"] == "research-platform"
        assert doc["metadata"]["labels"]["app"] == "research-worker"
        assert doc["metadata"]["labels"]["component"] == "worker"

    def test_api_selector_unchanged(self) -> None:
        doc = _load_yaml(DEPLOY_API)
        assert doc["spec"]["selector"]["matchLabels"] == {"app": "research-api"}

    def test_worker_selector_unchanged(self) -> None:
        doc = _load_yaml(DEPLOY_WORKER)
        assert doc["spec"]["selector"]["matchLabels"] == {"app": "research-worker"}

    def test_api_replicas_unchanged(self) -> None:
        assert _load_yaml(DEPLOY_API)["spec"]["replicas"] == 3

    def test_worker_replicas_unchanged(self) -> None:
        assert _load_yaml(DEPLOY_WORKER)["spec"]["replicas"] == 5

    def test_api_container_port_unchanged(self) -> None:
        doc = _load_yaml(DEPLOY_API)
        api = _container(doc, "api")
        ports = api["ports"]
        assert any(p["containerPort"] == 8000 for p in ports)

    def test_api_pod_serviceaccount_unchanged(self) -> None:
        spec = _pod_spec(_load_yaml(DEPLOY_API))
        assert spec["serviceAccountName"] == "research-platform-sa"

    def test_worker_pod_serviceaccount_unchanged(self) -> None:
        spec = _pod_spec(_load_yaml(DEPLOY_WORKER))
        assert spec["serviceAccountName"] == "research-platform-sa"

    def test_api_tmp_volume_writable_path(self) -> None:
        """readOnlyRootFilesystem requires a writable mount; tmp emptyDir
        already covers /tmp. This invariant must remain true post-refactor."""
        doc = _load_yaml(DEPLOY_API)
        api = _container(doc, "api")
        mounts = {m["name"]: m["mountPath"] for m in api["volumeMounts"]}
        assert mounts.get("tmp") == "/tmp"
        volumes = {v["name"]: v for v in _pod_spec(doc)["volumes"]}
        assert "emptyDir" in volumes["tmp"]

    def test_worker_tmp_volume_writable_path(self) -> None:
        doc = _load_yaml(DEPLOY_WORKER)
        worker = _container(doc, "worker")
        mounts = {m["name"]: m["mountPath"] for m in worker["volumeMounts"]}
        assert mounts.get("tmp") == "/tmp"
        volumes = {v["name"]: v for v in _pod_spec(doc)["volumes"]}
        assert "emptyDir" in volumes["tmp"]


# ---------------------------------------------------------------------------
# CI workflow invariants — must hold pre AND post refactor
# ---------------------------------------------------------------------------


class TestCIWorkflowInvariants:
    """Lock down the structural parts of ci.yml: job names, dependency graph,
    and the security-tool invocations themselves. The refactor only removes
    advisory exit-code suppression; the actual scanners must still run."""

    @pytest.fixture(scope="class")
    def ci(self) -> dict[str, Any]:
        return _load_yaml(CI_WORKFLOW)

    def test_ci_workflow_parses(self, ci: dict[str, Any]) -> None:
        assert ci["name"] == "CI Pipeline"

    def test_required_jobs_present(self, ci: dict[str, Any]) -> None:
        expected = {
            "lint",
            "test",
            "test-temporal",
            "test-cli",
            "security",
            "validate-docker",
            "validate-k8s",
            "frontend-ci",
            "all-checks",
        }
        assert expected.issubset(set(ci["jobs"].keys()))

    def test_all_checks_dependency_graph_unchanged(self, ci: dict[str, Any]) -> None:
        all_checks = ci["jobs"]["all-checks"]
        needs = set(all_checks["needs"])
        assert needs == {
            "lint",
            "test",
            "test-temporal",
            "test-cli",
            "security",
            "validate-docker",
            "validate-k8s",
            "frontend-ci",
        }

    def test_security_job_invokes_bandit(self, ci: dict[str, Any]) -> None:
        steps = ci["jobs"]["security"]["steps"]
        runs = [s.get("run", "") for s in steps]
        assert any("bandit" in r for r in runs)

    def test_security_job_invokes_safety(self, ci: dict[str, Any]) -> None:
        runs = [s.get("run", "") for s in ci["jobs"]["security"]["steps"]]
        assert any("safety" in r for r in runs)

    def test_security_job_invokes_pip_audit(self, ci: dict[str, Any]) -> None:
        runs = [s.get("run", "") for s in ci["jobs"]["security"]["steps"]]
        assert any("pip-audit" in r for r in runs)

    def test_validate_docker_job_invokes_hadolint(self, ci: dict[str, Any]) -> None:
        runs = [s.get("run", "") for s in ci["jobs"]["validate-docker"]["steps"]]
        assert any("hadolint" in r for r in runs)

    def test_validate_docker_lints_main_dockerfile(self, ci: dict[str, Any]) -> None:
        """Either an explicit reference to ``Dockerfile`` or a loop that would
        cover it. Locks in that hadolint targets the production Dockerfile."""
        runs = "\n".join(s.get("run", "") for s in ci["jobs"]["validate-docker"]["steps"])
        assert "Dockerfile" in runs

    def test_security_artifact_upload_present(self, ci: dict[str, Any]) -> None:
        steps = ci["jobs"]["security"]["steps"]
        upload = next(
            (s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")),
            None,
        )
        assert upload is not None
        assert upload["with"]["name"] == "security-reports"


# ---------------------------------------------------------------------------
# Acceptance tests — fail before refactor, pass after refactor
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
class TestK8sSecurityContextAcceptance:
    """Spec for the K8s securityContext additions (group-005 brief)."""

    @pytest.mark.parametrize("path", [DEPLOY_API, DEPLOY_WORKER], ids=["api", "worker"])
    def test_pod_runs_as_non_root(self, path: Path) -> None:
        spec = _pod_spec(_load_yaml(path))
        sc = spec.get("securityContext", {})
        assert sc.get("runAsNonRoot") is True

    @pytest.mark.parametrize("path", [DEPLOY_API, DEPLOY_WORKER], ids=["api", "worker"])
    def test_pod_run_as_user_set_per_brief(self, path: Path) -> None:
        spec = _pod_spec(_load_yaml(path))
        sc = spec.get("securityContext", {})
        assert sc.get("runAsUser") == 1000

    @pytest.mark.parametrize("path", [DEPLOY_API, DEPLOY_WORKER], ids=["api", "worker"])
    def test_pod_seccomp_runtime_default(self, path: Path) -> None:
        spec = _pod_spec(_load_yaml(path))
        sc = spec.get("securityContext", {})
        seccomp = sc.get("seccompProfile", {})
        assert seccomp.get("type") == "RuntimeDefault"

    @pytest.mark.parametrize(
        "path,name",
        [(DEPLOY_API, "api"), (DEPLOY_WORKER, "worker")],
        ids=["api", "worker"],
    )
    def test_container_drops_all_capabilities(self, path: Path, name: str) -> None:
        c = _container(_load_yaml(path), name)
        sc = c.get("securityContext", {})
        caps = sc.get("capabilities", {})
        assert caps.get("drop") == ["ALL"]

    @pytest.mark.parametrize(
        "path,name",
        [(DEPLOY_API, "api"), (DEPLOY_WORKER, "worker")],
        ids=["api", "worker"],
    )
    def test_container_no_privilege_escalation(self, path: Path, name: str) -> None:
        c = _container(_load_yaml(path), name)
        sc = c.get("securityContext", {})
        assert sc.get("allowPrivilegeEscalation") is False

    @pytest.mark.parametrize(
        "path,name",
        [(DEPLOY_API, "api"), (DEPLOY_WORKER, "worker")],
        ids=["api", "worker"],
    )
    def test_container_read_only_root_filesystem(self, path: Path, name: str) -> None:
        c = _container(_load_yaml(path), name)
        sc = c.get("securityContext", {})
        assert sc.get("readOnlyRootFilesystem") is True


@pytest.mark.acceptance
class TestCIBlockingGatesAcceptance:
    """Spec for CI ``|| true`` removal — security gates must be blocking."""

    @pytest.fixture(scope="class")
    def ci_text(self) -> str:
        return CI_WORKFLOW.read_text()

    @pytest.fixture(scope="class")
    def ci(self) -> dict[str, Any]:
        return _load_yaml(CI_WORKFLOW)

    def _runs_in_job(self, ci: dict[str, Any], job: str) -> str:
        return "\n".join(s.get("run", "") for s in ci["jobs"][job]["steps"])

    def test_no_or_true_in_security_job(self, ci: dict[str, Any]) -> None:
        runs = self._runs_in_job(ci, "security")
        assert "|| true" not in runs, (
            "security job must not suppress scanner exit codes; remove `|| true`"
        )

    def test_no_or_true_in_validate_docker_job(self, ci: dict[str, Any]) -> None:
        runs = self._runs_in_job(ci, "validate-docker")
        assert "|| true" not in runs, (
            "validate-docker hadolint must be blocking; remove `|| true`"
        )

    def test_security_artifact_upload_runs_on_failure(self, ci: dict[str, Any]) -> None:
        """When bandit fails (no `|| true`), artifact upload must still run.
        Enforced via `if: always()` on the upload step."""
        steps = ci["jobs"]["security"]["steps"]
        upload = next(
            s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")
        )
        if_clause = str(upload.get("if", "")).strip().lower()
        assert "always()" in if_clause, (
            "upload-artifact must declare `if: always()` so security reports "
            "survive a failing bandit/safety/pip-audit run"
        )

    def test_hadolint_robust_to_missing_files(self, ci: dict[str, Any], ci_text: str) -> None:
        """Worker Dockerfile is currently missing from the repo. The hadolint
        invocation must not hard-fail purely because a target file is absent
        (that's an inventory issue, not a security finding). Acceptable
        patterns: `if [ -f ... ]` guard, `[ -e ... ] &&`, or a for-loop with
        `[ -f ]` test."""
        runs = self._runs_in_job(ci, "validate-docker")
        # At least one defensive guard present
        defensive_pattern = re.compile(r"\[\s*-f\s+|\[\s*-e\s+|test\s+-f\s+|test\s+-e\s+")
        assert defensive_pattern.search(runs), (
            "hadolint invocation must guard against missing Dockerfiles "
            "(use `[ -f path ]` test or equivalent)"
        )

    def test_bandit_invoked_exactly_once(self, ci: dict[str, Any]) -> None:
        """Phase 1 collapses the duplicated bandit pattern (lines 228 + 229)
        into a single invocation that BOTH gates on findings AND produces the
        JSON report."""
        runs = self._runs_in_job(ci, "security")
        bandit_runs = re.findall(r"\bbandit\b\s+-r\s+src\b", runs)
        assert len(bandit_runs) == 1, (
            f"expected exactly 1 bandit invocation, found {len(bandit_runs)}"
        )
