"""ADR-112 waves C and D: standing grants in the UMRS gate (r4_contracts.hpc_gate).

Contract: gated-delegation plan section 6, "C/D.0 Wire contract" (C1-C3 with
the critic amendments K1-K20), lane G. The gate runs in-process
(``dispatch``) for the refusal matrix, in threads for the lock tests, and as
sshd runs it (``python -I <gate> --site s1``, also under python3.9 when
installed) for the race and the end-to-end runs. Slurm is the B2 fake from
``test_hpc_gate_exec.py`` with three more behaviours (a sleeping, silent or
custom ``sacct`` accounting row). Signatures are real ``ssh-keygen -Y sign``
signatures from keys generated into tmp at test time.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from r4_contracts import hpc_gate as g


def _load_harness() -> Any:
    """The B2 harness (world, fake Slurm, keys), loaded by path so this file
    works whatever pytest's import mode."""
    path = Path(__file__).with_name("test_hpc_gate_exec.py")
    spec = importlib.util.spec_from_file_location("_hpc_gate_exec_harness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gx = _load_harness()

pytestmark = pytest.mark.skipif(not os.path.exists(gx.KEYGEN), reason="needs /usr/bin/ssh-keygen")

SITE, DOMAIN, PRINCIPAL, LOGIN = gx.SITE, gx.DOMAIN, gx.PRINCIPAL, gx.LOGIN
OUT_BUCKET, IN_BUCKET = gx.OUT_BUCKET, gx.IN_BUCKET
GRANT_NS = f"umrs-grant-v1@{DOMAIN}"
CONTROL_NS = f"umrs-control-v1@{DOMAIN}"
RUN_1, RUN_2, RUN_3 = "wf-000000000001", "wf-000000000002", "wf-000000000003"
USAGE_FMT = "JobID,State,Elapsed,AllocTRES,AllocCPUS,Account,Partition,Start,End"
ok, refused, obj = gx.ok, gx.refused, gx.obj


def _fake_slurm() -> str:
    """The B2 fake plus ``sacct_sleep`` / ``usage_rc`` (the accounting call
    only), ``sacct_omit`` (jobs sacct never reports) and ``usage`` (the row)."""
    text = gx.FAKE_SLURM
    edits = (
        (
            'def sacct(args, beh):\n    if beh.get("sacct_rc"):',
            "def sacct(args, beh):\n"
            f'    if opt(args, "-o") == "{USAGE_FMT}":\n'
            '        time.sleep(beh.get("sacct_sleep", 0))\n'
            '        if beh.get("usage_rc"):\n'
            '            sys.exit(beh["usage_rc"])\n'
            '    if beh.get("sacct_rc"):',
        ),
        (
            "        if k not in known:\n            continue",
            '        if k not in known or k in beh.get("sacct_omit", []):\n            continue',
        ),
        (
            "print(row.get(fmt, USAGE.format(k, state)))",
            'print(row.get(fmt, beh.get("usage", USAGE).format(k, state)))',
        ),
    )
    for old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def _install_fake(bindir: Path, state: Path) -> None:
    text = _fake_slurm().replace("__PYTHON__", sys.executable)
    text = text.replace("__STATE__", repr(str(state)))
    for tool in gx.SLURM_TOOLS:
        (bindir / tool).write_text(text)
        (bindir / tool).chmod(0o755)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Any:
    base = tmp_path_factory.mktemp("keys")
    signer_b64 = gx._keygen(base / "signer")
    gx._keygen(base / "other")
    return gx.Keys(signer=base / "signer", signer_b64=signer_b64, other=base / "other")


def _bootstrap_legacy_epoch(root: Path) -> None:
    """These worlds model legacy active installs, so zero must be explicit."""
    grants = root / "state" / "grants"
    grants.mkdir(parents=True, exist_ok=True)
    (grants / "epoch").write_bytes(g.canonical_bytes({"epoch": 0}))


@pytest.fixture
def world(tmp_path: Path, keys: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    w = gx._make_world(tmp_path, keys)
    _bootstrap_legacy_epoch(w.root)
    _install_fake(w.bin, w.slurm)
    return w


# ---------------------------------------------------------------------------
# grants, run specs, envelopes
# ---------------------------------------------------------------------------

WF1: dict[str, Any] = {
    "name": "demo",
    "steps": [{"name": "a", "config": {"threshold": 0.25, "label": None}, "image": "dropped"}],
}
WF2: dict[str, Any] = {
    "name": "demo",
    "steps": [
        {"name": "a", "config": {"threshold": 0.25}},
        {"name": "b", "depends_on": ["a"], "config": {"mode": "fast"}},
    ],
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["alpha"],
    "properties": {
        "alpha": {"type": "integer", "minimum": 0, "maximum": 10},
        "flag": {"type": "boolean"},
        "label": {"type": "string", "maxLength": 8, "enum": ["x", "y"]},
        "note": {"type": "string", "maxLength": 16},
        "ratio": {"type": "number", "minimum": 0, "maximum": 1},
    },
}
PARAMS: dict[str, Any] = {"project_id": "proj-1", "alpha": 3, "label": "x", "ratio": 0.5}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Run:
    """One grant-backed run: its grant, its run spec and its workflow."""

    world: Any
    wf: dict[str, Any]
    grant: dict[str, Any]
    spec: dict[str, Any]

    @property
    def gid(self) -> str:
        return str(self.grant["grant_id"])

    @property
    def key(self) -> str:
        return g.grant_run_key(self.gid, self.spec["run_id"])

    def envelope(self, **kw: Any) -> bytes:
        return genv(self.world, self.grant, self.spec, self.wf, **kw)

    def ready(self, step: str = "a", blobs: tuple[bytes, ...] = (), key: str = "") -> str:
        wd = ok(self.world.run(f"mkwd {key or self.key} {step}"))["wd"]
        ok(self.world.stage(wd, None, blobs))
        return str(wd)

    def submit(self, wd: str, step: str = "a", **kw: Any) -> Any:
        return self.world.run(f"submit {wd} {step}", self.envelope(**kw))

    def again(self, **kw: Any) -> Run:
        """Another run spec under the same grant (``make_spec`` arguments)."""
        kw.setdefault("wf", self.wf)
        return Run(self.world, self.wf, self.grant, make_spec(self.world, **kw))

    def counters(self) -> dict[str, Any]:
        path = self.world.root / "state" / "grants" / f"{self.gid}.json"
        return dict(json.loads(path.read_text()))


def make_spec(
    world: Any,
    wf: dict[str, Any] = WF1,
    params: dict[str, Any] | None = None,
    objects: dict[str, tuple[dict[str, Any], ...]] | None = None,
    run_id: str = RUN_1,
    step_over: dict[str, Any] | None = None,
    **over: Any,
) -> dict[str, Any]:
    """The run spec UMRS would compose: B2's intent body with the configs the
    skeleton and the params give (``expected_step_config``)."""
    params = copy.deepcopy(PARAMS if params is None else params)
    steps = []
    for index, (name, after, chained) in enumerate(g.step_graph(wf["steps"])):
        objs = tuple((objects or {}).get(name, ()))
        step = world.step(name, after=tuple(after), chained=tuple(chained), objects=objs)
        step["config"] = g.expected_step_config(wf["steps"][index], name, params)
        step.update(step_over or {})
        steps.append(step)
    return dict(world.intent(steps, params=params, run_id=run_id, **over))


def make_grant(world: Any, spec: dict[str, Any], wf: dict[str, Any], **over: Any) -> dict[str, Any]:
    now = int(time.time())
    entry = {
        "name": spec["workflow"],
        "version": spec["version"],
        "src_manifest_sha256": g.src_manifest_sha256(spec["src_manifest"]),
        "steps_sha256": g.steps_sha256(wf),
        "params": sorted(SCHEMA["properties"]),
    }
    body = {
        "v": 1, "type": "grant", "grant_id": secrets.token_hex(16), "site": SITE, "login": LOGIN,
        "principal": PRINCIPAL, "accounts": ["acct1"], "projects": ["proj-1"], "workflows": [entry],
        "images": sorted([world.image, world.trusted], key=lambda i: i["image_id"]),
        "trusted_images": [world.trusted],
        "param_schema": copy.deepcopy(SCHEMA), "inputs": {"mode": "any", "listed": []},
        "caps": {"max_jobs": 10, "max_gpu_hours": 10, "max_core_hours": 0, "max_concurrent": 4},
        "not_before": now, "expires": now + 86400, "issued": now, "delegable": False,
        "nonce": secrets.token_hex(16),
    }  # fmt: skip
    body.update(over)
    return body


def new_run(world: Any, wf: dict[str, Any] = WF1, grant: Any = None, **spec_kw: Any) -> Run:
    spec = make_spec(world, wf, **spec_kw)
    return Run(world, wf, make_grant(world, spec, wf, **(grant or {})), spec)


def genv(
    world: Any,
    grant: dict[str, Any],
    spec: dict[str, Any],
    wf: dict[str, Any],
    *,
    key: Path | None = None,
    ns: str = GRANT_NS,
    payload: bytes | None = None,
    spec_bytes: bytes | None = None,
    steps_bytes: bytes | None = None,
    **extra: Any,
) -> bytes:
    """The C2 submit envelope (a signed grant, the run spec, the skeleton)."""
    data = g.canonical_bytes(grant) if payload is None else payload
    signature = gx._sign(key or world.keys.signer, ns, data)
    skeleton = g.canonical_bytes(g.workflow_steps_skeleton(wf), True)
    envelope = {
        "grant": {"payload": _b64(data), "signature": signature},
        "run_spec": _b64(g.canonical_bytes(spec, True) if spec_bytes is None else spec_bytes),
        "workflow_steps": _b64(skeleton if steps_bytes is None else steps_bytes),
        **extra,
    }
    return json.dumps(envelope).encode()


def check_request(world: Any, grant: dict[str, Any], **kw: Any) -> bytes:
    data = g.canonical_bytes(grant)
    signature = gx._sign(kw.pop("key", None) or world.keys.signer, GRANT_NS, data)
    return json.dumps({"payload": _b64(data), "signature": signature, **kw}).encode()


def control(world: Any, op: str, **over: Any) -> bytes:
    now = int(time.time())
    body: dict[str, Any] = {
        "v": 1, "type": "control", "op": op, "site": SITE, "principal": PRINCIPAL,
        "nonce": secrets.token_hex(16), "issued": now, "expires": now + 300,
    }  # fmt: skip
    body.update(over)
    data = g.canonical_bytes({k: v for k, v in body.items() if v is not None})
    signature = gx._sign(world.keys.signer, CONTROL_NS, data)
    return json.dumps({"payload": _b64(data), "signature": signature}).encode()


def last_log(world: Any) -> dict[str, Any]:
    return dict(json.loads(world.log.read_text().splitlines()[-1]))


def write_counters(run: Run, entries: dict[str, Any]) -> None:
    body = {
        "v": 1, "grant_id": run.gid, "grant_sha256": _sha(g.canonical_bytes(run.grant)),
        "expires": run.grant["expires"], "entries": entries,
    }  # fmt: skip
    path = run.world.root / "state" / "grants" / f"{run.gid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(g.canonical_bytes(body))


def age_entries(run: Run, seconds: int) -> None:
    counters = run.counters()
    for entry in counters["entries"].values():
        entry["submitted"] -= seconds
    path = run.world.root / "state" / "grants" / f"{run.gid}.json"
    path.write_bytes(g.canonical_bytes(counters))


# ---------------------------------------------------------------------------
# constants and the shared API
# ---------------------------------------------------------------------------

C_REFUSALS = [
    "grant_mismatch", "grant_exhausted", "grant_expired", "grant_not_yet_valid",
    "grant_cancelled", "grant_conflict", "run_spec_changed",
]  # fmt: skip


def test_constants_verbs_and_refusal_names() -> None:
    assert (g.GATE_VERSION, g.GATE_CONFIG_VERSIONS) == ("3", ("1", "2", "3"))
    sizes = (g.SUBMIT_MAX_STDIN, g.GRANT_CHECK_MAX_STDIN, g.GRANT_MAX_BYTES)
    assert sizes == (16 << 20, 4 << 20, 2 << 20)
    assert (g.GRANT_MAX_WINDOW, g.GRANT_CANCEL_RETENTION) == (2592000, 400 * 86400)


def test_verbs_refusal_names_and_shared_api() -> None:
    assert set(C_REFUSALS) <= set(g.REFUSALS) and len(set(g.REFUSALS)) == len(g.REFUSALS)
    assert {"grant-check", "grant-cancel"} <= set(g.VERBS)
    assert g.CONTROL_VERBS == ("revoke", "rotate", "grant-cancel")
    shared = (
        "validate_grant", "grant_param_schema", "params_problems", "workflow_steps_skeleton",
        "steps_sha256", "src_manifest_sha256", "grant_covers", "grant_run_key", "grant_cost",
        "grant_control_body", "checkpoint_text",
    )  # fmt: skip
    assert all(callable(getattr(g, name)) for name in shared)


def test_run_key_and_digests() -> None:
    gid = "ab" * 16
    data = b"umrs-grant-run-v1\n" + gid.encode() + b"\n" + RUN_1.encode()
    assert g.grant_run_key(gid, RUN_1) == _sha(data)
    with pytest.raises(ValueError):
        g.grant_run_key("AB" * 16, RUN_1)
    skeleton = g.workflow_steps_skeleton(WF1)
    assert skeleton == {"steps": [{"name": "a", "config": {"threshold": 0.25, "label": None}}]}
    assert g.workflow_steps_skeleton(skeleton) == skeleton
    assert (
        g.steps_sha256(WF1) == g.steps_sha256(skeleton) == _sha(g.canonical_bytes(skeleton, True))
    )
    manifest = {"files": [], "entrypoints": {"a": "m:C"}}
    assert g.src_manifest_sha256(manifest) == _sha(g.canonical_bytes(manifest))


def test_k4_grant_cost_rounds_up_and_charges_the_whole_exclusive_node() -> None:
    shared = {"exclusive": False, "max_cpus": 16, "max_gpus": 4}
    whole = {"exclusive": True, "max_cpus": 4, "max_gpus": 2}
    assert g.grant_cost({"time": 61, "cpus": 2, "gpus": 1}, shared) == (120, 240)
    assert g.grant_cost({"time": 60, "cpus": 2, "gpus": 0}, shared) == (0, 120)
    assert g.grant_cost({"time": 60, "cpus": 1, "gpus": 0}, whole) == (0, 240)
    assert g.grant_cost({"time": 90, "cpus": 1, "gpus": 1}, whole) == (240, 480)


def test_checkpoint_text_and_hello(world: Any) -> None:
    head = {"n": 3, "h": "ab" * 32}
    text = g.checkpoint_text(SITE, LOGIN, head)
    assert text == f"umrs-gate/{SITE}/{LOGIN}\n3\n{_b64(bytes.fromhex('ab' * 32))}\n"
    out = ok(world.run("hello"))
    assert out["checkpoint"] == g.checkpoint_text(SITE, LOGIN, out["log_head"])
    assert {"grant-check", "grant-cancel"} <= set(out["verbs"]) and out["gate_version"] == "3"


def test_grant_control_body_validates() -> None:
    body = g.grant_control_body(SITE, PRINCIPAL, "cd" * 16, now=1000)
    assert set(body) == {"v", "type", "op", "site", "principal", "grant_id", "nonce", "issued",
                         "expires"}  # fmt: skip
    assert (body["op"], body["expires"] - body["issued"]) == ("grant-cancel", 300)
    for bad in ({"grant_id": "x"}, {"ttl": 601}):
        with pytest.raises(ValueError):
            g.grant_control_body(SITE, PRINCIPAL, **{"grant_id": "cd" * 16, **bad})


# ---------------------------------------------------------------------------
# grant body v1 (C1)
# ---------------------------------------------------------------------------


def _grant_for_schema(world: Any) -> dict[str, Any]:
    spec = make_spec(world)
    return make_grant(world, spec, WF1)


def test_validate_grant_accepts_the_edges(world: Any) -> None:
    grant = _grant_for_schema(world)
    assert g.validate_grant(grant) is grant
    now = grant["issued"]
    seed = [{"sha256": "0" * 64, "size": 1}]
    for over in (
        {"inputs": {"mode": "chained", "listed": seed}},
        {"inputs": {"mode": "chained", "listed": []}},
        {"inputs": {"mode": "listed", "listed": seed}},
        {"caps": {"max_jobs": 1, "max_gpu_hours": 0, "max_core_hours": 0, "max_concurrent": 1}},
        {"expires": now + g.GRANT_MAX_WINDOW},
    ):
        g.validate_grant({**grant, **over})


def _set(body: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target: Any = body
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    if value is _DEL:
        del target[parts[-1]]
    else:
        target[parts[-1]] = value


_DEL = object()
_S = "param_schema.properties"
BAD_GRANTS = [
    ("v", 2), ("type", "intent"), ("grant_id", "AB" * 16), ("site", "S!"), ("login", "-x"),
    ("principal", ""), ("accounts", []), ("accounts", ["b", "a"]), ("accounts", ["a", "a"]),
    ("accounts", [f"a{i:02d}" for i in range(17)]), ("projects", []), ("projects", ["p\n"]),
    ("workflows", []), ("workflows.0.extra", 1), ("workflows.0.params", ["label", "alpha"]),
    ("workflows.0.params", ["alpha", "zeta"]), ("workflows.0.name", "n" * 129),
    ("images", []), ("images.0.image_id", "x"), ("param_schema.additionalProperties", True),
    ("param_schema.type", "array"), (f"{_S}.alpha.type", "object"), (f"{_S}.note.maxLength", _DEL),
    (f"{_S}._x", {"type": "boolean"}), (f"{_S}.image_param", {"type": "boolean"}),
    (f"{_S}.api_base", {"type": "boolean"}), (f"{_S}.project_id", {"type": "boolean"}),
    (f"{_S}.step_name", {"type": "boolean"}), (f"{_S}.note.pattern", "x"),
    (f"{_S}.note.maxLength", 0), (f"{_S}.note.maxLength", 65537), (f"{_S}.alpha.minimum", 11),
    (f"{_S}.note.minimum", 0), (f"{_S}.label.enum", ["toolongvalue"]), (f"{_S}.label.enum", []),
    (f"{_S}.flag.enum", [1]), ("param_schema.required", ["zeta"]),
    ("param_schema.required", ["note", "alpha"]),
    ("inputs", {"mode": "listed", "listed": []}),
    ("inputs", {"mode": "any", "listed": [{"sha256": "0" * 64, "size": 1}]}),
    ("inputs", {"mode": "bogus", "listed": []}),
    ("inputs.listed", [{"sha256": "1" * 64, "size": 1}, {"sha256": "0" * 64, "size": 1}]),
    ("caps.max_jobs", 0), ("caps.max_jobs", 10001), ("caps.max_jobs", True),
    ("caps.max_gpu_hours", -1), ("caps.max_gpu_hours", 1000001),
    ("caps.max_core_hours", 10000001), ("caps.max_concurrent", 0), ("caps.max_concurrent", 65),
    ("caps.max_jobs", 1.0), ("projects", [None]), ("delegable", True), ("nonce", "x"),
    ("extra", 1), ("expires", _DEL),
]  # fmt: skip


@pytest.mark.parametrize(("path", "value"), BAD_GRANTS, ids=[str(c[0]) for c in BAD_GRANTS])
def test_validate_grant_refuses(world: Any, path: str, value: Any) -> None:
    grant = _grant_for_schema(world)
    _set(grant, path, value)
    with pytest.raises(g.ContractError) as info:
        g.validate_grant(grant)
    assert info.value.error == "bad_schema"


@pytest.mark.parametrize(
    "window",
    [(0, 0, 1 + g.GRANT_MAX_WINDOW), (0, -1, 100), (0, 100, 100), (0, 200, 100)],
)
def test_validate_grant_refuses_bad_windows(world: Any, window: tuple[int, int, int]) -> None:
    grant = _grant_for_schema(world)
    issued = grant["issued"]
    grant.update(
        issued=issued + window[0], not_before=issued + window[1], expires=issued + window[2]
    )
    with pytest.raises(g.ContractError):
        g.validate_grant(grant)


def test_validate_grant_refuses_too_many_listed_and_properties(world: Any) -> None:
    grant = _grant_for_schema(world)
    listed = [{"sha256": f"{i:064x}", "size": 1} for i in range(g.MAX_LISTED_INPUTS + 1)]
    with pytest.raises(g.ContractError):
        g.validate_grant({**grant, "inputs": {"mode": "listed", "listed": listed}})
    props = {f"p{i:03d}": {"type": "boolean"} for i in range(129)}
    schema = {**grant["param_schema"], "properties": props, "required": []}
    with pytest.raises(g.ContractError):
        g.validate_grant({**grant, "param_schema": schema, "workflows": [
            {**grant["workflows"][0], "params": []}]})  # fmt: skip


# ---------------------------------------------------------------------------
# r4 building the schema; run params (C1, K9)
# ---------------------------------------------------------------------------


def test_grant_param_schema_normalizes_narrows_and_defaults() -> None:
    wf = {"parameters": [
        {"name": "alpha", "type": "integer", "minimum": 0.5, "maximum": 9.7, "required": True,
         "description": "d", "title": "t", "default": 1},
        {"name": "note", "type": "string"},
        {"name": "project_id", "type": "string"}, {"name": "input_files", "type": "array"},
        {"name": "flag", "type": "boolean", "enum": [True]},
        {"name": "ratio", "type": "number", "minimum": -1.5},
    ]}  # fmt: skip
    schema = g.grant_param_schema(wf)
    assert schema == {
        "type": "object", "additionalProperties": False, "required": ["alpha"],
        "properties": {
            "alpha": {"type": "integer", "minimum": 1, "maximum": 9},
            "flag": {"type": "boolean", "enum": [True]},
            "note": {"type": "string", "maxLength": 1024},
            "ratio": {"type": "number", "minimum": -1},
        },
    }  # fmt: skip
    assert g.grant_param_schema({"parameter_schema": {"type": "object"}})["properties"] == {}


@pytest.mark.parametrize(
    ("name", "prop"),
    [
        ("script", {"type": "string"}), ("input_path", {"type": "string"}),
        ("model_url", {"type": "string"}), ("api_base", {"type": "string"}),
        ("base_url", {"type": "string"}), ("image_param", {"type": "string"}),
        ("_x", {"type": "string"}), ("Prompt_Template", {"type": "string"}),
        ("a", {"type": "string", "format": "uri"}), ("a", {"type": "string", "pattern": "x"}),
        ("a", {"type": "array", "items": {}}), ("a", {"$ref": "#/x"}),
        ("a", {"type": "string", "x-umrs": 1}), ("a", {"type": "object"}), ("a", {"type": "null"}),
        ("a", {}), ("a", {"type": ["string", "null"]}), ("a", {"type": "number", "enum": [0.5]}),
        ("a", {"type": "integer", "minimum": 0.2, "maximum": 0.8}),
        ("a", {"type": "string", "maxLength": 2, "enum": ["abc"]}),
    ],
)  # fmt: skip
def test_grant_param_schema_refuses_naming_the_parameter(name: str, prop: Any) -> None:
    with pytest.raises(ValueError, match=name if name != "a" else "parameter a"):
        g.grant_param_schema({"parameter_schema": {"type": "object", "properties": {name: prop}}})


@pytest.mark.parametrize(
    "name",
    [
        "scriptPath", "pythonCode", "callbackUrl", "commands", "scripts", "config_files",
        "urls", "shellCmd", "templates", "hostname", "filepath", "codeSnippet", "HTTPUrl",
        "inputPaths", "addresses_url", "entryPoint", "entryPoints", "callBack", "modelOutput",
        "modelOutputs", "apiBase", "apiBases", "imageParam",
    ],
)  # fmt: skip
def test_k9b_camel_case_plural_and_compound_code_names_are_refused(name: str) -> None:
    schema = {"type": "object", "properties": {name: {"type": "string"}}}
    with pytest.raises(ValueError, match="may carry code or a place"):
        g.grant_param_schema({"parameter_schema": schema})


@pytest.mark.parametrize(
    "name", ["profile", "filter", "learning_rate", "epochs", "hostile", "pathology", "batchSize"]
)
def test_k9b_ordinary_names_stay_grantable(name: str) -> None:
    schema = {"type": "object", "properties": {name: {"type": "integer"}}}
    assert name in g.grant_param_schema({"parameter_schema": schema})["properties"]


def test_merge_grant_param_schemas() -> None:
    one = g.grant_param_schema({"parameters": [
        {"name": "a", "type": "integer", "required": True},
        {"name": "b", "type": "boolean", "required": True}]})  # fmt: skip
    two = g.grant_param_schema({"parameters": [
        {"name": "a", "type": "integer", "required": True}, {"name": "c", "type": "boolean"}]})  # fmt: skip
    merged = g.merge_grant_param_schemas([one, two])
    assert (sorted(merged["properties"]), merged["required"]) == (["a", "b", "c"], ["a"])
    clash = g.grant_param_schema({"parameters": [{"name": "a", "type": "number"}]})
    with pytest.raises(ValueError, match="grant them separately"):
        g.merge_grant_param_schemas([one, clash])


PARAM_CASES = [
    ({"alpha": "3"}, "alpha"), ({"alpha": True}, "alpha"), ({"alpha": 11}, "alpha"),
    ({"alpha": -1}, "alpha"), ({"ratio": 1.5}, "ratio"), ({"ratio": float("inf")}, "ratio"),
    ({"ratio": None}, "ratio"), ({"label": "z"}, "label"), ({"label": "x" * 9}, "label"),
    ({"flag": 1}, "flag"), ({"note": "n" * 17}, "note"), ({"zeta": 1}, "zeta"),
    ({"alpha": _DEL}, "alpha"), ({"mcp_tools": []}, "mcp_tools"),
    ({"project_id": "proj-2"}, "project_id"), ({"input_files": ["../x"]}, "input_files"),
    ({"input_files": ["s3://BAD/x"]}, "input_files"), ({"input_files": "x"}, "input_files"),
    ({"input_files": ["k"] * 10001}, "input_files"),
]  # fmt: skip


@pytest.mark.parametrize(("change", "name"), PARAM_CASES, ids=[c[1] for c in PARAM_CASES])
def test_params_problems_name_the_first_problem(change: dict[str, Any], name: str) -> None:
    params = {**PARAMS, "flag": True, "note": "short"}
    for key, value in change.items():
        if value is _DEL:
            del params[key]
        else:
            params[key] = value
    assert g.params_problems(SCHEMA, params, "proj-1").startswith(f"{name}:")


def test_params_problems_accepts_and_scopes_to_the_workflow() -> None:
    files = ["data/x.bin", "s3://wf-runner/data/y.bin"]
    params = {**PARAMS, "flag": False, "note": "n" * 16, "ratio": 1, "input_files": files}
    assert g.params_problems(SCHEMA, params, "proj-1") == ""
    assert g.params_problems(SCHEMA, params, "proj-1", ["alpha", "flag", "label", "ratio"]) == (
        "note: not a parameter this workflow declares under the grant"
    )
    assert g.params_problems(SCHEMA, {"project_id": "proj-1"}, "proj-1", ["note"]) == ""
    assert g.params_problems(SCHEMA, [], "proj-1") == "params: must be an object"


def test_grant_covers_passes_and_names_the_field(world: Any) -> None:
    run = new_run(world)
    skeleton = g.workflow_steps_skeleton(WF1)
    assert g.grant_covers(run.grant, run.spec, skeleton) == ""
    other = copy.deepcopy(skeleton)
    other["steps"][0]["config"]["threshold"] = 1
    assert g.grant_covers(run.grant, run.spec, other).startswith("steps_sha256:")
    assert g.grant_covers(run.grant, run.spec, {"steps": "x"}).startswith("workflow_steps:")


# ---------------------------------------------------------------------------
# submit with a grant: the C2 check order
# ---------------------------------------------------------------------------


def _raw(**envelope: Any) -> Any:
    return lambda w, _r, wd: w.run(f"submit {wd} a", json.dumps(envelope).encode())


def _env(**kw: Any) -> Any:
    return lambda _w, r, wd: r.submit(wd, **kw)


def _grant_over(**over: Any) -> Any:
    def scenario(w: Any, r: Run, wd: str) -> Any:
        r.grant.update(over)
        return r.submit(wd)

    return scenario


def _spec_over(**over: Any) -> Any:
    def scenario(w: Any, r: Run, wd: str) -> Any:
        r.spec.update(over)
        return r.submit(wd)

    return scenario


def _fresh(
    grant: dict[str, Any] | None = None, window: tuple[int, int, int] | None = None, **spec_kw: Any
) -> Any:
    """A run whose spec (and grant) differ, on a workdir of its own run key.
    ``window``: the grant's issued, not_before and expires relative to now."""

    def scenario(w: Any, r: Run, wd: str) -> Any:
        spec = make_spec(w, **spec_kw)
        base = {**make_grant(w, r.spec, WF1), **(grant or {})}
        if window is not None:
            now = int(time.time())
            base.update(
                zip(("issued", "not_before", "expires"), (now + t for t in window), strict=True)
            )
        run = Run(w, WF1, base, spec)
        return run.submit(run.ready())

    return scenario


def _s_not_canonical_grant(w: Any, r: Run, wd: str) -> Any:
    return r.submit(wd, payload=json.dumps(r.grant, indent=1).encode())


def _s_key_type(w: Any, r: Run, wd: str) -> Any:
    cfg = json.loads((w.root / "gate.json").read_text())
    (w.root / "gate.json").write_text(json.dumps({**cfg, "allow_non_sk": False}))
    return r.submit(wd)


def _s_other_run_key(w: Any, r: Run, wd: str) -> Any:
    wd2 = r.ready(key="ab" * 32)
    return r.submit(wd2)


def _s_wrong_step(w: Any, r: Run, wd: str) -> Any:
    wd2 = r.ready(step="zz")
    return w.run(f"submit {wd2} zz", r.envelope())


def _s_skeleton_extra(w: Any, r: Run, wd: str) -> Any:
    raw = {"steps": [{**WF1["steps"][0]}]}
    return r.submit(wd, steps_bytes=g.canonical_bytes(raw, True))


def _s_config(w: Any, r: Run, wd: str) -> Any:
    r.spec["steps"][0]["config"]["threshold"] = 0.5
    return r.submit(wd)


def _s_image(w: Any, r: Run, wd: str) -> Any:
    r.spec["steps"][0]["image"] = w.add_image(salt="rogue")
    return r.submit(wd)


def _s_endpoint(w: Any, r: Run, wd: str) -> Any:
    r.spec["steps"][0]["endpoint"] = {"host": "h", "port": 1, "kind": "vllm"}
    return r.submit(wd)


def _s_conflict(w: Any, r: Run, wd: str) -> Any:
    ok(r.submit(wd))
    twin = Run(w, WF1, {**r.grant, "nonce": secrets.token_hex(16)}, make_spec(w, run_id=RUN_2))
    return twin.submit(twin.ready())


def _s_cancelled(w: Any, r: Run, wd: str) -> Any:
    (w.root / "state" / "grants" / f"{r.gid}.cancelled").write_text('{"cancelled":1}')
    return r.submit(wd)


def _s_epoch(w: Any, r: Run, wd: str) -> Any:
    g.write_epoch(str(w.root), r.grant["issued"] + 1)
    return r.submit(wd)


def _s_jobs(w: Any, r: Run, wd: str) -> Any:
    r.grant["caps"] = {**r.grant["caps"], "max_jobs": 1}
    write_counters(r, {"wf-0000000000ff/a": {"job_id": "9", "state": "settled", "submitted": 1,
                       "wall": 60, "reserved": [0, 60], "charged": [0, 60], "actual": [],
                       "bound": False}})  # fmt: skip
    return r.submit(wd)


_NOW = int(time.time())
_BIG = b"x" * (g.GRANT_MAX_BYTES + 1)
GRANT_MATRIX = [
    ("5 endpoint_credential with a grant", _env(endpoint_credential={"headers": {}}), "bad_envelope"),
    ("5 a missing key", _raw(grant={"payload": "", "signature": "x"}, run_spec=""), "bad_envelope"),
    ("5 grant not an object", _raw(grant="x", run_spec="", workflow_steps=""), "bad_envelope"),
    ("5 run_spec not base64", _raw(grant={"payload": "", "signature": "x"}, run_spec="*", workflow_steps=""), "bad_envelope"),
    ("5 unsigned grant", _raw(grant={"payload": ""}, run_spec="", workflow_steps=""), "not_signed"),
    ("5 grant too large", _env(payload=_BIG), "too_large"),
    ("5 run spec too large", _env(spec_bytes=b"x" * (g.INTENT_MAX_BYTES + 1)), "too_large"),
    ("5 skeleton too large", _env(steps_bytes=b"x" * (g.WORKFLOW_STEPS_MAX_BYTES + 1)), "too_large"),
    ("6 another key", lambda w, r, wd: r.submit(wd, key=w.keys.other), "bad_signature"),
    ("6 intent namespace", _env(ns=gx.INTENT_NS), "bad_signature"),
    ("7 key type", _s_key_type, "key_type_not_allowed"),
    ("8 not canonical", _s_not_canonical_grant, "not_canonical"),
    ("8 bad schema", _grant_over(delegable=True), "bad_schema"),
    ("9 wrong site", _grant_over(site="s2"), "wrong_site"),
    ("9 wrong principal", _grant_over(principal="bob"), "wrong_principal"),
    ("9 wrong login", _grant_over(login="nobody"), "wrong_login"),
    ("10 run spec not canonical", _env(spec_bytes=b'{"v": 1}'), "not_canonical"),
    ("10 run spec bad schema", _spec_over(nonce="x"), "bad_schema"),
    ("10 run spec wrong site", _spec_over(site="s2"), "wrong_site"),
    ("10 another run key", _s_other_run_key, "wrong_intent"),
    ("10 step not in the run spec", _s_wrong_step, "wrong_step"),
    ("10 profile", _spec_over(site_profile_sha256="0" * 64), "profile_mismatch"),
    ("11 not a skeleton", _s_skeleton_extra, "grant_mismatch"),
    ("11 account", _grant_over(accounts=["acct2"]), "grant_mismatch"),
    ("11 project", _fresh(params={**PARAMS, "project_id": "p2"}, project="p2"), "grant_mismatch"),
    ("11 workflow", _spec_over(workflow="other"), "grant_mismatch"),
    ("11 config", _s_config, "grant_mismatch"),
    ("11 image", _s_image, "grant_mismatch"),
    ("11 endpoint", _s_endpoint, "grant_mismatch"),
    ("11 param bound", _fresh(params={**PARAMS, "alpha": 11}), "grant_mismatch"),
    ("11 mcp_tools", _fresh(params={**PARAMS, "mcp_tools": ["x"]}), "grant_mismatch"),
    ("13 conflict", _s_conflict, "grant_conflict"),
    ("13 cancelled", _s_cancelled, "grant_cancelled"),
    ("13 K7 epoch", _s_epoch, "grant_cancelled"),
    ("13 not yet valid", _fresh(window=(0, 400, 900)), "grant_not_yet_valid"),
    ("13 K8 issued ahead", _fresh(window=(300, 300, 900)), "grant_not_yet_valid"),
    ("13 expired", _fresh(window=(-900, -900, -200)), "grant_expired"),
    ("14 run spec expired", _spec_over(issued=_NOW - 7200, expires=_NOW - 3600), "expired"),
    ("15 account not held", _fresh({"accounts": ["acct1", "acct9"]}, account="acct9"), "account_not_held"),
    ("19b jobs", _s_jobs, "grant_exhausted"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("label", "scenario", "name"), GRANT_MATRIX, ids=[case[0] for case in GRANT_MATRIX]
)
def test_grant_submit_refusals_in_contract_order(
    world: Any, monkeypatch: pytest.MonkeyPatch, label: str, scenario: Any, name: str
) -> None:
    monkeypatch.setattr(g, "LOCK_WAIT", 0.2)
    run = new_run(world)
    wd = run.ready()
    queued = len(world.jobs())
    result = scenario(world, run, wd)
    gx.refused(result, name)
    assert len(world.jobs()) == queued + ("conflict" in label), "a refused submit queued a job"


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda r: r.grant.update(accounts=["acct2"]), "account:"),
        (lambda r: r.spec.update(workflow="other"), "workflow:"),
        (lambda r: r.grant["workflows"][0].update(src_manifest_sha256="0" * 64), "workflow:"),
        (lambda r: r.grant["workflows"][0].update(steps_sha256="0" * 64), "steps_sha256:"),
        (lambda r: r.spec["steps"][0]["config"].update(threshold=1), "config:"),
        (lambda r: r.spec.update(trusted_image={**r.spec["trusted_image"], "ref": "x/y"}), "images:"),
        (lambda r: r.spec["steps"][0].update(endpoint={"host": "h", "port": 1, "kind": "vllm"}), "endpoint:"),
    ],
)  # fmt: skip
def test_coverage_misses_name_their_field(world: Any, mutate: Any, detail: str) -> None:
    run = new_run(world)
    wd = run.ready()
    mutate(run)
    result = run.submit(wd)
    refused(result, "grant_mismatch")
    assert result.err["detail"].startswith(detail), result.err


def test_k9a_a_param_only_another_workflow_declares_is_refused(world: Any) -> None:
    run = new_run(world, params={**PARAMS, "note": "hello"})
    entry = run.grant["workflows"][0]
    other = {**entry, "name": "other", "params": ["note"]}
    run.grant["workflows"] = [{**entry, "params": ["alpha", "label", "ratio"]}, other]
    result = run.submit(run.ready())
    refused(result, "grant_mismatch")
    assert result.err["detail"].startswith("params: note: not a parameter this workflow declares")


def test_role_bound_grants_separate_helper_images_from_step_images(world: Any) -> None:
    run = new_run(world, grant={"trusted_images": [world.trusted]})
    ok(run.submit(run.ready()))
    swapped = run.again(run_id=RUN_2)
    swapped.spec["trusted_image"] = world.image
    result = swapped.submit(swapped.ready())
    refused(result, "grant_mismatch")
    assert result.err["detail"].startswith("images: trusted_image")


def test_legacy_grants_without_separate_helper_roles_are_rejected(world: Any) -> None:
    run = new_run(world)
    run.grant.pop("trusted_images")
    run.spec["trusted_image"] = world.image
    result = run.submit(run.ready())
    refused(result, "grant_mismatch")
    assert "trusted_images" in result.err["detail"]
    assert "reissue" in result.err["detail"]


def test_a_single_image_legacy_grant_has_no_role_substitution_to_permit(world: Any) -> None:
    run = new_run(world)
    run.grant.pop("trusted_images")
    run.grant["images"] = [world.image]
    run.spec["trusted_image"] = world.image
    ok(run.submit(run.ready()))
    swapped = run.again(run_id=RUN_2)
    swapped.spec["trusted_image"] = world.trusted
    refused(swapped.submit(swapped.ready()), "grant_mismatch")


def test_earlier_checks_win(world: Any) -> None:
    run = new_run(world)
    wd = run.ready()
    run.grant["site"] = "s2"
    run.spec["workflow"] = "other"
    refused(run.submit(wd, key=world.keys.other), "bad_signature")
    refused(run.submit(wd), "wrong_site")
    run.grant["site"] = SITE
    refused(run.submit(wd), "grant_mismatch")


# ---------------------------------------------------------------------------
# a successful grant submit
# ---------------------------------------------------------------------------


def test_a_grant_submit_reserves_logs_and_answers(world: Any) -> None:
    run = new_run(world)
    wd = run.ready()
    out = ok(run.submit(wd))
    assert set(out) == {"job_id", "wd", "grant_id", "run_key", "step", "log_head", "reserved"}
    assert (out["grant_id"], out["run_key"], out["step"]) == (run.gid, run.key, "a")
    assert out["reserved"] == {"gpu_seconds": 0, "core_seconds": 1200}
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert entry == {"job_id": out["job_id"], "state": "live", "submitted": entry["submitted"],
                     "wall": 600, "reserved": [0, 1200], "charged": [0, 0], "actual": [],
                     "bound": False}  # fmt: skip
    spec_id = _sha(g.canonical_bytes(run.spec, True))
    signer = g.fingerprint(g.ed25519_blob(world.keys.signer_b64))
    assert last_log(world)["detail"] == (
        f"grant={run.gid} run={RUN_1} spec={spec_id} step=a wd={wd} job={out['job_id']} "
        f"gpu_s=0 core_s=1200 signer={signer}"
    )  # K17


def test_a_grant_run_is_named_and_recorded_by_its_run_key(world: Any) -> None:
    run = new_run(world)
    wd = run.ready()
    ok(run.submit(wd))
    script = (world.work / wd / "job.sbatch").read_text()
    assert f"--job-name=umrs-gated-{run.key[:16]}-a" in script
    assert (world.root / "state" / "intents" / run.key / "grant").read_text() == run.gid
    stored = json.loads((world.root / "state" / "intents" / run.key / "intent.json").read_text())
    assert set(stored) == {"grant", "run_spec", "workflow_steps"}
    assert g.verify_log(str(world.log))


def test_k4_the_gate_reserves_rounded_walltime_and_the_whole_node(world: Any) -> None:
    run = new_run(world, step_over={"time": 61, "gpus": 1})
    assert ok(run.submit(run.ready()))["reserved"] == {"gpu_seconds": 120, "core_seconds": 240}
    node = run.again(run_id=RUN_2, step_over={"partition": "cpu", "cpus": 1})
    assert ok(node.submit(node.ready()))["reserved"] == {"gpu_seconds": 0, "core_seconds": 2400}
    assert "gpu_s=0 core_s=2400" in last_log(world)["detail"]


def test_listed_mode_passes_listed_digests_only(world: Any) -> None:
    data = b"input bytes"
    listed = {"mode": "listed", "listed": [{"sha256": _sha(data), "size": len(data)}]}
    run = new_run(world, grant={"inputs": listed}, objects={"a": (obj("data/x.bin", data),)})
    ok(run.submit(run.ready(blobs=(data,))))
    stray = run.again(run_id=RUN_2, objects={"a": (obj("data/y.bin", b"other"),)})
    result = stray.submit(stray.ready(blobs=(b"other",)))
    refused(result, "grant_mismatch")
    assert result.err["detail"] == f"inputs: {IN_BUCKET}/data/y.bin is not listed"


def _complete(world: Any, run: Run, wd: str, job: str, data: bytes) -> None:
    reply = {"status": "ok", "result": {"output_keys": ["o/1"]}}
    world.finish(wd, job, reply, {f"{OUT_BUCKET}/o/1": data})


def test_k2_k10_chained_inputs_come_only_from_this_grants_outputs(world: Any) -> None:
    seed = b"seed bytes"
    mode = {"mode": "chained", "listed": [{"sha256": _sha(seed), "size": len(seed)}]}
    run = new_run(world, grant={"inputs": mode}, objects={"a": (obj("s.bin", seed),)})
    wd = run.ready(blobs=(seed,))
    job = ok(run.submit(wd))["job_id"]  # K10: the seed passes on the first run
    _complete(world, run, wd, job, b"made here")
    assert ok(world.run(f"collect-out {wd}")) == {"files": 1, "bytes": 9}  # K2
    index = world.root / "state" / "grants" / run.gid / "outputs" / _sha(b"made here")
    assert index.read_text() == "9"
    made = obj("o/1", b"made here", bucket=OUT_BUCKET)
    second = run.again(run_id=RUN_2, objects={"a": (made,)})
    ok(second.submit(second.ready(blobs=(b"made here",))))
    stranger = new_run(world, grant={"inputs": {"mode": "chained", "listed": []}}, run_id=RUN_3)
    wd_s = stranger.ready()
    job_s = ok(stranger.submit(wd_s))["job_id"]
    _complete(world, stranger, wd_s, job_s, b"made elsewhere")
    ok(world.run(f"collect-out {wd_s}"))
    for data in (b"made elsewhere", b"never made"):
        third = run.again(run_id=RUN_3, objects={"a": (obj("o/9", data, bucket=OUT_BUCKET),)})
        result = third.submit(third.ready(blobs=(data,)))
        refused(result, "grant_mismatch")
        assert result.err["detail"].endswith("was not produced under this grant")


def test_any_mode_takes_any_data(world: Any) -> None:
    data = b"anything"
    run = new_run(world, objects={"a": (obj("d/x", data),)})
    ok(run.submit(run.ready(blobs=(data,))))


def test_outputs_index_is_capped(world: Any) -> None:
    run = new_run(world, grant={"caps": {"max_jobs": 1, "max_gpu_hours": 1, "max_core_hours": 0,
                                         "max_concurrent": 1}})  # fmt: skip
    world.write_profile(caps={**world.profile["caps"], "max_out_files": 2})
    run.spec["site_profile_sha256"] = world.profile_sha
    wd = run.ready()
    job = ok(run.submit(wd))["job_id"]
    reply = {"status": "ok", "result": {"output_keys": ["o/1"]}}
    world.finish(wd, job, reply, {f"{OUT_BUCKET}/o/1": b"one"})
    outputs = world.root / "state" / "grants" / run.gid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    for name in ("e" * 64, "f" * 64):
        (outputs / name).write_text("1")
    ok(world.run(f"collect-out {wd}"))
    assert not (outputs / _sha(b"one")).exists()
    details = [json.loads(line)["detail"] for line in world.log.read_text().splitlines()]
    assert f"outputs_capped grant={run.gid} skipped=1" in details


def test_run_spec_changed_on_the_second_step_and_same_bytes_pass(world: Any) -> None:
    run = new_run(world, wf=WF2)
    wd = run.ready()
    job = ok(run.submit(wd))["job_id"]
    _complete(world, run, wd, job, b"out-a")
    world.run(f"read-reply {wd}")
    ok(world.run(f"collect-out {wd}"))
    wd_b = run.ready(step="b", blobs=(b"out-a",))
    changed = Run(world, WF2, run.grant, {**run.spec, "nonce": secrets.token_hex(16)})
    refused(changed.submit(wd_b, "b"), "run_spec_changed")
    ok(run.submit(wd_b, "b"))
    assert set(run.counters()["entries"]) == {f"{RUN_1}/a", f"{RUN_1}/b"}


def test_max_run_seconds_closes_later_steps_of_a_grant_run(world: Any) -> None:
    run = new_run(world, wf=WF2)
    wd = run.ready()
    job = ok(run.submit(wd))["job_id"]
    _complete(world, run, wd, job, b"out-a")
    world.run(f"read-reply {wd}")
    ok(world.run(f"collect-out {wd}"))
    (world.root / "state" / "intents" / run.key / "started").write_text(str(_NOW - 99999))
    refused(run.submit(run.ready(step="b", blobs=(b"out-a",)), "b"), "expired")


# ---------------------------------------------------------------------------
# reservations: release, timeout, K3
# ---------------------------------------------------------------------------


def test_an_sbatch_that_queued_nothing_frees_the_reservation(world: Any) -> None:
    run = new_run(world)
    world.behave(sbatch_rc=1)
    assert run.submit(run.ready()).code == 5
    assert run.counters()["entries"] == {}
    world.behave()
    ok(run.submit(run.ready()))


def test_an_uncertain_sbatch_keeps_the_reservation_and_lookup_rekeys_it(world: Any) -> None:
    run = new_run(world)
    world.behave(sbatch_rc=1, queue_anyway=True, squeue_rc=1)
    assert run.submit(run.ready()).code == 5
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["job_id"]) == ("reserved", "")
    world.behave()
    assert ok(world.run(f"lookup {run.key} a"))["job_id"] == "1000"
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["job_id"]) == ("live", "1000")
    assert (world.root / "state" / "jobs" / "1000.grant").read_text() == f"{run.gid} {RUN_1}/a"


def test_a_refused_nonce_drops_the_reservation(world: Any) -> None:
    run = new_run(world)
    wd = run.ready()
    nonce = world.root / "state" / "intents" / run.key / "a.json"
    real = g._create_exclusive

    def taken(path: str, data: bytes) -> bool:
        if path == str(nonce):
            return False
        return bool(real(path, data))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(g, "_create_exclusive", taken)
        refused(run.submit(wd), "replayed")
    assert run.counters()["entries"] == {}


def test_k3_a_pruned_run_cannot_run_a_settled_step_again(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    assert ok(world.run(f"usage {job}"))["jobs"][0]["settled"] is True
    shutil.rmtree(world.root / "state" / "intents" / run.key)
    fresh = run.again()  # the same run_id: the same run key, no nonce left
    refused(fresh.submit(fresh.ready()), "replayed")


def test_k3_a_stale_reservation_without_a_nonce_counts_once(world: Any) -> None:
    caps = {"max_jobs": 1, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 1}
    run = new_run(world, grant={"caps": caps})
    stale = {"job_id": "", "state": "reserved", "submitted": int(time.time()), "wall": 600,
             "reserved": [0, 1200], "charged": [0, 0], "actual": [], "bound": False}  # fmt: skip
    write_counters(run, {f"{RUN_1}/a": stale})
    ok(run.submit(run.ready()))
    assert [e["state"] for e in run.counters()["entries"].values()] == ["live"]


def test_k3_a_grant_runs_state_outlives_the_b2_limit(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    (world.root / "state" / "intents" / run.key / "started").write_text(str(_NOW - 30 * 86400))
    ok(world.run("sweep"))
    assert (world.root / "state" / "intents" / run.key / "intent.json").exists()


# ---------------------------------------------------------------------------
# settlement (K5) and caps
# ---------------------------------------------------------------------------


def _usage_row(elapsed: str, tres: str = "cpu=2,mem=1G", cpus: str = "2") -> str:
    return "{}|{}|" + f"{elapsed}|{tres}|{cpus}|acct1|gpu|2026-11-09T10:00:00|2026-11-09T10:20:00"


@pytest.mark.parametrize(
    ("row", "actual", "charged"),
    [
        (_usage_row("00:05:00"), [0, 600], [0, 600]),
        (_usage_row("00:20:00", "cpu=2,gres/gpu=1"), [1200, 2400], [1200, 2400]),
        (_usage_row("00:10:00", "cpu=64", "64"), [0, 38400], [0, 38400]),
        (_usage_row("00:10:00", "cpu=2,gres/gpu:h100=1"), [600, 1200], [600, 1200]),
        (_usage_row("00:10:00", "cpu=1", "1"), [0, 1200], [0, 1200]),
        (_usage_row("INVALID"), [], [0, 1200]),
    ],
    ids=["below", "above", "overallocated", "typed_gpu", "fewer_than_asked", "unparsable"],
)
def test_settlement_charges_what_was_used(
    world: Any, row: str, actual: list[int], charged: list[int]
) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    world.behave(usage=row)
    usage = ok(world.run(f"usage {job}"))["jobs"][0]
    assert (usage["grant_id"], usage["settled"]) == (run.gid, True)
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["actual"], entry["charged"]) == ("settled", actual, charged)


@pytest.mark.parametrize(
    ("tres", "gpus"),
    [
        ("billing=1,cpu=1,gres/gpu=2,mem=1G", 2),
        ("gres:gpu=3", 3),
        ("gpu=4", 4),
        ("gres/gpu:2", 2),
        ("billing=1,cpu=1,gres/gpu:h100=1,mem=1G,node=1", 1),
        ("cpu=8,gres/gpu:a100=4,gres/gpu:v100=2,mem=64G", 6),
        ("gres/gpu=2,gres/gpu:a100=2", 2),
        ("billing=8,cpu=8,mem=64G,node=1", 0),
        ("", 0),
    ],
)
def test_alloc_gpus_reads_typed_and_untyped_entries(tres: str, gpus: int) -> None:
    assert g._alloc_gpus(tres) == gpus


def test_settlement_missing_alloc_tres_is_charged_at_reserved_gpus() -> None:
    entry = {"state": "live", "wall": 1800, "reserved": [1800, 1800]}
    g._settle_measured(entry, {"elapsed": "00:30:00", "alloc_cpus": "1"})
    assert entry["actual"] == entry["charged"] == [1800, 1800]


def test_k21_a_gpu_job_whose_accounting_omits_gpus_counts_against_the_gpu_cap(
    world: Any,
) -> None:
    caps = {"max_jobs": 10, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 1}
    step = {"gpus": 1, "time": 1800, "cpus": 1}
    run = new_run(world, grant={"caps": caps}, step_over=step)
    first = ok(run.submit(run.ready()))["job_id"]
    world.set_state(first, "COMPLETED")
    world.behave(usage=_usage_row("00:30:00", "billing=1,cpu=1,mem=1G,node=1", "1"))
    second = run.again(run_id=RUN_2, step_over=step)
    job = ok(second.submit(second.ready()))["job_id"]
    assert run.counters()["entries"][f"{RUN_1}/a"]["charged"] == [1800, 1800]
    world.set_state(job, "COMPLETED")
    third = run.again(run_id=RUN_3, step_over=step)
    result = third.submit(third.ready())
    refused(result, "grant_exhausted")
    assert result.err["detail"] == "gpu_hours"


def test_k21_cpus_slurm_added_beyond_the_request_count_against_the_core_cap(
    world: Any,
) -> None:
    caps = {"max_jobs": 10, "max_gpu_hours": 0, "max_core_hours": 2, "max_concurrent": 1}
    step = {"gpus": 0, "time": 1800, "cpus": 1}
    run = new_run(world, grant={"caps": caps}, step_over=step)
    first = ok(run.submit(run.ready()))["job_id"]
    world.set_state(first, "COMPLETED")
    world.behave(usage=_usage_row("00:30:00", "cpu=64,mem=500G", "64"))
    second = run.again(run_id=RUN_2, step_over=step)
    result = second.submit(second.ready())
    refused(result, "grant_exhausted")
    assert result.err["detail"] == "core_hours"
    assert run.counters()["entries"][f"{RUN_1}/a"]["charged"] == [0, 64 * 1800]


def test_concurrency_is_freed_by_settlement_inside_the_reservation(world: Any) -> None:
    caps = {"max_jobs": 10, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 1}
    run = new_run(world, grant={"caps": caps})
    job = ok(run.submit(run.ready()))["job_id"]
    second = run.again(run_id=RUN_2)
    result = second.submit(second.ready())
    refused(result, "grant_exhausted")
    assert result.err["detail"] == "concurrent"
    world.set_state(job, "COMPLETED")
    ok(second.submit(second.ready()))
    assert run.counters()["entries"][f"{RUN_1}/a"]["state"] == "settled"


def test_hour_caps_count_reserved_and_charged_seconds(world: Any) -> None:
    caps = {"max_jobs": 10, "max_gpu_hours": 1, "max_core_hours": 1, "max_concurrent": 4}
    run = new_run(world, grant={"caps": caps}, step_over={"gpus": 1, "time": 1800, "cpus": 1})
    ok(run.submit(run.ready()))
    second = run.again(run_id=RUN_2, step_over={"gpus": 1, "time": 1800, "cpus": 1})
    ok(second.submit(second.ready()))
    third = run.again(run_id=RUN_3, step_over={"gpus": 1, "time": 60, "cpus": 1})
    result = third.submit(third.ready())
    refused(result, "grant_exhausted")
    assert result.err["detail"] == "gpu_hours"
    core = run.again(run_id="wf-000000000004", step_over={"gpus": 0, "time": 60, "cpus": 1})
    result = core.submit(core.ready())
    assert result.err["detail"] == "core_hours"


def test_k5_a_job_sacct_never_reports_settles_at_its_bound(world: Any) -> None:
    caps = {"max_jobs": 10, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 1}
    run = new_run(world, grant={"caps": caps})
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")  # gone from squeue
    world.behave(sacct_omit=[job])  # purged from accounting
    second = run.again(run_id=RUN_2)
    refused(second.submit(second.ready()), "grant_exhausted")  # not past its bound yet
    age_entries(run, 10_000)
    ok(second.submit(second.ready()))
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["bound"], entry["actual"]) == ("settled", True, [])
    assert entry["charged"] == entry["reserved"] == [0, 1200]


def test_k5_a_reservation_never_resolved_settles_at_its_bound(world: Any) -> None:
    run = new_run(world)
    world.behave(sbatch_rc=1, squeue_rc=1)
    assert run.submit(run.ready()).code == 5
    world.behave()
    age_entries(run, 10_000)
    ok(world.run("sweep"))
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["bound"], entry["charged"]) == ("settled", True, [0, 1200])


def test_k5_a_sleeping_sacct_does_not_make_a_concurrent_submit_busy(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(g, "LOCK_WAIT", 1.0)
    caps = {"max_jobs": 10, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 4}
    run = new_run(world, grant={"caps": caps})
    ok(run.submit(run.ready()))  # an open entry for the heavy submit to settle
    heavy = run.again(run_id=RUN_2, step_over={"gpus": 1, "time": 3601})
    light = run.again(run_id=RUN_3)
    wd_heavy, wd_light = heavy.ready(), light.ready()
    env_heavy, env_light = heavy.envelope(), light.envelope()
    world.behave(sacct_sleep=3)
    results: dict[str, Any] = {}
    thread = threading.Thread(
        target=lambda: results.update(heavy=world.run(f"submit {wd_heavy} a", env_heavy))
    )
    thread.start()
    _wait_for_usage_call(world)
    started = time.monotonic()
    results["light"] = world.run(f"submit {wd_light} a", env_light)
    elapsed = time.monotonic() - started
    thread.join(timeout=60)
    ok(results["light"])
    assert elapsed < 3, "the light submit waited for the heavy one's sacct"
    refused(results["heavy"], "grant_exhausted")
    assert results["heavy"].err["detail"] == "gpu_hours"


def _wait_for_usage_call(world: Any) -> None:
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if any("-j" in call["args"] for call in world.calls("sacct")):
            return
        time.sleep(0.05)
    raise AssertionError("the heavy submit never called sacct")


def _spawn(world: Any, interp: str, command: str, stdin: bytes) -> subprocess.Popen[bytes]:
    """A gate process as sshd starts it, its request already on a file stdin."""
    env = {"HOME": str(world.home), "PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": command}
    request = world.tmp / f"request-{secrets.token_hex(4)}"
    request.write_bytes(stdin)
    with open(request, "rb") as handle:
        return subprocess.Popen(
            [interp, "-I", str(gx.GATE), "--site", SITE],
            stdin=handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )


def _race(world: Any, runs: list[Run]) -> list[tuple[int, Any]]:
    """Two gate processes submit at once; each answer as (exit code, error)."""
    prepared = [(f"submit {r.ready()} a", r.envelope()) for r in runs]
    procs = [_spawn(world, sys.executable, command, data) for command, data in prepared]
    outcomes = []
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        lines = err.decode().strip().splitlines()
        outcomes.append((proc.returncode, json.loads(lines[-1]) if lines else None))
    return outcomes


@pytest.mark.parametrize(
    ("caps", "step_over", "detail"),
    [
        ({"max_jobs": 1, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 4}, {}, "jobs"),
        ({"max_jobs": 9, "max_gpu_hours": 1, "max_core_hours": 0, "max_concurrent": 4},
         {"gpus": 1, "time": 3600}, "gpu_hours"),
    ],
    ids=["last job", "last gpu seconds"],
)  # fmt: skip
def test_caps_under_concurrency_exactly_one_of_two_processes_passes(
    world: Any, caps: dict[str, int], step_over: dict[str, Any], detail: str
) -> None:
    first = new_run(world, grant={"caps": caps}, step_over=step_over)
    second = first.again(run_id=RUN_2, step_over=step_over)
    outcomes = _race(world, [first, second])
    codes = sorted(code for code, _err in outcomes)
    assert codes == [0, 4], outcomes
    errors = [err for code, err in outcomes if code == 4]
    assert errors == [{"error": "grant_exhausted", "detail": detail}]
    assert len(first.counters()["entries"]) == 1 and len(world.jobs()) == 1


# ---------------------------------------------------------------------------
# grant-check, grant-cancel, revoke and the epoch
# ---------------------------------------------------------------------------


def test_grant_check_reports_state_bounds_and_usage(world: Any) -> None:
    run = new_run(world)
    request = check_request(world, run.grant)
    out = ok(world.run("grant-check", request))
    assert not (world.root / "state" / "grants" / f"{run.gid}.json").exists()
    assert (out["grant_id"], out["state"], out["held_accounts"]) == (run.gid, "active", ["acct1"])
    assert out["grant_sha256"] == _sha(g.canonical_bytes(run.grant))
    assert out["bounds"]["workflows"] == [{"name": "demo", "version": "1.0.0"}]
    assert (out["bounds"]["inputs_mode"], out["bounds"]["listed_count"]) == ("any", 0)
    job = ok(run.submit(run.ready()))["job_id"]
    usage = ok(world.run("grant-check", request))["usage"]
    assert usage == {"jobs": 1, "open": 1, "gpu_seconds": 0, "core_seconds": 0,
                     "reserved_gpu_seconds": 0, "reserved_core_seconds": 1200}  # fmt: skip
    world.set_state(job, "COMPLETED")
    usage = ok(world.run("grant-check", request))["usage"]
    assert (usage["open"], usage["core_seconds"], usage["reserved_core_seconds"]) == (0, 120, 0)
    assert last_log(world)["detail"] == f"grant={run.gid} state=active"


@pytest.mark.parametrize(
    ("window", "state"), [((0, 400, 900), "not_yet_valid"), ((-900, -900, -200), "expired")]
)
def test_grant_check_reports_time_without_refusing(world: Any, window: Any, state: str) -> None:
    now = int(time.time())
    over = dict(zip(("issued", "not_before", "expires"), (now + t for t in window), strict=True))
    run = new_run(world, grant=over)
    assert ok(world.run("grant-check", check_request(world, run.grant)))["state"] == state


def test_grant_check_refusals(world: Any) -> None:
    run = new_run(world)
    refused(world.run("grant-check", check_request(world, run.grant, key=world.keys.other)),
            "bad_signature")  # fmt: skip
    refused(world.run("grant-check", b" " * (g.GRANT_CHECK_MAX_STDIN + 1)), "too_large")
    refused(
        world.run("grant-check", check_request(world, {**run.grant, "site": "s2"})), "wrong_site"
    )
    stranger = {**run.grant, "accounts": ["acct9"]}
    refused(world.run("grant-check", check_request(world, stranger)), "account_not_held")
    ok(run.submit(run.ready()))
    twin = {**run.grant, "nonce": secrets.token_hex(16)}
    refused(world.run("grant-check", check_request(world, twin)), "grant_conflict")
    assert world.run("grant-check extra", b"").code == 2


def test_grant_cancel_is_idempotent_and_ends_the_grant_but_not_its_jobs(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    out = ok(world.run("grant-cancel", control(world, "grant-cancel", grant_id=run.gid)))
    assert (out["ok"], out["op"], out["grant_id"], out["already"]) == (
        True,
        "grant-cancel",
        run.gid,
        False,
    )
    signer = g.fingerprint(g.ed25519_blob(world.keys.signer_b64))
    assert last_log(world)["detail"] == f"grant={run.gid} signer={signer}"
    again = ok(world.run("grant-cancel", control(world, "grant-cancel", grant_id=run.gid)))
    assert again["already"] is True
    second = run.again(run_id=RUN_2)
    result = second.submit(second.ready())
    refused(result, "grant_cancelled")
    assert result.err["detail"] == "cancelled"
    assert world.jobs()[job]["state"] == "PENDING" and not world.calls("scancel")
    request = check_request(world, run.grant)
    assert ok(world.run("grant-check", request))["state"] == "cancelled"


def test_grant_cancel_control_schema(world: Any) -> None:
    refused(world.run("grant-cancel", control(world, "grant-cancel", grant_id="x")), "bad_schema")
    with_key = control(world, "grant-cancel", grant_id="ab" * 16, key_id="tk-aaaaaaaaaaaa")
    refused(world.run("grant-cancel", with_key), "bad_schema")
    refused(world.run("grant-cancel", control(world, "revoke", grant_id="ab" * 16)), "bad_schema")
    body = control(world, "grant-cancel", grant_id="ab" * 16)
    ok(world.run("grant-cancel", body))
    refused(world.run("grant-cancel", body), "replayed")


def _transport_line(world: Any, tmp: Path) -> str:
    b64 = gx._keygen(tmp / "transport")
    ssh = world.home / ".ssh"
    ssh.mkdir(mode=0o700, exist_ok=True)
    line = g.authorized_keys_line(str(world.home), SITE, "tk-aaaaaaaaaaaa", b64)
    (ssh / "authorized_keys").write_text(line + "\n")
    (ssh / "authorized_keys").chmod(0o600)
    return line


def test_k7_a_revoke_writes_the_epoch_and_ends_earlier_grants(world: Any, tmp_path: Path) -> None:
    run = new_run(world)
    _transport_line(world, tmp_path)
    later = int(time.time()) + 60
    ok(world.run("revoke", control(world, "revoke", key_id="tk-aaaaaaaaaaaa", issued=later,
                                   expires=later + 300)))  # fmt: skip
    assert json.loads((world.root / "state" / "grants" / "epoch").read_text()) == {"epoch": later}
    result = run.submit(run.ready())
    refused(result, "grant_cancelled")
    assert result.err["detail"] == "account re-enrolled"
    assert ok(world.run("grant-check", check_request(world, run.grant)))["state"] == "cancelled"
    fresh = new_run(world, grant={"issued": later, "not_before": later, "expires": later + 900},
                    run_id=RUN_2)  # fmt: skip
    ok(fresh.submit(fresh.ready()))


def test_revoke_advances_an_existing_higher_epoch(world: Any, tmp_path: Path) -> None:
    """A signed revoke is a new boundary even when its timestamp is older."""
    now = int(time.time())
    cutoff = now + 60
    g.write_epoch(str(world.root), cutoff)
    _transport_line(world, tmp_path)
    ok(world.run("revoke", control(world, "revoke", key_id="tk-aaaaaaaaaaaa", issued=now,
                                   expires=now + 300)))  # fmt: skip
    assert g.read_epoch(str(world.root)) == cutoff + 1
    old = new_run(world, grant={"issued": cutoff, "not_before": cutoff, "expires": cutoff + 900})
    result = old.submit(old.ready())
    refused(result, "grant_cancelled")
    assert result.err["detail"] == "account re-enrolled"


def test_a_missing_epoch_after_bootstrap_fails_closed(world: Any) -> None:
    """Losing the file must not reset revocation and recreate fresh counters."""
    run = new_run(world)
    g.write_epoch(str(world.root), run.grant["issued"] + 1)
    (world.root / "state" / "grants" / "epoch").unlink()
    result = run.submit(run.ready())
    assert result.code == 1
    assert not world.jobs()


def test_write_epoch_bootstraps_explicit_zero_for_a_legacy_install(world: Any) -> None:
    """Explicit zero keeps old active grants valid without meaning missing."""
    epoch = world.root / "state" / "grants" / "epoch"
    epoch.unlink()
    g.write_epoch(str(world.root), 0)
    assert epoch.read_bytes() == b'{"epoch":0}'
    first = new_run(world)
    assert g._grant_ended(str(world.root), first.grant) == ""


def test_the_epoch_never_goes_back_and_a_bad_epoch_fails_closed(world: Any) -> None:
    root = str(world.root)
    g.write_epoch(root, 500)
    g.write_epoch(root, 100)
    assert g.read_epoch(root) == 500
    (world.root / "state" / "grants" / "epoch").write_text('{"epoch": "x"}')
    run = new_run(world)
    assert run.submit(run.ready()).code == 1


# ---------------------------------------------------------------------------
# log failure, sweep, usage rows
# ---------------------------------------------------------------------------


def test_an_unlogged_grant_job_is_cancelled_and_settles_at_zero(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = new_run(world)
    wd = run.ready()

    def broken(self: Any, verb: str, result: str, detail: str, now: int) -> None:
        if verb == "submit" and result == "ok":
            raise OSError("disk full")

    monkeypatch.setattr(g.GateLog, "record", broken)
    refused(run.submit(wd), "log_unwritable")
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    assert (entry["state"], entry["charged"]) == ("settled", [0, 0])
    assert world.calls("scancel")


def test_sweep_prunes_ended_grants_and_keeps_cancels_400_days(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    grants = world.root / "state" / "grants"
    counters = run.counters()
    counters["expires"] = _NOW - 8 * 86400
    (grants / f"{run.gid}.json").write_bytes(g.canonical_bytes(counters))
    ok(world.run("sweep"))
    assert (grants / f"{run.gid}.json").exists(), "an open entry keeps the counters"
    world.set_state(job, "COMPLETED")
    ok(world.run("sweep"))
    assert not (grants / f"{run.gid}.json").exists() and not (grants / f"{run.gid}.lock").exists()
    for gid, age, kept in (("a" * 32, 31, True), ("b" * 32, 401, False)):
        (grants / f"{gid}.cancelled").write_text(json.dumps({"cancelled": _NOW - age * 86400}))
        ok(world.run("sweep"))
        assert (grants / f"{gid}.cancelled").exists() is kept


def test_usage_rows_name_their_grant(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    plain, wd = gx._one_step(world)
    intent_job = ok(world.submit(plain, wd, "a"))["job_id"]
    rows = ok(world.run(f"usage {job} {intent_job}"))["jobs"]
    fields = {row["job_id"]: (row["grant_id"], row["settled"]) for row in rows}
    assert fields == {job: (run.gid, False), intent_job: ("", False)}


def test_settlement_appends_a_fixed_form_charge_receipt(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    ok(world.run(f"usage {job}"))
    entry = run.counters()["entries"][f"{RUN_1}/a"]
    gpu, core = entry["charged"]
    detail = (
        f"grant={run.gid} run={RUN_1} step=a job={job} gpu_s={gpu} core_s={core} "
        f"bound={int(entry['bound'])}"
    )
    settles = [json.loads(line) for line in world.log.read_text().splitlines()]
    matches = [row for row in settles if row["verb"] == "settle" and row["result"] == "ok"]
    assert [row["detail"] for row in matches] == [detail]


def test_a_bound_settlement_receipt_marks_the_bound_flag(world: Any) -> None:
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    world.behave(sacct_omit=[job])
    age_entries(run, 10_000)
    ok(world.run("sweep"))
    rows = [json.loads(line) for line in world.log.read_text().splitlines()]
    settle = next(row for row in reversed(rows) if row["verb"] == "settle")
    assert settle["detail"].endswith(" bound=1")


def _settle_receipts(world: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in (json.loads(line) for line in world.log.read_text().splitlines())
        if row["verb"] == "settle"
    ]


def test_settlement_log_and_counter_failures_retry_without_loss_or_duplicates(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log-first recovery: neither failure order may strand or duplicate evidence."""
    run = new_run(world)
    job = ok(run.submit(run.ready()))["job_id"]
    world.set_state(job, "COMPLETED")
    entry_key = f"{RUN_1}/a"
    real_append, real_save = g._append_unsigned, g._save_counters

    def log_fails(*_args: Any) -> None:
        raise g.refuse("log_unwritable", "injected log failure")

    monkeypatch.setattr(g, "_append_unsigned", log_fails)
    ok(world.run(f"usage {job}"))
    assert run.counters()["entries"][entry_key]["state"] == "live"

    monkeypatch.setattr(g, "_append_unsigned", real_append)

    def save_fails(*_args: Any) -> None:
        raise OSError("injected counter failure")

    monkeypatch.setattr(g, "_save_counters", save_fails)
    ok(world.run(f"usage {job}"))
    assert run.counters()["entries"][entry_key]["state"] == "live"
    assert len(_settle_receipts(world)) == 1

    monkeypatch.setattr(g, "_save_counters", real_save)
    ok(world.run(f"usage {job}"))
    assert run.counters()["entries"][entry_key]["state"] == "settled"
    assert len(_settle_receipts(world)) == 1


def test_k19a_a_step_whose_walltime_runs_past_the_grants_expiry_is_refused(world: Any) -> None:
    now = int(time.time())
    window = {"issued": now, "not_before": now, "expires": now + 90}
    run = new_run(world, grant=window, step_over={"time": 3600})
    result = run.submit(run.ready())
    refused(result, "grant_expired")
    assert "runs past the grant's expiry" in result.err["detail"]
    assert not (world.root / "state" / "grants" / f"{run.gid}.json").exists()


def test_k19a_a_grant_jobs_deadline_never_outlives_the_grant(world: Any) -> None:
    now = int(time.time())
    window = {"issued": now, "not_before": now, "expires": now + 700}
    run = new_run(world, grant=window, step_over={"time": 600})
    wd = run.ready()
    ok(run.submit(wd))
    script = (world.work / wd / "job.sbatch").read_text()
    assert f"#SBATCH --deadline={g._slurm_time(window['expires'])}" in script
    assert "#SBATCH --deadline=now+" not in script
    plain = g.render_job_script(run.spec, "a", world.profile, "/tmp/wd", LOGIN, run_key=run.key)
    assert int(plain.split("--deadline=now+", 1)[1].split()[0]) > 700


def test_render_job_script_takes_a_run_key(world: Any) -> None:
    run = new_run(world)
    script = g.render_job_script(run.spec, "a", world.profile, "/tmp/wd", LOGIN, run_key=run.key)
    assert f"umrs-gated-{run.key[:16]}-a" in script
    with pytest.raises(ValueError):
        g.render_job_script(run.spec, "a", world.profile, "/tmp/wd", LOGIN, run_key="x")


# ---------------------------------------------------------------------------
# K1: sizes the gate takes
# ---------------------------------------------------------------------------


def _padded_schema(count: int) -> dict[str, Any]:
    props = {f"p{i:03d}": {"type": "string", "maxLength": g.PARAM_MAX_LENGTH} for i in range(count)}
    pad = [f"{i:05d}" + "e" * 29995 for i in range(64)]
    props["pad"] = {"type": "string", "maxLength": 30000, "enum": pad}
    return {"type": "object", "additionalProperties": False, "required": [], "properties": props}


def test_k1_a_maximal_grant_envelope_is_accepted(world: Any) -> None:
    schema = _padded_schema(40)
    params = {"project_id": "proj-1", **{name: "v" * g.PARAM_MAX_LENGTH for name in schema["properties"]
                                         if name != "pad"}}  # fmt: skip
    run = new_run(world, params=params)
    run.grant["param_schema"] = schema
    run.grant["workflows"][0]["params"] = sorted(schema["properties"])
    envelope = run.envelope()
    assert len(envelope) > 12 << 20 and len(g.canonical_bytes(run.spec, True)) <= g.INTENT_MAX_BYTES
    ok(world.run(f"submit {run.ready()} a", envelope))


def test_k1_a_two_mebibyte_grant_passes_grant_check(world: Any) -> None:
    run = new_run(world)
    pad = [f"{j:05d}" + "e" * 14995 for j in range(64)]
    listed = [{"sha256": f"{i:064x}", "size": i} for i in range(g.MAX_LISTED_INPUTS)]
    run.grant["param_schema"]["properties"]["pad"] = {
        "type": "string",
        "maxLength": 15000,
        "enum": pad,
    }
    run.grant["inputs"] = {"mode": "chained", "listed": listed}
    size = len(g.canonical_bytes(run.grant))
    assert (
        1 << 20 < size <= g.GRANT_MAX_BYTES and len(check_request(world, run.grant)) > g.MAX_STDIN
    )
    assert ok(world.run("grant-check", check_request(world, run.grant)))["bounds"][
        "listed_count"
    ] == (g.MAX_LISTED_INPUTS)


# ---------------------------------------------------------------------------
# end to end as sshd runs it (python3.9 too)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interp", gx.INTERPRETERS, ids=lambda p: Path(p).name)
def test_grant_flow_end_to_end(world: Any, interp: str) -> None:
    run = new_run(world)
    wd = run.ready()
    submitted = world.run_sub(interp, f"submit {wd} a", run.envelope())
    assert submitted.code == 0, submitted
    job = submitted.out["job_id"]
    checked = world.run_sub(interp, "grant-check", check_request(world, run.grant))
    assert (checked.code, checked.out["usage"]["open"]) == (0, 1), checked
    world.set_state(job, "COMPLETED")
    usage = world.run_sub(interp, f"usage {job}")
    assert usage.out["jobs"][0]["settled"] is True, usage
    cancel = world.run_sub(interp, "grant-cancel", control(world, "grant-cancel", grant_id=run.gid))
    assert (cancel.code, cancel.out["already"]) == (0, False), cancel
    hello = world.run_sub(interp, "hello")
    assert hello.out["checkpoint"].startswith(f"umrs-gate/{SITE}/{LOGIN}\n"), hello
    assert g.verify_log(str(world.log))
