"""Shared fixtures for the ADR-112 wave B2 r4 tests (lane R).

``honest_intent`` builds what a correct UMRS proposal holds for the temp
workflow tree below, with the gate's own shared functions (``step_graph``,
``expected_step_config``), the same ones r4 recomputes with.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import zipfile
from typing import Any, Optional

from r4_contracts import hpc_gate

SITE = "bdrt"
DOMAIN = "r4.example.org"
PROJECT = "proj-1"
LOGIN = "alice"
PRINCIPAL = "alice"
ACCOUNT = "alloc-1"
RUN_ID = "wf-0123456789ab"
INPUT_BUCKET = "wf-runner"
OUTPUT_BUCKET = "wf-runner-output"
QUEUE_SECONDS = 600
IMAGE = {
    "ref": "ghcr.io/example/umrs-step:2026.11",
    "image_id": "b" * 64,
    "archive_sha256": "c" * 64,
}
TRUSTED = {
    "ref": "ghcr.io/example/umrs-platform-step:2026.11",
    "image_id": "d" * 64,
    "archive_sha256": "e" * 64,
}

PROFILE: dict[str, Any] = {
    "v": 1,
    "site": SITE,
    "domain": DOMAIN,
    "slurm": {
        "path": "/usr/bin:/bin",
        "owner_uids": [],
        "queue_seconds": QUEUE_SECONDS,
        "grace_seconds": 300,
    },
    "partitions": {
        "cpu": {
            "arch": "x86_64",
            "max_cpus": 64,
            "max_gpus": 0,
            "max_time": 86400,
            "mem": True,
            "gpu_request": "",
            "gres": "",
            "gpu_flag": "gres",
            "exclusive": False,
        }
    },
    "gpu": {"enabled": False, "form": "cdi"},
    "podman": {"pids_limit": 4096, "cgroups": "enabled", "nproc_limit": 4096},
    "venue": {"root": "", "storage_root": "", "network_config_dir": "", "network_backend": ""},
    "image_archive_dir": "/srv/umrs/images",
    "caps": {
        "max_object_bytes": 1 << 30,
        "max_in_bytes": 1 << 33,
        "max_in_objects": 20000,
        "max_src_bytes": 100 << 20,
        "max_src_files": 5000,
        "max_out_files": 20000,
        "max_out_bytes": 1 << 33,
        "max_out_file_bytes": 1 << 30,
        "min_free_bytes": 1 << 30,
        "reply_bytes": 1 << 20,
        "log_tail_bytes": 65536,
        "max_workdirs": 64,
    },
    "endpoint": {
        "max_request_bytes": 1 << 20,
        "max_response_bytes": 1 << 24,
        "max_total_bytes": 1 << 28,
    },
    "watch_seconds": 30,
}

#: The workflow source tree (``workflow.json`` and ``EXPORT.json`` are added
#: by ``export_zip``). A float in a config (§1.4) and a Unicode file name.
SOURCES = {
    "steps/__init__.py": b"",
    "steps/prep.py": b"class Prep:\n    pass\n",
    "steps/fit.py": b"class Fit:\n    pass\n",
    "data/naïve.txt": "café\n".encode(),
    "README.md": b"# wf\n",
}
WORKFLOW = {
    "name": "wf",
    "version": "1.0",
    "steps": [
        {
            "name": "prep",
            "entrypoint": "steps.prep:Prep",
            "config": {"threshold": 0.5, "api_key": "not-signed"},
            "stage_inputs": {"prefixes": ["s3://data/raw/"]},
        },
        {
            "name": "fit",
            "entrypoint": "steps.fit:Fit",
            "depends_on": ["prep"],
            "config": {"grid": [1, 2.5], "temperature": 1.0},
        },
    ],
}
USER_PARAMS = {"alpha": 0.25, "label": "run"}
INPUT_FILES = ["s3://data/raw/a.csv"]
LISTED_BYTES = b"x,y\n1,2\n"
SERVER_LISTED = {
    "mode": "server_listed",
    "bucket": "data",
    "key": "raw/b.csv",
    "sha256": "f" * 64,
    "size": 2048,
}


def canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def export_zip(
    sources: Optional[dict[str, bytes]] = None,
    workflow: Optional[dict[str, Any]] = None,
    *,
    reverse: bool = False,
) -> bytes:
    """A zip shaped like ``GET /umrs/workflows/{name}/export``."""
    members = dict(SOURCES if sources is None else sources)
    members["workflow.json"] = json.dumps(WORKFLOW if workflow is None else workflow).encode()
    members["EXPORT.json"] = json.dumps({"files": {}, "note": "never trusted"}).encode()
    names = sorted(members, reverse=reverse)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o100644 << 16
            archive.writestr(info, members[name])
    return buffer.getvalue()


def manifest_files(sources: Optional[dict[str, bytes]] = None) -> list[dict[str, Any]]:
    tree = SOURCES if sources is None else sources
    return [
        {"path": p, "sha256": hashlib.sha256(tree[p]).hexdigest(), "size": len(tree[p])}
        for p in sorted(tree, key=lambda p: p.encode("utf-8"))
    ]


def full_params() -> dict[str, Any]:
    return {**USER_PARAMS, "input_files": list(INPUT_FILES), "project_id": PROJECT}


def listed_entry() -> dict[str, Any]:
    return {
        "mode": "listed",
        "bucket": "data",
        "key": "raw/a.csv",
        "sha256": hashlib.sha256(LISTED_BYTES).hexdigest(),
        "size": len(LISTED_BYTES),
    }


def _node(node: Any) -> tuple[str, list[str], list[str]]:
    if isinstance(node, dict):
        return node["name"], list(node["after"]), list(node["chained"])
    name, after, chained = node
    return name, list(after), list(chained)


def _step(raw: dict[str, Any], node: Any, params: dict[str, Any]) -> dict[str, Any]:
    name, after, chained = _node(node)
    objects = [listed_entry(), dict(SERVER_LISTED)] if name == "prep" else []
    prefixes = ["data/raw/"] if name == "prep" else []
    return {
        "name": name,
        "after": after,
        "config": hpc_gate.expected_step_config(raw, name, params),
        "image": dict(IMAGE),
        "partition": "cpu",
        "cpus": 4,
        "mem": "16G",
        "gpus": 0,
        "time": 3600,
        "inputs": {"objects": objects, "prefixes": prefixes, "chained": chained},
    }


def honest_intent(
    *, profile_sha256: str, now: Optional[float] = None, workflow: Optional[dict] = None
) -> dict[str, Any]:
    """What a correct proposal for ``SOURCES``/``WORKFLOW`` holds."""
    wf = WORKFLOW if workflow is None else workflow
    params = full_params()
    graph = list(hpc_gate.step_graph(wf["steps"]))
    steps = [_step(raw, node, params) for raw, node in zip(wf["steps"], graph)]
    issued = int(time.time() if now is None else now)
    return {
        "v": 1,
        "type": "intent",
        "site": SITE,
        "principal": PRINCIPAL,
        "login": LOGIN,
        "account": ACCOUNT,
        "project": PROJECT,
        "workflow": "wf",
        "version": "1.0",
        "source_sha256": "a" * 64,
        "run_id": RUN_ID,
        "input_bucket": INPUT_BUCKET,
        "output_bucket": OUTPUT_BUCKET,
        "params": params,
        "site_profile_sha256": profile_sha256,
        "trusted_image": dict(TRUSTED),
        "src_manifest": {
            "files": manifest_files(),
            "entrypoints": {s["name"]: s["entrypoint"] for s in wf["steps"]},
        },
        "steps": steps,
        "nonce": "0123456789abcdef0123456789abcdef",
        "issued": issued,
        "expires": issued + 3600,
        "max_run_seconds": sum(s["time"] for s in steps) + len(steps) * QUEUE_SECONDS,
    }


def proposal_answer(intent: dict[str, Any]) -> dict[str, Any]:
    payload = canonical(intent)
    return {
        "intent_b64": base64.b64encode(payload).decode("ascii"),
        "intent_id": hashlib.sha256(payload).hexdigest(),
        "site_profile_sha256": intent["site_profile_sha256"],
        "summary": "advisory; r4 ignores it",
    }


def profile_answer(profile: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = canonical(PROFILE if profile is None else profile)
    return {
        "profile": json.loads(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes_b64": base64.b64encode(data).decode("ascii"),
    }
