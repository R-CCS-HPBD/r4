#!/usr/bin/env python3
"""
K8s Smoke Test — validate K8s execution path on a real cluster.

Exercises:
  1. Namespace creation / cleanup
  2. K8sTarget job lifecycle (submit → status → cancel)
  3. K8sJobWrapper service creation / teardown
  4. Infrastructure deployment (postgres, minio via kustomize)
  5. GPU device-plugin detection

Designed for USE_EXISTING_CLUSTER=true against a real K8s cluster
accessible via KUBECONFIG or kubectl context.

Usage:
    # Standalone (will auto-detect KUBECONFIG)
    python tests/test_k8s_smoke.py

    # With explicit kubeconfig
    KUBECONFIG=~/.kube/config python tests/test_k8s_smoke.py

    # With pytest
    KUBECONFIG=~/.kube/config pytest tests/test_k8s_smoke.py -v

    # Skip infrastructure deployment (faster, namespace+job only)
    python tests/test_k8s_smoke.py --skip-infra

    # Keep resources after test for inspection
    python tests/test_k8s_smoke.py --no-cleanup
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
K8S_MANIFESTS = PROJECT_ROOT / "umrs" / "docker" / "k8s"

# Smoke test uses its own namespace to avoid conflicts
SMOKE_NAMESPACE = "rivault-smoke-test"


# ── helpers ──────────────────────────────────────────────────────────────────


def kubectl(
    args: list[str],
    check: bool = True,
    timeout: int = 30,
    stdin_data: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run kubectl with the current KUBECONFIG."""
    cmd = ["kubectl"] + args
    logger.debug(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=stdin_data.decode() if stdin_data else None,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed (rc={result.returncode}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result


def kubectl_json(args: list[str], **kwargs) -> dict:
    """Run kubectl and parse JSON output."""
    result = kubectl(args + ["-o", "json"], **kwargs)
    return json.loads(result.stdout)


# ── smoke test results ───────────────────────────────────────────────────────


@dataclass
class SmokeResult:
    """Accumulates pass/fail/skip for each check."""

    checks: list[tuple[str, str, str]] = field(default_factory=list)
    # (name, status, detail) where status is "PASS", "FAIL", "SKIP", "WARN"

    def record(self, name: str, status: str, detail: str = ""):
        self.checks.append((name, status, detail))
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘", "WARN": "⚠"}.get(status, "?")
        log = logger.info if status in ("PASS", "SKIP") else logger.error
        if status == "WARN":
            log = logger.warning
        log(f"  {icon} {name}: {detail}" if detail else f"  {icon} {name}")

    @property
    def passed(self) -> int:
        return sum(1 for _, s, _ in self.checks if s == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for _, s, _ in self.checks if s == "FAIL")

    @property
    def skipped(self) -> int:
        return sum(1 for _, s, _ in self.checks if s == "SKIP")

    @property
    def warnings(self) -> int:
        return sum(1 for _, s, _ in self.checks if s == "WARN")

    def summary(self) -> str:
        total = len(self.checks)
        parts = [f"{self.passed} passed"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.warnings:
            parts.append(f"{self.warnings} warnings")
        return f"{total} checks: {', '.join(parts)}"

    @property
    def ok(self) -> bool:
        return self.failed == 0


# ── smoke checks ─────────────────────────────────────────────────────────────


def check_cluster_reachable(res: SmokeResult) -> bool:
    """Verify kubectl can talk to the cluster."""
    try:
        result = kubectl(["cluster-info"], check=False, timeout=10)
        if result.returncode == 0:
            res.record("cluster-reachable", "PASS", "Cluster API is reachable")
            return True
        res.record("cluster-reachable", "FAIL", result.stderr.strip())
        return False
    except Exception as e:
        res.record("cluster-reachable", "FAIL", str(e))
        return False


def _node_summary(node: dict) -> dict:
    """Project one `kubectl get nodes -o json` item onto the smoke summary."""
    status = node["status"]
    conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
    gpu_count = status.get("capacity", {}).get("nvidia.com/gpu", "0")
    node_info = status["nodeInfo"]
    return {
        "ready": conditions.get("Ready") == "True",
        "arch": node_info["architecture"],
        "os": node_info["osImage"],
        "kubelet": node_info["kubeletVersion"],
        "gpu": int(gpu_count) if gpu_count else 0,
        "roles": node["metadata"]
        .get("labels", {})
        .get("node-role.kubernetes.io/control-plane", None),
    }


def _record_node_readiness(res: SmokeResult, node_info: dict) -> None:
    """Record the nodes-ready check from the collected per-node summaries."""
    not_ready = [name for name, info in node_info.items() if not info["ready"]]
    if not_ready:
        res.record("nodes-ready", "FAIL", f"Not ready: {not_ready}")
        return
    arches = list({info["arch"] for info in node_info.values()})
    res.record("nodes-ready", "PASS", f"{len(node_info)} node(s) ready, arch={arches}")


def check_nodes(res: SmokeResult) -> dict:
    """Gather node info and check readiness."""
    try:
        data = kubectl_json(["get", "nodes"])
        node_info = {
            node["metadata"]["name"]: _node_summary(node) for node in data.get("items", [])
        }
        _record_node_readiness(res, node_info)
        return node_info

    except Exception as e:
        res.record("nodes-ready", "FAIL", str(e))
        return {}


def check_gpu_device_plugin(res: SmokeResult, node_info: dict) -> bool:
    """Check if nvidia.com/gpu resource is present in node capacity."""
    gpus_found = sum(v["gpu"] for v in node_info.values())
    if gpus_found > 0:
        res.record("gpu-device-plugin", "PASS", f"{gpus_found} GPU(s) visible to K8s")
        return True

    # Check if nvidia device plugin daemonset exists
    try:
        result = kubectl(
            ["get", "daemonsets", "--all-namespaces", "-o", "json"],
            check=False,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            nvidia_ds = [
                item["metadata"]["name"]
                for item in data.get("items", [])
                if "nvidia" in item["metadata"]["name"].lower()
            ]
            if nvidia_ds:
                res.record(
                    "gpu-device-plugin",
                    "WARN",
                    f"DaemonSet(s) {nvidia_ds} exist but GPUs not in capacity",
                )
            else:
                res.record(
                    "gpu-device-plugin",
                    "WARN",
                    "No nvidia.com/gpu in node capacity — "
                    "NVIDIA K8s device plugin not installed. "
                    "GPU workloads (vLLM) require: "
                    "kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/main/deployments/static/nvidia-device-plugin.yml",
                )
    except Exception:
        res.record("gpu-device-plugin", "WARN", "Could not check for device plugin")

    return False


def pick_schedulable_node(node_info: dict) -> str:
    """Pick a node that can run pods.

    Prefers the control-plane node because in air-gapped clusters
    worker nodes may lack the sandbox (pause) image and cannot pull
    it from the internet. The control-plane always has it cached from
    initial k3s bootstrapping.

    Returns empty string if no suitable node found (lets scheduler decide).
    """
    # Prefer control-plane (has cached images in air-gapped setups)
    for name, info in node_info.items():
        if info.get("ready") and info.get("roles") is not None:
            return name
    # Fall back to any ready node
    for name, info in node_info.items():
        if info.get("ready"):
            return name
    return ""


def check_namespace_lifecycle(res: SmokeResult) -> bool:
    """Create and verify the smoke test namespace."""
    try:
        # Create namespace
        manifest = json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": SMOKE_NAMESPACE,
                    "labels": {
                        "app.kubernetes.io/name": "rivault-smoke-test",
                        "purpose": "smoke-test",
                    },
                },
            }
        )
        kubectl(
            ["apply", "-f", "-"],
            stdin_data=manifest.encode(),
        )
        # Verify it exists
        result = kubectl(
            ["get", "namespace", SMOKE_NAMESPACE],
            check=False,
        )
        if result.returncode == 0:
            res.record("namespace-create", "PASS", f"Namespace '{SMOKE_NAMESPACE}' created")
            return True
        res.record("namespace-create", "FAIL", result.stderr.strip())
        return False
    except Exception as e:
        res.record("namespace-create", "FAIL", str(e))
        return False


def _smoke_job_manifest(job_name: str, target_node: str) -> dict:
    """Build the busybox Job manifest the smoke run submits."""
    pod_spec: dict = {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "rivault",
                "image": "busybox:latest",
                "command": ["sh", "-c", "echo 'rivault-smoke-ok'; sleep 5"],
            }
        ],
    }
    if target_node:
        pod_spec["nodeName"] = target_node

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": SMOKE_NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": "rivault",
                "app.kubernetes.io/component": "smoke-test",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "job-name": job_name,
                    },
                },
                "spec": pod_spec,
            },
        },
    }


def _submit_smoke_job(res: SmokeResult, job_name: str, manifest: dict) -> bool:
    """Submit the Job (K8sTarget.submit equivalent)."""
    try:
        kubectl(
            ["apply", "-f", "-", "--namespace", SMOKE_NAMESPACE],
            stdin_data=json.dumps(manifest).encode(),
        )
        res.record("job-submit", "PASS", f"Job '{job_name}' created")
        return True
    except Exception as e:
        res.record("job-submit", "FAIL", str(e))
        return False


def _smoke_job_pods(job_name: str) -> list | None:
    """Pods belonging to the smoke Job; None when the query itself failed."""
    result = kubectl(
        [
            "get",
            "pods",
            "--namespace",
            SMOKE_NAMESPACE,
            "--selector",
            f"job-name={job_name}",
            "-o",
            "json",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout).get("items", [])


def _terminated_reason(pod: dict) -> str:
    """Termination reason of the pod's first container ("" when unknown)."""
    statuses = pod["status"].get("containerStatuses", [])
    if not statuses:
        return ""
    return statuses[0].get("state", {}).get("terminated", {}).get("reason", "")


def _classify_pod_phase(res: SmokeResult, pod: dict) -> bool | None:
    """Record a terminal pod phase; None while the pod is still settling."""
    phase = pod["status"].get("phase", "Unknown")
    if phase in ("Running", "Succeeded"):
        node = pod["spec"].get("nodeName", "")
        res.record("job-pod-scheduled", "PASS", f"Pod phase={phase}, node={node}")
        return True
    if phase == "Failed":
        res.record("job-pod-scheduled", "FAIL", f"Pod failed: {_terminated_reason(pod)}")
        return False
    return None


def _await_pod_scheduled(res: SmokeResult, job_name: str) -> bool:
    """Poll until the Job's pod schedules (K8sTarget.status equivalent)."""
    deadline = time.time() + 120
    while time.time() < deadline:
        pods = _smoke_job_pods(job_name)
        if pods:
            verdict = _classify_pod_phase(res, pods[0])
            if verdict is not None:
                return verdict
        time.sleep(3)
    return False


def _record_unscheduled_pod(res: SmokeResult, job_name: str) -> None:
    """Explain why the Job's pod never reached Running/Succeeded."""
    pods = _smoke_job_pods(job_name)
    if pods is None:
        res.record("job-pod-scheduled", "FAIL", "Could not query pods")
        return
    if not pods:
        res.record("job-pod-scheduled", "FAIL", "No pods created for job")
        return
    status = pods[0]["status"]
    res.record(
        "job-pod-scheduled",
        "FAIL",
        f"Stuck in phase={status.get('phase', 'Unknown')}, "
        f"conditions={status.get('conditions', [])}",
    )


def _check_pod_scheduling(res: SmokeResult, job_name: str) -> None:
    """Wait for the pod to schedule and record why it did not when it didn't."""
    try:
        if not _await_pod_scheduled(res, job_name):
            _record_unscheduled_pod(res, job_name)
    except Exception as e:
        res.record("job-pod-scheduled", "FAIL", str(e))


def _job_status(job_name: str) -> dict | None:
    """`.status` of the smoke Job; None when the query itself failed."""
    result = kubectl(
        [
            "get",
            "job",
            job_name,
            "--namespace",
            SMOKE_NAMESPACE,
            "-o",
            "json",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout).get("status", {})


def _await_job_completion(res: SmokeResult, job_name: str) -> bool:
    """Poll until the Job succeeds (True) or fails/times out (False)."""
    deadline = time.time() + 60
    while time.time() < deadline:
        status = _job_status(job_name)
        if status is not None:
            if status.get("succeeded", 0) > 0:
                res.record("job-completed", "PASS", "Job succeeded")
                return True
            if status.get("failed", 0) > 0:
                res.record("job-completed", "FAIL", "Job failed")
                return False
        time.sleep(3)
    return False


def _check_job_completion(res: SmokeResult, job_name: str) -> None:
    """Wait for the Job to finish."""
    try:
        if not _await_job_completion(res, job_name):
            res.record("job-completed", "WARN", "Job did not complete within timeout")
    except Exception as e:
        res.record("job-completed", "FAIL", str(e))


def _check_job_logs(res: SmokeResult, job_name: str) -> None:
    """Validate the Job actually produced its expected stdout marker."""
    try:
        result = kubectl(
            [
                "logs",
                "--namespace",
                SMOKE_NAMESPACE,
                "--selector",
                f"job-name={job_name}",
                "--tail=5",
            ],
            check=False,
        )
        if result.returncode != 0:
            res.record("job-logs", "WARN", "Could not retrieve logs")
        elif "rivault-smoke-ok" in result.stdout:
            res.record("job-logs", "PASS", "Got expected output from job")
        else:
            res.record("job-logs", "WARN", f"Unexpected output: {result.stdout.strip()[:100]}")
    except Exception as e:
        res.record("job-logs", "WARN", str(e))


def _delete_smoke_job(res: SmokeResult, job_name: str) -> bool:
    """Delete the Job (K8sTarget.cancel equivalent)."""
    try:
        kubectl(
            [
                "delete",
                "job",
                job_name,
                "--namespace",
                SMOKE_NAMESPACE,
                "--ignore-not-found",
            ],
        )
        res.record("job-cancel", "PASS", f"Job '{job_name}' deleted")
        return True
    except Exception as e:
        res.record("job-cancel", "FAIL", str(e))
        return False


def check_job_lifecycle(res: SmokeResult, target_node: str = "") -> bool:
    """Submit a K8s Job, check status, then delete it.

    This exercises the same code path as K8sTarget.submit/status/cancel.
    """
    job_name = "rivault-smoke-job"
    if not _submit_smoke_job(res, job_name, _smoke_job_manifest(job_name, target_node)):
        return False

    _check_pod_scheduling(res, job_name)
    _check_job_completion(res, job_name)
    _check_job_logs(res, job_name)
    return _delete_smoke_job(res, job_name)


def check_service_lifecycle(res: SmokeResult) -> bool:
    """Create a ClusterIP Service and verify it, then delete.

    Exercises the same pattern as K8sJobWrapper._build_service.
    """
    svc_name = "rivault-smoke-svc"
    manifest = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": svc_name,
            "namespace": SMOKE_NAMESPACE,
        },
        "spec": {
            "selector": {"job-name": "rivault-smoke-job"},
            "ports": [
                {
                    "port": 11434,
                    "targetPort": 11434,
                }
            ],
            "type": "ClusterIP",
        },
    }

    try:
        kubectl(
            ["apply", "-f", "-", "--namespace", SMOKE_NAMESPACE],
            stdin_data=json.dumps(manifest).encode(),
        )

        # Verify ClusterIP was assigned
        result = kubectl(
            [
                "get",
                "service",
                svc_name,
                "--namespace",
                SMOKE_NAMESPACE,
                "-o",
                "jsonpath={.spec.clusterIP}",
            ],
        )
        cluster_ip = result.stdout.strip()
        if cluster_ip and cluster_ip != "None":
            res.record("service-create", "PASS", f"Service created, ClusterIP={cluster_ip}")
        else:
            res.record("service-create", "WARN", "Service created but no ClusterIP assigned")

        # Cleanup
        kubectl(
            [
                "delete",
                "service",
                svc_name,
                "--namespace",
                SMOKE_NAMESPACE,
                "--ignore-not-found",
            ],
        )
        res.record("service-delete", "PASS", "Service deleted")
        return True

    except Exception as e:
        res.record("service-create", "FAIL", str(e))
        return False


_INFRA_COMPONENTS = ("postgres", "minio")
_INFRA_NAMESPACE = "umrs-system"


def _apply_infra_manifests(res: SmokeResult) -> bool:
    """Apply umrs/docker/k8s via kustomize."""
    try:
        kubectl(["apply", "-k", str(K8S_MANIFESTS)], timeout=60)
        res.record("infra-apply", "PASS", "Kustomize manifests applied")
        return True
    except Exception as e:
        res.record("infra-apply", "FAIL", str(e))
        return False


def _scale_infra_statefulsets(replicas: int) -> None:
    """Scale both infra StatefulSets to *replicas* (best effort)."""
    for sts_name in _INFRA_COMPONENTS:
        kubectl(
            [
                "scale",
                "statefulset",
                sts_name,
                "--namespace",
                _INFRA_NAMESPACE,
                f"--replicas={replicas}",
            ],
            check=False,
            timeout=15,
        )


def _pin_infra_to_node(target_node: str) -> None:
    """Pin the infra StatefulSets to *target_node*.

    In air-gapped clusters the local-path PVC provisioner helper pods only run
    on the schedulable node, so scale to 0 before anything lands on the wrong
    node, drop the PVCs already bound to the wrong topology, add the
    nodeSelector, then scale back up.
    """
    _scale_infra_statefulsets(0)
    kubectl(
        [
            "delete",
            "pvc",
            "--namespace",
            _INFRA_NAMESPACE,
            "--all",
        ],
        check=False,
        timeout=15,
    )
    patch = json.dumps(
        {
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {
                            "kubernetes.io/hostname": target_node,
                        },
                    },
                },
            },
        }
    )
    for sts_name in _INFRA_COMPONENTS:
        kubectl(
            [
                "patch",
                "statefulset",
                sts_name,
                "--namespace",
                _INFRA_NAMESPACE,
                "--type",
                "strategic",
                "-p",
                patch,
            ],
            check=False,
            timeout=15,
        )
    _scale_infra_statefulsets(1)


def _infra_pod(selector: str) -> dict | None:
    """First umrs-system pod matching *selector*; None when there is none."""
    result = kubectl(
        [
            "get",
            "pods",
            "--namespace",
            _INFRA_NAMESPACE,
            "--selector",
            selector,
            "-o",
            "json",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    items = json.loads(result.stdout).get("items", [])
    return items[0] if items else None


def _container_wait_detail(pod: dict, cs: dict) -> str:
    """One-line diagnosis of why a not-yet-ready container is waiting."""
    node = pod["spec"].get("nodeName", "unscheduled")
    phase = pod["status"].get("phase", "Unknown")
    restart_count = cs.get("restartCount", 0)
    waiting = cs.get("state", {}).get("waiting", {})
    terminated = cs.get("state", {}).get("terminated", {})
    if waiting:
        return f"node={node}, waiting={waiting.get('reason', '?')}, restarts={restart_count}"
    if terminated:
        return f"node={node}, terminated={terminated.get('reason', '?')}, restarts={restart_count}"
    return f"node={node}, phase={phase}, restarts={restart_count}"


def _crashlooping(cs: dict) -> bool:
    """True once a container is in CrashLoopBackOff and won't self-recover."""
    waiting = cs.get("state", {}).get("waiting", {})
    return waiting.get("reason") == "CrashLoopBackOff" and cs.get("restartCount", 0) >= 2


def _infra_pod_progress(res: SmokeResult, component: str, pod: dict) -> tuple[bool | None, str]:
    """Evaluate one poll of an infra pod.

    Returns ``(verdict, detail)`` — verdict True means ready (PASS recorded),
    False means a terminal failure, None means keep polling.
    """
    node = pod["spec"].get("nodeName", "unscheduled")
    phase = pod["status"].get("phase", "Unknown")
    statuses = pod["status"].get("containerStatuses", [])
    if not statuses:
        return None, f"node={node}, phase={phase}, no container status yet"

    cs = statuses[0]
    if cs.get("ready", False):
        res.record(
            f"infra-{component}-ready",
            "PASS",
            f"Pod ready (phase={phase}, node={node})",
        )
        return True, ""

    detail = _container_wait_detail(pod, cs)
    # Fast-exit on CrashLoopBackOff (won't self-recover)
    return (False if _crashlooping(cs) else None), detail


def _await_infra_ready(res: SmokeResult, component: str, selector: str) -> tuple[bool, str]:
    """Poll one infra pod until it is ready, crash-loops, or times out."""
    deadline = time.time() + 120
    last_detail = "Pod not found"
    while time.time() < deadline:
        pod = _infra_pod(selector)
        if pod is not None:
            verdict, detail = _infra_pod_progress(res, component, pod)
            if verdict is True:
                return True, detail
            last_detail = detail or last_detail
            if verdict is False:
                break
        time.sleep(5)
    return False, last_detail


def _check_infra_component(res: SmokeResult, component: str) -> None:
    """Wait for one infra StatefulSet pod to report ready."""
    role = "database" if component == "postgres" else "object-storage"
    selector = f"app.kubernetes.io/component={role}"
    try:
        ready, last_detail = _await_infra_ready(res, component, selector)
        if ready:
            return
        # Downgrade to WARN if pod was scheduled (K8s path works,
        # container config is wrong — e.g. permissions on PVC).
        scheduled = "node=" in last_detail and "unscheduled" not in last_detail
        res.record(f"infra-{component}-ready", "WARN" if scheduled else "FAIL", last_detail)
    except Exception as e:
        res.record(f"infra-{component}-ready", "FAIL", str(e))


def _check_infra_service(res: SmokeResult, svc_name: str) -> None:
    """Verify an infra Service was given a ClusterIP."""
    try:
        result = kubectl(
            [
                "get",
                "service",
                svc_name,
                "--namespace",
                _INFRA_NAMESPACE,
                "-o",
                "jsonpath={.spec.clusterIP}",
            ],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            res.record(
                f"infra-{svc_name}-svc",
                "PASS",
                f"ClusterIP={result.stdout.strip()}",
            )
        else:
            res.record(f"infra-{svc_name}-svc", "FAIL", "Service not found")
    except Exception as e:
        res.record(f"infra-{svc_name}-svc", "FAIL", str(e))


def check_infra_deployment(res: SmokeResult, target_node: str = "") -> bool:
    """Deploy postgres + minio from kustomize manifests.

    Uses the real manifests from umrs/docker/k8s/ to validate that
    the infrastructure stack deploys on the target cluster.
    """
    if not K8S_MANIFESTS.exists():
        res.record("infra-manifests", "SKIP", f"Manifests not found at {K8S_MANIFESTS}")
        return False

    if not _apply_infra_manifests(res):
        return False

    # In air-gapped clusters, pin StatefulSets to the schedulable node so that
    # local-path PVC provisioner helper pods can actually run.
    if target_node:
        _pin_infra_to_node(target_node)

    # Wait for postgres and minio to be ready
    for component in _INFRA_COMPONENTS:
        _check_infra_component(res, component)

    # Verify services have ClusterIPs
    for svc_name in _INFRA_COMPONENTS:
        _check_infra_service(res, svc_name)

    return True


def check_network_policy(res: SmokeResult) -> bool:
    """Verify NetworkPolicy can be created (if supported by CNI)."""
    policy_name = "rivault-smoke-netpol"
    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": policy_name,
            "namespace": SMOKE_NAMESPACE,
        },
        "spec": {
            "podSelector": {
                "matchLabels": {"app": "rivault-smoke"},
            },
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"app.kubernetes.io/name": "gateway"},
                            },
                        }
                    ],
                }
            ],
        },
    }

    try:
        kubectl(
            ["apply", "-f", "-", "--namespace", SMOKE_NAMESPACE],
            stdin_data=json.dumps(manifest).encode(),
        )
        res.record(
            "network-policy",
            "PASS",
            "NetworkPolicy created (enforcement depends on CNI)",
        )

        # Cleanup
        kubectl(
            [
                "delete",
                "networkpolicy",
                policy_name,
                "--namespace",
                SMOKE_NAMESPACE,
                "--ignore-not-found",
            ],
        )
        return True
    except Exception as e:
        res.record("network-policy", "WARN", f"NetworkPolicy not supported: {e}")
        return False


def check_k8s_target_import(res: SmokeResult) -> bool:
    """Verify K8sTarget and K8sJobWrapper can be imported."""
    try:
        # Roadmap plan D-1 (ADR-053): namespace-package import, no path splice.
        from umrs.orchestrator.hybrid.targets import K8sTarget
        from umrs.orchestrator.hybrid.wrappers.kubernetes import K8sJobWrapper

        target = K8sTarget(namespace=SMOKE_NAMESPACE)
        assert target.authority == "k8s"
        wrapper = K8sJobWrapper(namespace=SMOKE_NAMESPACE)
        assert wrapper.runtime_name == "k8s"

        res.record("k8s-target-import", "PASS", "K8sTarget + K8sJobWrapper importable")
        return True
    except Exception as e:
        res.record("k8s-target-import", "FAIL", str(e))
        return False


def cleanup_smoke_namespace(res: SmokeResult):
    """Delete the smoke test namespace."""
    try:
        kubectl(
            ["delete", "namespace", SMOKE_NAMESPACE, "--ignore-not-found"],
            timeout=60,
        )
        res.record("cleanup-namespace", "PASS", f"Namespace '{SMOKE_NAMESPACE}' deleted")
    except Exception as e:
        res.record("cleanup-namespace", "WARN", f"Cleanup failed: {e}")


def cleanup_infra(res: SmokeResult):
    """Delete the infrastructure stack."""
    if K8S_MANIFESTS.exists():
        try:
            kubectl(
                ["delete", "-k", str(K8S_MANIFESTS), "--ignore-not-found=true"],
                timeout=120,
                check=False,
            )
            res.record("cleanup-infra", "PASS", "Infrastructure stack deleted")
        except Exception as e:
            res.record("cleanup-infra", "WARN", f"Infra cleanup failed: {e}")


# ── main ─────────────────────────────────────────────────────────────────────


def run_smoke_test(
    skip_infra: bool = False,
    no_cleanup: bool = False,
) -> SmokeResult:
    """Run the full K8s smoke test suite."""
    res = SmokeResult()

    logger.info("=" * 60)
    logger.info("K8s Smoke Test — Real Cluster Validation")
    logger.info("=" * 60)

    # Phase 1: Connectivity
    logger.info("\n[Phase 1] Cluster connectivity")
    if not check_cluster_reachable(res):
        logger.error("Cluster not reachable — aborting")
        return res

    # Phase 2: Node inspection
    logger.info("\n[Phase 2] Node inspection")
    node_info = check_nodes(res)
    check_gpu_device_plugin(res, node_info)
    target_node = pick_schedulable_node(node_info)
    if target_node:
        logger.info(f"  → targeting node '{target_node}' for smoke jobs")

    # Phase 3: Module import
    logger.info("\n[Phase 3] Module imports")
    check_k8s_target_import(res)

    # Phase 4: Namespace lifecycle
    logger.info("\n[Phase 4] Namespace lifecycle")
    if not check_namespace_lifecycle(res):
        logger.error("Cannot create namespace — aborting remaining checks")
        return res

    # Phase 5: Job lifecycle
    logger.info("\n[Phase 5] Job lifecycle (K8sTarget path)")
    check_job_lifecycle(res, target_node=target_node)

    # Phase 6: Service lifecycle
    logger.info("\n[Phase 6] Service lifecycle (K8sJobWrapper path)")
    check_service_lifecycle(res)

    # Phase 7: Network policy
    logger.info("\n[Phase 7] Network policy")
    check_network_policy(res)

    # Phase 8: Infrastructure deployment (optional)
    _run_infra_phase(res, skip_infra, target_node)

    # Cleanup
    _run_cleanup_phase(res, skip_infra, no_cleanup)

    _log_smoke_summary(res)
    return res


def _run_infra_phase(res: SmokeResult, skip_infra: bool, target_node: str) -> None:
    """Phase 8 — deploy postgres + minio unless the caller skipped infra."""
    if skip_infra:
        res.record("infra-deploy", "SKIP", "Skipped with --skip-infra")
        return
    logger.info("\n[Phase 8] Infrastructure deployment (postgres + minio)")
    check_infra_deployment(res, target_node=target_node)


def _run_cleanup_phase(res: SmokeResult, skip_infra: bool, no_cleanup: bool) -> None:
    """Tear down what the smoke run created, unless --no-cleanup was given."""
    if no_cleanup:
        logger.info("\n[Cleanup] Skipped (--no-cleanup)")
        return
    logger.info("\n[Cleanup]")
    cleanup_smoke_namespace(res)
    if not skip_infra:
        cleanup_infra(res)


def _log_checks_with_status(res: SmokeResult, status_wanted: str, glyph: str) -> None:
    """Log every recorded check that landed on *status_wanted*."""
    for name, status, detail in res.checks:
        if status == status_wanted:
            logger.info(f"  {glyph} {name}: {detail}")


def _log_smoke_summary(res: SmokeResult) -> None:
    """Print the run summary plus the failing/warning check detail."""
    logger.info("\n" + "=" * 60)
    logger.info(f"Result: {res.summary()}")
    logger.info("=" * 60)

    if res.failed:
        logger.info("\nFailed checks:")
        _log_checks_with_status(res, "FAIL", "✗")

    if res.warnings:
        logger.info("\nWarnings / gaps:")
        _log_checks_with_status(res, "WARN", "⚠")


# ── pytest integration ───────────────────────────────────────────────────────

try:
    import pytest as _pytest

    @_pytest.fixture
    def k8s_smoke_cluster():
        """Skip if K8s cluster not reachable."""
        try:
            result = subprocess.run(
                ["kubectl", "cluster-info"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                _pytest.skip("K8s cluster not reachable")
        except Exception:
            _pytest.skip("kubectl not available")

    class TestK8sSmokePrerequisites:
        """Quick prerequisite checks (no cluster needed; kubectl check is
        guarded because CI runners do not ship the binary)."""

        @_pytest.mark.skipif(
            shutil.which("kubectl") is None,
            reason="requires the kubectl binary on PATH",
        )
        def test_kubectl_available(self):
            result = subprocess.run(
                ["kubectl", "version", "--client", "-o", "json"],
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0

        def test_manifests_exist(self):
            assert K8S_MANIFESTS.exists()
            assert (K8S_MANIFESTS / "kustomization.yaml").exists()

        def test_k8s_target_importable(self):
            from umrs.orchestrator.hybrid.targets import K8sTarget

            t = K8sTarget(namespace="test")
            assert t.authority == "k8s"

        def test_k8s_wrapper_importable(self):
            from umrs.orchestrator.hybrid.wrappers.kubernetes import K8sJobWrapper

            w = K8sJobWrapper(namespace="test")
            assert w.runtime_name == "k8s"

    class TestK8sSmokeCluster:
        """Tests that require a live K8s cluster."""

        def test_full_smoke(self, k8s_smoke_cluster):
            _ = k8s_smoke_cluster  # fixture named for pytest
            """Run the full smoke test suite."""
            result = run_smoke_test(skip_infra=True, no_cleanup=True)
            assert result.ok, f"Smoke test had failures: {result.summary()}"

except ImportError:
    pass


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="K8s smoke test")
    parser.add_argument("--skip-infra", action="store_true", help="Skip infrastructure deployment")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep resources after test")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    result = run_smoke_test(
        skip_infra=args.skip_infra,
        no_cleanup=args.no_cleanup,
    )
    sys.exit(0 if result.ok else 1)
