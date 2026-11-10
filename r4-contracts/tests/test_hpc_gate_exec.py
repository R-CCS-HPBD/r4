"""ADR-112 wave B2: gated execution through the UMRS gate (r4_contracts.hpc_gate).

Contract: gated-delegation plan section 5, "B2.0 Wire contract". The gate runs
in-process (``dispatch``) for the refusal matrix and as sshd runs it
(``python -I <gate> --site s1`` with ``SSH_ORIGINAL_COMMAND``, also under
python3.9 when installed) for the end-to-end runs. Slurm is a set of fake
``sbatch`` / ``squeue`` / ``sacct`` / ``scancel`` / ``sacctmgr`` scripts in a
tmp directory (the pattern of ``umrs/tests/step_lane_harness.py``); podman is a
fake that loads real docker archives built here. Signatures are real
``ssh-keygen -Y sign`` signatures from keys generated into tmp at test time.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from r4_contracts import hpc_gate as g

KEYGEN = "/usr/bin/ssh-keygen"
GATE = Path(g.__file__).resolve()
SITE = "s1"
DOMAIN = "umrs.example.org"
PRINCIPAL = "alice"
RUN_ID = "wf-0123456789ab"
IN_BUCKET = "wf-runner"
OUT_BUCKET = "wf-runner-output"
INTENT_NS = f"umrs-intent-v1@{DOMAIN}"
LOGIN = pwd.getpwuid(os.geteuid()).pw_name
ARCH = platform.machine() if platform.machine() in ("x86_64", "aarch64") else "x86_64"
LABELS = {"org.umrs.step-contract": "1", "org.umrs.step-payload": "pickle,json"}
ENTRY = "pkg.step:Step"
SRC = {"pkg/__init__.py": b"", "pkg/step.py": b"class Step:\n    pass\n"}

pytestmark = pytest.mark.skipif(not os.path.exists(KEYGEN), reason="needs /usr/bin/ssh-keygen")

_PY39 = shutil.which("python3.9")
INTERPRETERS = [sys.executable] + ([_PY39] if _PY39 else [])


# ---------------------------------------------------------------------------
# fake Slurm (state in a JSON file; the gate gives it a clean environment)
# ---------------------------------------------------------------------------

FAKE_SLURM = r"""#!__PYTHON__
import fcntl, json, os, sys, time

STATE = __STATE__
LIVE = {"PENDING", "RUNNING", "COMPLETING", "CONFIGURING", "SUSPENDED"}
USAGE = "{}|{}|00:01:00|cpu=2,mem=1G,gres/gpu=1|2|acct1|gpu|2026-11-08T10:00:00|2026-11-08T10:01:00"


def load(name, default):
    path = os.path.join(STATE, name)
    return json.load(open(path)) if os.path.exists(path) else default


def opt(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def log(tool, args):
    entry = {"tool": tool, "args": args, "env": dict(os.environ), "cwd": os.getcwd()}
    with open(os.path.join(STATE, "calls.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def jobs():
    return load("jobs.json", {"next": 1000, "jobs": {}})["jobs"]


def queue(workdir, script):
    with open(os.path.join(STATE, "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load("jobs.json", {"next": 1000, "jobs": {}})
        job = str(data["next"])
        data["next"] += 1
        data["jobs"][job] = {"workdir": workdir, "state": "PENDING", "script": script}
        json.dump(data, open(os.path.join(STATE, "jobs.json"), "w"))
    return job


def sbatch(args, beh):
    time.sleep(beh.get("sbatch_sleep", 0))
    rc = beh.get("sbatch_rc", 0)
    chdir = [a[len("--chdir="):] for a in args if a.startswith("--chdir=")]
    job = None
    if rc == 0 or beh.get("queue_anyway"):
        job = queue(chdir[0] if chdir else os.getcwd(), open(args[-1]).read())
    if rc:
        sys.stderr.write("sbatch: error: Socket timed out on send/recv operation\n")
        sys.exit(rc)
    print(f"{job};fakecluster")


def squeue(args, beh):
    if beh.get("squeue_rc"):
        sys.exit(beh["squeue_rc"])
    live = {k: v for k, v in jobs().items() if v["state"] in LIVE}
    if "-j" in args:
        job = live.get(opt(args, "-j"))
        if job is None:
            sys.stderr.write("slurm_load_jobs error: Invalid job id specified\n")
            sys.exit(1)
        print(job["state"])
    elif opt(args, "-o") == "%i %Z":
        for k, v in live.items():
            print(f"{k} {v['workdir']}")
    else:
        for k in live:
            print(k)


def sacct(args, beh):
    if beh.get("sacct_rc"):
        sys.exit(beh["sacct_rc"])
    known = jobs()
    fmt = opt(args, "-o")
    ids = opt(args, "-j").split(",") if "-j" in args else list(known)
    for k in ids:
        if k not in known:
            continue
        state, wd = known[k]["state"], known[k]["workdir"]
        row = {"State": state, "JobID,WorkDir": f"{k}|{wd}", "JobID,State": f"{k}|{state}"}
        print(row.get(fmt, USAGE.format(k, state)))


def scancel(args, beh):
    with open(os.path.join(STATE, "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load("jobs.json", {"next": 1000, "jobs": {}})
        data["jobs"][args[-1]]["state"] = "CANCELLED"
        json.dump(data, open(os.path.join(STATE, "jobs.json"), "w"))


def sacctmgr(args, beh):
    if beh.get("sacctmgr_rc"):
        sys.stderr.write("sacctmgr: error: slurmdbd down\n")
        sys.exit(beh["sacctmgr_rc"])
    sys.stdout.write(beh.get("assocs", "c1|acct1|gpu|normal\nc1|acct2||\n"))


tool = os.path.basename(sys.argv[0])
log(tool, sys.argv[1:])
{"sbatch": sbatch, "squeue": squeue, "sacct": sacct, "scancel": scancel, "sacctmgr": sacctmgr}[tool](
    sys.argv[1:], load("behaviour.json", {})
)
"""
SLURM_TOOLS = ("sbatch", "squeue", "sacct", "scancel", "sacctmgr")


def _install_fake_slurm(bindir: Path, state: Path) -> None:
    text = FAKE_SLURM.replace("__PYTHON__", sys.executable).replace("__STATE__", repr(str(state)))
    for tool in SLURM_TOOLS:
        path = bindir / tool
        path.write_text(text)
        path.chmod(0o755)


# ---------------------------------------------------------------------------
# keys, archives, tars
# ---------------------------------------------------------------------------


def _keygen(path: Path) -> str:
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", path.name, "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return (path.parent / (path.name + ".pub")).read_text().split()[1]


def _sign(key: Path, ns: str, payload: bytes) -> str:
    proc = subprocess.run(
        [KEYGEN, "-Y", "sign", "-f", str(key), "-n", ns],
        input=payload,
        check=True,
        capture_output=True,
    )
    return proc.stdout.decode("ascii")


@dataclass(frozen=True)
class Keys:
    signer: Path
    signer_b64: str
    other: Path


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    base = tmp_path_factory.mktemp("keys")
    signer_b64 = _keygen(base / "signer")
    _keygen(base / "other")
    return Keys(signer=base / "signer", signer_b64=signer_b64, other=base / "other")


def _tar(members: list[tuple[str, bytes]], fmt: int = tarfile.PAX_FORMAT) -> bytes:
    sink = io.BytesIO()
    with tarfile.open(fileobj=sink, mode="w", format=fmt) as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return sink.getvalue()


def docker_archive(labels: dict[str, str], salt: str = "") -> tuple[bytes, str]:
    """A docker-archive tar (manifest.json + config); returns (bytes, image_id)."""
    config = json.dumps(
        {"architecture": "amd64", "config": {"Labels": labels}, "salt": salt}
    ).encode()
    image_id = hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        [{"Config": f"{image_id}.json", "RepoTags": ["localhost/x:y"], "Layers": []}]
    )
    return _tar([("manifest.json", manifest.encode()), (f"{image_id}.json", config)]), image_id


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def obj(key: str, data: bytes, bucket: str = IN_BUCKET, mode: str = "listed") -> dict[str, Any]:
    return {"mode": mode, "bucket": bucket, "key": key, "sha256": _sha(data), "size": len(data)}


# ---------------------------------------------------------------------------
# the world: a home with an installed gate, a fake cluster, archives
# ---------------------------------------------------------------------------


@dataclass
class Result:
    code: int
    out: Any
    err: Any
    raw: bytes = b""


@dataclass
class World:
    tmp: Path
    home: Path
    bin: Path
    slurm: Path
    archives: Path
    keys: Keys
    image: dict[str, Any] = field(default_factory=dict)
    trusted: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.home / ".umrs" / "gate"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def log(self) -> Path:
        return self.root / "log" / "gate.jsonl"

    @property
    def profile_sha(self) -> str:
        return _sha(g.site_profile_bytes(self.profile))

    def write_profile(self, **over: Any) -> None:
        self.profile.update(over)
        (self.root / "site.json").write_bytes(g.site_profile_bytes(self.profile))

    def add_image(self, labels: dict[str, str] | None = None, salt: str = "") -> dict[str, Any]:
        data, image_id = docker_archive(LABELS if labels is None else labels, salt)
        (self.archives / f"{_sha(data)}.tar").write_bytes(data)
        return {
            "ref": "localhost/umrs-step:abc",
            "image_id": image_id,
            "archive_sha256": _sha(data),
        }

    def behave(self, **beh: Any) -> None:
        (self.slurm / "behaviour.json").write_text(json.dumps(beh))

    def jobs(self) -> dict[str, Any]:
        path = self.slurm / "jobs.json"
        return json.loads(path.read_text())["jobs"] if path.exists() else {}

    def set_state(self, job_id: str, state: str) -> None:
        data = json.loads((self.slurm / "jobs.json").read_text())
        data["jobs"][job_id]["state"] = state
        (self.slurm / "jobs.json").write_text(json.dumps(data))

    def calls(self, tool: str) -> list[dict[str, Any]]:
        path = self.slurm / "calls.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [c for c in map(json.loads, lines) if c["tool"] == tool]

    # -- running the gate ------------------------------------------------

    def run(self, command: str, stdin: bytes = b"", deadline: float | None = None) -> Result:
        sink = io.BytesIO()
        try:
            out = g.dispatch(["--site", SITE], command, io.BytesIO(stdin), sink, deadline)
        except g.GateError as err:
            return Result(err.code, None, err.as_json(), sink.getvalue())
        if isinstance(out, g._Streamed):
            return Result(0, out.summary, None, sink.getvalue())
        if isinstance(out, bytes):
            return Result(0, None, None, out)
        return Result(0, out, None)

    def run_sub(self, interp: str, command: str, stdin: bytes = b"") -> Result:
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": command}
        proc = subprocess.run(
            [interp, "-I", str(GATE), "--site", SITE],
            input=stdin,
            capture_output=True,
            env=env,
            timeout=120,
        )
        lines = proc.stderr.decode().strip().splitlines()
        err = json.loads(lines[-1]) if lines else None
        out = None
        if proc.stdout.strip().startswith(b"{") and command.split()[0] != "read-reply":
            out = json.loads(proc.stdout)
        return Result(proc.returncode, out, err, proc.stdout)

    # -- intents -----------------------------------------------------------

    def step(self, name: str, *, after: tuple[str, ...] = (), chained: tuple[str, ...] = (),
             objects: tuple[dict[str, Any], ...] = (), **over: Any) -> dict[str, Any]:  # fmt: skip
        step = {
            "name": name,
            "after": sorted(after),
            "config": {"threshold": 0.25, "label": None, "step_name": name},
            "image": self.image,
            "partition": "gpu",
            "cpus": 2,
            "mem": "",
            "gpus": 0,
            "time": 600,
            "inputs": {
                "objects": sorted(objects, key=lambda o: (o["bucket"], o["key"])),
                "prefixes": [],
                "chained": list(chained),
            },  # fmt: skip
        }
        step.update(over)
        return step

    def intent(
        self, steps: list[dict[str, Any]], src: dict[str, bytes] | None = None, **over: Any
    ) -> dict[str, Any]:
        now = int(time.time())
        files = [g.src_file_entry(path, data) for path, data in sorted((src or SRC).items())]
        body = {
            "v": 1, "type": "intent", "site": SITE, "principal": PRINCIPAL, "login": LOGIN,
            "account": "acct1", "project": "proj-1", "workflow": "demo", "version": "1.0.0",
            "source_sha256": "a" * 64, "run_id": RUN_ID, "input_bucket": IN_BUCKET,
            "output_bucket": OUT_BUCKET, "params": {"project_id": "proj-1", "alpha": 0.5, "beta": None},
            "site_profile_sha256": self.profile_sha, "trusted_image": self.trusted,
            "src_manifest": {"files": files, "entrypoints": {s["name"]: ENTRY for s in steps}},
            "steps": steps, "nonce": secrets.token_hex(16), "issued": now, "expires": now + 3600,
            "max_run_seconds": 7200,
        }  # fmt: skip
        body.update(over)
        return body

    def envelope(self, body: Any, *, key: Path | None = None, ns: str = INTENT_NS,
                 payload: bytes | None = None, **extra: Any) -> bytes:  # fmt: skip
        data = g.canonical_bytes(body, True) if payload is None else payload
        sig = _sign(key or self.keys.signer, ns, data)
        envelope = {"payload": base64.b64encode(data).decode(), "signature": sig, **extra}
        return json.dumps(envelope).encode()

    # -- staging ------------------------------------------------------------

    def mkwd(self, body: dict[str, Any], step: str) -> str:
        iid = g.intent_id(g.canonical_bytes(body, True))
        return ok(self.run(f"mkwd {iid} {step}"))["wd"]

    def stage(
        self, wd: str, src: dict[str, bytes] | None = None, blobs: tuple[bytes, ...] = ()
    ) -> Result:
        members = [(f"src/{p}", d) for p, d in sorted((SRC if src is None else src).items())]
        members += [(f"in/blobs/{_sha(b)}", b) for b in blobs]
        return self.run(f"stage-in {wd}", _tar(members))

    def ready(
        self,
        body: dict[str, Any],
        step: str,
        blobs: tuple[bytes, ...] = (),
        src: dict[str, bytes] | None = None,
    ) -> str:
        wd = self.mkwd(body, step)
        ok(self.stage(wd, src, blobs))
        return wd

    def submit(self, body: dict[str, Any], wd: str, step: str, **kw: Any) -> Result:
        return self.run(f"submit {wd} {step}", self.envelope(body, **kw))

    def finish(
        self, wd: str, job_id: str, reply: dict[str, Any], outs: dict[str, bytes] | None = None
    ) -> None:
        """What the job would leave behind: a reply, outputs, COMPLETED."""
        base = self.work / wd
        (base / "reply" / "output.json").write_text(json.dumps(reply))
        for rel, data in (outs or {}).items():
            path = base / "out" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.set_state(job_id, "COMPLETED")


def ok(result: Result) -> Any:
    assert result.code == 0, result
    return result.out


def refused(result: Result, name: str) -> None:
    assert (result.code, result.err["error"]) == (4, name), result


def _profile(world: World) -> dict[str, Any]:
    big = 1 << 30
    return {
        "v": 1, "site": SITE, "domain": DOMAIN,
        "slurm": {"path": str(world.bin), "owner_uids": [os.geteuid()], "queue_seconds": 600, "grace_seconds": 300},
        "partitions": {
            "gpu": {"arch": ARCH, "max_cpus": 16, "max_gpus": 4, "max_time": 86400, "mem": True,
                    "gpu_request": "gres", "gres": "", "gpu_flag": "gres", "exclusive": False},
            "cpu": {"arch": ARCH, "max_cpus": 4, "max_gpus": 0, "max_time": 3600, "mem": False,
                    "gpu_request": "none", "gres": "", "gpu_flag": "gres", "exclusive": True},
        },
        "gpu": {"enabled": True, "form": "cdi"},
        "podman": {"pids_limit": 512, "cgroups": "enabled", "nproc_limit": 0},
        "venue": {"root": str(world.tmp / "venue"), "storage_root": "", "network_config_dir": "", "network_backend": ""},
        "image_archive_dir": str(world.archives),
        "caps": {"max_object_bytes": big, "max_in_bytes": big, "max_in_objects": 100, "max_src_bytes": 1 << 20,
                 "max_src_files": 100, "max_out_files": 100, "max_out_bytes": big, "max_out_file_bytes": big,
                 "min_free_bytes": 0, "reply_bytes": 1 << 20, "log_tail_bytes": 4096, "max_workdirs": 8},
        "endpoint": {"max_request_bytes": 1 << 20, "max_response_bytes": 1 << 20, "max_total_bytes": 1 << 24},
        "watch_seconds": 15,
    }  # fmt: skip


def _make_world(tmp_path: Path, keys: Keys) -> World:
    home, bindir, slurm, archives = (tmp_path / n for n in ("home", "bin", "slurm", "archives"))
    root = home / ".umrs" / "gate"
    root.mkdir(parents=True, mode=0o700)
    for directory in (bindir, slurm, archives):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
    _install_fake_slurm(bindir, slurm)
    world = World(tmp_path, home, bindir, slurm, archives, keys)
    cfg = {"site": SITE, "domain": DOMAIN, "principal": PRINCIPAL, "uv_required": False,
           "allow_non_sk": True, "gate_version": "2"}  # fmt: skip
    (root / "gate.json").write_text(json.dumps(cfg))
    line = g.allowed_signers_line(PRINCIPAL, DOMAIN, "ssh-ed25519", keys.signer_b64)
    (root / "allowed_signers").write_text(line + "\n")
    world.image = world.add_image()
    world.trusted = world.add_image(salt="trusted")
    world.profile = _profile(world)
    world.write_profile()
    return world


@pytest.fixture
def world(tmp_path: Path, keys: Keys, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return _make_world(tmp_path, keys)


def _one_step(world: World, **over: Any) -> tuple[dict[str, Any], str]:
    """A one-step intent with one listed input, staged and ready to submit."""
    data = b"input bytes"
    body = world.intent([world.step("a", objects=(obj("data/x.bin", data),), **over)])
    return body, world.ready(body, "a", (data,))


def _submitted(world: World) -> tuple[dict[str, Any], str, str]:
    body, wd = _one_step(world)
    return body, wd, ok(world.submit(body, wd, "a"))["job_id"]


@contextlib.contextmanager
def held(path: Path) -> Any:
    """Hold an flock on ``path`` (as another gate session would)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        yield


def _iid(body: dict[str, Any]) -> str:
    return g.intent_id(g.canonical_bytes(body, True))


def _nonce(world: World, body: dict[str, Any], step: str) -> Path:
    return world.root / "state" / "intents" / _iid(body) / f"{step}.json"


# ---------------------------------------------------------------------------
# shared API: constants, canonical JSON values, intent schema
# ---------------------------------------------------------------------------


B2_REFUSALS = [
    "sealed", "broken", "busy", "wrong_step", "wrong_login", "wrong_intent", "profile_mismatch",
    "profile_unsafe", "resource_not_allowed", "account_not_held", "parent_not_collected",
    "src_mismatch", "input_mismatch", "image_absent", "image_mismatch", "image_label",
    "bad_member", "digest_mismatch", "too_many_workdirs", "low_space", "not_mine",
    "not_finished", "job_live", "bad_reply", "log_full",
]  # fmt: skip


def test_shared_constants_and_refusal_names() -> None:
    limits = (g.INTENT_MAX_BYTES, g.INTENT_MAX_WINDOW, g.RUN_MAX_SECONDS, g.INPUT_JSON_MAX_BYTES)
    assert limits + (g.REPLY_CAP_BYTES,) == (8 << 20, 3600, 604800, 48 << 20, 4 << 20)
    names = (g.STEP_RE.fullmatch("step_1-a"), g.STEP_RE.fullmatch("-a"))
    assert (bool(names[0]), names[1]) == (True, None)
    assert g.WD_RE.fullmatch("0123456789abcdef-step_1-0a1b2c3d")
    assert set(B2_REFUSALS) <= set(g.REFUSALS)
    assert len(set(g.REFUSALS)) == len(g.REFUSALS)
    long_verbs = dict.fromkeys(("stage-in", "collect-out"), 7200)
    expected = {"submit": 600, **long_verbs, **dict.fromkeys(("rmwd", "sweep"), 1800)}
    assert {**expected, "grant-check": 180} == g.DEADLINES


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        ({"a": 0.5, "b": None}, b'{"a":0.5,"b":null}'),
        ({"x": [1.0, -0.0, 1e-07, 1e16]}, b'{"x":[1.0,-0.0,1e-07,1e+16]}'),
    ],
)
def test_json_values_round_trip(value: Any, raw: bytes) -> None:
    assert g.canonical_bytes(value, json_values=True) == raw
    assert g.parse_strict(raw, json_values=True) == value
    with pytest.raises(ValueError):
        g.canonical_bytes(value)
    with pytest.raises(ValueError):
        g.parse_strict(raw)


@pytest.mark.parametrize(
    "raw", [b'{"a":1E5}', b'{"a":0.50}', b'{"a":1e400}', b'{"a":NaN}', b'{"a":1.}']
)
def test_json_values_refuse_non_canonical_floats(raw: bytes) -> None:
    with pytest.raises(ValueError):
        g.parse_strict(raw, json_values=True)


def test_validate_intent_accepts_a_good_body(world: World) -> None:
    body = world.intent([world.step("a"), world.step("b", after=("a",), chained=("a",))])
    assert g.validate_intent(body) is body
    payload = g.canonical_bytes(body, True)
    assert g.validate_intent(g.parse_strict(payload, json_values=True)) == body


def _mutate(body: Any, path: str, value: Any) -> None:
    *parents, last = path.split(".")
    target: Any = body
    for part in parents:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key: Any = int(last) if isinstance(target, list) else last
    if value is KeyError:
        del target[key]
    else:
        target[key] = value


BAD_INTENTS = [
    ("extra", "<unknown>"),
    ("v", True),
    ("issued", KeyError),
    ("steps.0.cpus", 2.0),
    ("steps.0.cpus", True),
    ("steps.0.time", 59),
    ("steps.0.image.ref", None),
    ("trusted_image.image_id", "A" * 64),
    ("expires", "<+3601>"),
    ("expires", "<+0>"),
    ("steps.0.name", "-bad"),
    ("steps.0.name", "intent"),
    ("steps.0.mem", "0G"),
    ("steps.0.partition", "a b"),
    ("steps.0.endpoint", {"host": "h", "port": 0, "kind": "vllm"}),
    ("steps.0.endpoint", {"host": "h", "port": 80, "kind": "other"}),
    ("steps.0.inputs.prefixes", ["wf-runner/a//"]),
    ("steps.0.inputs.prefixes", ["wf-runner/b/", "wf-runner/a/"]),
    ("steps.0.inputs.objects", "<dup>"),
    ("steps.0.inputs.objects", "<unsorted>"),
    ("steps.0.inputs.objects.0.key", "a//b"),
    ("steps.0.inputs.objects.0.key", "./a"),
    ("steps.0.inputs.objects.0.key", "a/../b"),
    ("steps.0.inputs.objects.0.key", "/a"),
    ("steps.0.inputs.objects.0.bucket", "Bad_Bucket"),
    ("steps.0.inputs.objects.0.mode", "chained"),
    ("run_id", "wf-xyz"),
    ("project", "a\nb"),
    ("params", {"x": float("nan")}),
    ("src_manifest.entrypoints.a", "pkg..step:Step"),
    ("src_manifest.entrypoints.a", "pkg.step.:Step"),
    ("src_manifest.entrypoints", {}),
    ("src_manifest.files", "<unsorted>"),
    ("src_manifest.files", "<dir_prefix>"),
    ("src_manifest.files", "<huge>"),
    ("steps.1.after", ["b"]),
    ("steps.1.after", ["zz"]),
    ("steps.1.inputs.chained", ["a", "a"]),
    ("steps.0.inputs.chained", ["b"]),
    ("steps.2.after", ["b"]),
]


def _special(body: dict[str, Any], path: str, value: Any) -> Any:
    files = body["src_manifest"]["files"]
    two = [obj("k1", b"1"), obj("k2", b"2")]
    table = {
        "<unknown>": 1,
        "<dup>": [two[0], two[0]],
        "<unsorted>": list(reversed(two if path.endswith("objects") else files)),
        "<dir_prefix>": [g.src_file_entry("a", b"x"), g.src_file_entry("a/b", b"y")],
        "<huge>": [{**files[0], "size": g.MAX_EXPORT_BYTES + 1}],
        "<+3601>": body["issued"] + 3601,
        "<+0>": body["issued"],
    }
    return table.get(value, value) if isinstance(value, str) else value


@pytest.mark.parametrize(
    ("path", "value"), BAD_INTENTS, ids=[f"{p}={v!r}"[:50] for p, v in BAD_INTENTS]
)
def test_validate_intent_refuses(world: World, path: str, value: Any) -> None:
    steps = [
        world.step("a", objects=(obj("k1", b"1"),)),
        world.step("b", after=("a",), chained=("a",)),
        world.step("c", after=("a", "b")),
    ]
    body = world.intent(steps, src={"pkg/a.py": b"1", "pkg/b.py": b"2"})
    _mutate(body, path, _special(body, path, value))
    with pytest.raises(g.ContractError) as caught:
        g.validate_intent(body)
    assert (caught.value.error, caught.value.code) == ("bad_schema", g.EXIT_REFUSED)
    assert isinstance(caught.value, ValueError)


# ---------------------------------------------------------------------------
# shared API: step graph, config, keys, profile, input.json
# ---------------------------------------------------------------------------


def test_step_graph_linear_fallback_and_synthetic_names() -> None:
    steps: list[dict[str, Any]] = [{"name": "a"}, {}, {"name": "c"}]
    assert g.step_graph(steps) == [
        ("a", [], []),
        ("step_1", ["a"], []),
        ("c", ["a", "step_1"], []),
    ]


def test_step_graph_explicit_roots_and_non_topological_order() -> None:
    steps = [
        {"name": "z", "depends_on": ["y", "x", "y"]},
        {"name": "y", "depends_on": ["x"]},
        {"name": "x", "depends_on": None},
        {"name": "w", "depends_on": []},
    ]
    assert g.step_graph(steps) == [
        ("z", ["x", "y"], ["y", "x"]),
        ("y", ["x"], ["x"]),
        ("x", [], []),
        ("w", [], []),
    ]


@pytest.mark.parametrize(
    "steps",
    [
        [{"name": "a", "depends_on": ["b"]}, {"name": "b", "depends_on": ["a"]}],
        [{"name": "a", "depends_on": ["a"]}],
        [{"name": "a", "depends_on": ["nope"]}],
        [{"name": "a"}, {"name": "a"}],
        [{"name": "a", "depends_on": "b"}, {"name": "b"}],
        "not a list",
    ],
)
def test_step_graph_refuses(steps: Any) -> None:
    with pytest.raises(ValueError):
        g.step_graph(steps)


def test_expected_step_config_matches_the_runner_rules() -> None:
    config_in = {"model": "m", "api_key": "k", "headers": {"a": "b"}, "_routing": 1, "t": 0.5}
    step = {"name": "s", "config": config_in}
    params = {"project_id": "p", "t": 9, "api_base": "http://evil", "image_param": "x", "new": None}
    config = g.expected_step_config(step, "s", params)
    assert config == {
        "model": "m", "t": 0.5, "step_name": "s", "_workflow_params": params,
        "project_id": "p", "new": None,
    }  # fmt: skip
    config["_workflow_params"]["t"] = 0
    assert params["t"] == 9 and step["config"]["api_key"] == "k"


def test_expected_step_config_equals_the_runner_when_umrs_is_importable() -> None:
    runner = pytest.importorskip("umrs.server.workflow_runner")
    lane = pytest.importorskip("umrs.server.remote_slurm_step_executor")
    step = {"name": "s", "config": {"k": 1.5, "api_key": "x", "_routing": {}}}
    params = {"project_id": "p", "headers": {"h": "v"}, "q": [1, None]}
    expected = runner._assemble_step_config(step, params)
    for key in lane._PLATFORM_CONFIG_KEYS:
        expected.pop(key, None)
    assert g.expected_step_config(step, "s", params) == expected


@pytest.mark.parametrize(
    ("raw", "member"),
    [
        ("a/b.txt", f"{OUT_BUCKET}/a/b.txt"),
        (f"s3://{OUT_BUCKET}/a", f"{OUT_BUCKET}/a"),
        ("s3://other-bucket/a", None),
        ("a//b", None),
        ("./a", None),
        ("", None),
        (f"s3://{OUT_BUCKET}", None),
        (7, None),
    ],
)
def test_out_member(raw: Any, member: str | None) -> None:
    assert g.out_member(raw, OUT_BUCKET) == member


def test_physical_key_and_normal_keys() -> None:
    assert g.physical_key(RUN_ID, "a", "x/y") == f"runs/{RUN_ID}/a/x/y"
    for bad in ("", "/a", "a/", "a/./b", "a\x01", "x" * 256, "é" * 513, "\ud800"):
        assert not g.is_normal_key(bad), bad
    with pytest.raises(ValueError):
        g.physical_key(RUN_ID, "a", "../x")


def test_src_file_entry() -> None:
    assert g.src_file_entry("a/b.py", b"xy") == {"path": "a/b.py", "sha256": _sha(b"xy"), "size": 2}
    with pytest.raises(ValueError):
        g.src_file_entry("a//b.py", b"")


PROFILE_BREAKS = [
    ("extra", 1),
    ("slurm.path", "relative/bin"),
    ("slurm.path", "/usr/bin:/a b"),
    ("slurm.path", "/usr/../bin"),
    ("image_archive_dir", "/a:b"),
    ("image_archive_dir", '/a"b'),
    ("venue.root", "rel"),
    ("caps.reply_bytes", (4 << 20) + 1),
    ("caps.max_src_bytes", 200 * 1024 * 1024 + 1),
    ("caps.max_in_bytes", -1),
    ("partitions.gpu.arch", "riscv"),
    ("partitions.gpu.gres", "gpu 1"),
    ("partitions.gpu.mem", 1),
    ("gpu.form", "other"),
    ("watch_seconds", 0),
    ("v", 2),
]


@pytest.mark.parametrize(("path", "value"), PROFILE_BREAKS)
def test_validate_site_profile_refuses(world: World, path: str, value: Any) -> None:
    profile = json.loads(json.dumps(world.profile))
    _mutate(profile, path, value)
    with pytest.raises(g.ContractError) as caught:
        g.validate_site_profile(profile)
    assert (caught.value.error, caught.value.code) == ("failed", g.EXIT_FAILED)


def test_site_profile_bytes_are_canonical(world: World) -> None:
    data = g.site_profile_bytes(world.profile)
    assert g.parse_strict(data) == world.profile
    assert data == (world.root / "site.json").read_bytes()


def test_input_json_shape_and_parent_rewrite(world: World) -> None:
    steps = [
        world.step("a"),
        world.step("b", after=("a",)),
        world.step("c", after=("a", "b"), chained=("b",)),
    ]
    body = world.intent(steps)
    parents = {
        "a": {"status": "ok", "result": {"output_keys": ["x.txt"], "metadata": {"f": 1.5}}},
        "b": {"status": "ok", "result": {"output_keys": [f"s3://{OUT_BUCKET}/y/z", "w"]}},
    }
    doc = json.loads(g.input_json(body, "c", parents))
    assert set(doc) == {"v", "entrypoint", "config", "parent_results", "ctx"}
    assert (doc["entrypoint"], doc["config"]) == (ENTRY, body["steps"][2]["config"])
    first = {"output_keys": [f"runs/{RUN_ID}/a/x.txt"], "metadata": {"f": 1.5}}
    assert doc["parent_results"]["a"] == first
    keys_b = [f"s3://{OUT_BUCKET}/runs/{RUN_ID}/b/y/z", f"runs/{RUN_ID}/b/w"]
    assert doc["parent_results"]["b"]["output_keys"] == keys_b
    assert doc["ctx"] == {
        "run_tag": RUN_ID, "step": "c", "attempt": 1, "input_bucket": IN_BUCKET,
        "output_bucket": OUT_BUCKET, "parent_output_bucket": OUT_BUCKET, "input_keys": keys_b,
    }  # fmt: skip
    root = json.loads(g.input_json(body, "a", {}))
    assert root["ctx"]["parent_output_bucket"] == "" and root["parent_results"] == {}


@pytest.mark.parametrize(
    "reply",
    [
        None,
        {"status": "error"},
        {"status": "ok"},
        {"status": "ok", "result": {"output_keys": ["s3://x-y/z"]}},
        {"status": "ok", "result": {"output_keys": [3]}},
    ],
)
def test_input_json_refuses_uncollected_parents(world: World, reply: Any) -> None:
    body = world.intent([world.step("a"), world.step("b", after=("a",))])
    with pytest.raises(g.ContractError) as caught:
        g.input_json(body, "b", {"a": reply} if reply is not None else {})
    assert caught.value.error == "parent_not_collected"


def test_input_json_cap(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "INPUT_JSON_MAX_BYTES", 64)
    with pytest.raises(g.ContractError, match="too_large"):
        g.input_json(world.intent([world.step("a")]), "a", {})


def test_manifest_json_lists_signed_and_chained_objects(world: World) -> None:
    data = b"payload"
    steps = [
        world.step("a"),
        world.step("b", after=("a",), chained=("a",), objects=(obj("in/x", data),)),
    ]
    body = world.intent(steps)
    reply = {"status": "ok", "result": {"output_keys": ["o/1"]}}
    outs = {"a": {f"{OUT_BUCKET}/o/1": ["f" * 64, 3]}}
    doc = json.loads(g.manifest_json(body, "b", {"a": reply}, outs))
    entry = {"path": "blobs/" + _sha(data), "size": 7, "etag": "", "content_type": ""}
    chained = {"path": "blobs/" + "f" * 64, "size": 3, "etag": "", "content_type": ""}
    assert doc == {
        "objects": {f"{IN_BUCKET}/in/x": entry, f"{OUT_BUCKET}/runs/{RUN_ID}/a/o/1": chained},
        "prefixes": [],
        "output_bucket": OUT_BUCKET,
    }
    with pytest.raises(g.ContractError, match="parent_not_collected"):
        g.manifest_json(body, "b", {"a": reply}, {"a": {}})


# ---------------------------------------------------------------------------
# hello, profile loading, work directory
# ---------------------------------------------------------------------------


def test_hello_reports_the_site_profile_sha_and_verbs(world: World) -> None:
    out = ok(world.run("hello"))
    assert out["gate_version"] == "3" and out["site_profile_sha256"] == world.profile_sha
    assert out["verbs"] == list(g.VERBS)
    (world.root / "site.json").write_bytes(b"not json")
    assert ok(world.run("hello"))["site_profile_sha256"] == _sha(b"not json")


def test_profile_absent_invalid_or_for_another_site(world: World) -> None:
    (world.root / "site.json").unlink()
    assert (world.run("sweep").code, world.run("sweep").err["error"]) == (3, "absent")
    (world.root / "site.json").write_bytes(b'{"v": 1}')
    assert world.run("sweep").code == 1
    world.write_profile(site="s2")
    assert world.run("sweep").code == 1


@pytest.mark.parametrize("problem", ["group_writable", "foreign_owner", "symlink", "missing"])
def test_slurm_path_must_be_safe(world: World, problem: str) -> None:
    if problem == "group_writable":
        world.bin.chmod(0o775)
    elif problem == "foreign_owner":
        world.profile["slurm"]["owner_uids"] = []
    elif problem == "symlink":
        os.symlink(world.bin, world.tmp / "binlink")
        world.profile["slurm"]["path"] = str(world.tmp / "binlink")
    else:
        world.profile["slurm"]["path"] = str(world.tmp / "nope")
    world.write_profile()
    refused(world.run("sweep"), "profile_unsafe")


@pytest.mark.parametrize("problem", ["open_parent", "group_parent"])
def test_slurm_path_ancestors_must_be_safe(world: World, problem: str) -> None:
    """Fixer amendment 2026-11-08: a root-owned 0755 ``bin`` under a parent
    another account can write is renamed away and replaced with a fake."""
    parent = world.tmp / "opt"
    parent.mkdir()
    parent.chmod(0o777 if problem == "open_parent" else 0o770)
    os.rename(world.bin, parent / "bin")
    world.profile["slurm"]["path"] = str(parent / "bin")
    world.write_profile()
    result = world.run("sweep")
    refused(result, "profile_unsafe")
    assert "writable" in str(result.err)


def _tool_session(world: World) -> Any:
    session = g._Session.__new__(g._Session)
    session.profile = world.profile
    return session


@pytest.mark.parametrize(
    "problem", ["link_into_open_tree", "writable_tool", "foreign_tool", "link_to_foreign_dir"]
)
def test_slurm_tools_are_resolved_and_checked(world: World, problem: str) -> None:
    """The tool file itself: a link out of a safe directory into a tree
    another account can write, a writable or foreign-owned file, or a link
    into a directory of an unlisted owner, is refused before it runs."""
    tool = world.bin / "squeue"
    elsewhere = world.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o755)
    elsewhere.chmod(0o777 if problem == "link_into_open_tree" else 0o755)
    if problem in ("link_into_open_tree", "link_to_foreign_dir"):
        os.rename(tool, elsewhere / "squeue")
        tool.symlink_to(elsewhere / "squeue")
    if problem == "writable_tool":
        tool.chmod(0o777)
    if problem in ("foreign_tool", "link_to_foreign_dir"):
        world.profile["slurm"]["owner_uids"] = []
    with pytest.raises(g.GateError) as err:
        g._slurm_tool(_tool_session(world), "squeue")
    assert err.value.error == "profile_unsafe"


def test_a_slurm_tool_linked_inside_a_safe_tree_is_executed_resolved(world: World) -> None:
    real = world.tmp / "slurm-real"
    real.mkdir(mode=0o755)
    real.chmod(0o755)
    os.rename(world.bin / "squeue", real / "squeue")
    (world.bin / "squeue").symlink_to(real / "squeue")
    assert g._slurm_tool(_tool_session(world), "squeue") == str(real / "squeue")


def test_work_link_target_under_an_open_parent_is_refused(world: World) -> None:
    """Fixer amendment 2026-11-08: a group scratch directory without the
    sticky bit lets another member rename the target after submit and put
    its own tree (code, inputs) at the same path."""
    group = world.tmp / "scratch-group"
    group.mkdir()
    group.chmod(0o777)
    target = group / "umrs-work"
    target.mkdir(mode=0o700)
    (world.root / "work").symlink_to(target)
    body = world.intent([world.step("a")])
    result = world.run(f"mkwd {_iid(body)} a")
    assert result.code == 1 and "another account can change" in str(result.err), result
    assert list(target.iterdir()) == []


def test_work_link_target_under_a_sticky_root_directory_is_accepted(world: World) -> None:
    """``/tmp`` is root-owned and sticky: nobody else can rename a login's
    entry there, so a work directory below it passes."""
    assert g.ancestors_problem(str(world.tmp / "x"), {os.geteuid()}) == ""
    assert "group- or other-writable" in g.ancestors_problem(
        str(_open_dir(world.tmp / "o") / "x"), {os.geteuid()}
    )


def test_work_may_be_one_link_to_a_private_directory(world: World) -> None:
    scratch = world.tmp / "scratch"
    scratch.mkdir(mode=0o700)
    (world.root / "work").symlink_to(scratch)
    body, wd = _one_step(world)
    assert (scratch / wd / "src" / "pkg" / "step.py").read_bytes() == SRC["pkg/step.py"]
    job = ok(world.submit(body, wd, "a"))["job_id"]
    assert world.jobs()[job]["workdir"] == str(scratch / wd)
    scratch.chmod(0o755)
    assert world.run("sweep").code == 1


# ---------------------------------------------------------------------------
# mkwd
# ---------------------------------------------------------------------------


def test_mkwd_creates_a_private_workdir_and_record(world: World) -> None:
    body = world.intent([world.step("a")])
    wd = world.mkwd(body, "a")
    assert re.fullmatch(_iid(body)[:16] + "-a-[0-9a-f]{8}", wd)
    modes = {sub: (world.work / wd / sub).stat().st_mode & 0o777 for sub in WORKDIR_SUBDIRS}
    assert modes == dict.fromkeys(WORKDIR_SUBDIRS, 0o700)
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    fields = (rec["intent_id"], rec["step"], rec["sealed"], rec["broken"], rec["job_id"])
    assert fields == (_iid(body), "a", False, False, "")
    assert json.loads(world.log.read_text().splitlines()[-1])["verb"] == "mkwd"


WORKDIR_SUBDIRS = ("in", "in/blobs", "out", "reply", "src", "log")


@pytest.mark.parametrize(
    "command", ["mkwd", "mkwd abc a", f"mkwd {'0' * 64}", f"mkwd {'0' * 64} -x"]
)
def test_mkwd_usage(world: World, command: str) -> None:
    assert world.run(command).code == 2


def test_mkwd_refusals(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _body, _wd, _job = _submitted(world)
    refused(world.run(f"mkwd {_iid(_body)} a"), "replayed")
    world.profile["caps"]["max_workdirs"] = 1
    world.write_profile()
    refused(world.run(f"mkwd {'1' * 64} a"), "too_many_workdirs")
    world.profile["caps"].update(max_workdirs=8, min_free_bytes=g.MAX_INT)
    world.write_profile()
    refused(world.run(f"mkwd {'1' * 64} a"), "low_space")
    world.profile["caps"]["min_free_bytes"] = 0
    world.write_profile()
    monkeypatch.setattr(g, "LOG_FULL_BYTES", 10)
    refused(world.run(f"mkwd {'1' * 64} a"), "log_full")


# ---------------------------------------------------------------------------
# stage-in (B2.0 section 5.1)
# ---------------------------------------------------------------------------


def _member(name: str, kind: bytes, data: bytes = b"x", **attrs: Any) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.size = kind, len(data) if kind == tarfile.REGTYPE else 0
    for key, value in attrs.items():
        setattr(info, key, value)
    return info


def _odd_tar(kind: str) -> bytes:
    sink = io.BytesIO()
    fmt = tarfile.GNU_FORMAT if kind in ("sparse", "latin1") else tarfile.PAX_FORMAT
    encoding = "latin-1" if kind == "latin1" else "utf-8"
    infos = {
        "symlink": _member("src/a", tarfile.SYMTYPE, linkname="/etc/passwd"),
        "hardlink": _member("src/a", tarfile.LNKTYPE, linkname="src/pkg/step.py"),
        "directory": _member("src/pkg", tarfile.DIRTYPE),
        "fifo": _member("src/a", tarfile.FIFOTYPE),
        "device": _member("src/a", tarfile.CHRTYPE),
        "sparse": _member("src/a", tarfile.GNUTYPE_SPARSE),
        "latin1": _member("src/\xff.py", tarfile.REGTYPE),
    }
    with tarfile.open(fileobj=sink, mode="w", format=fmt, encoding=encoding) as tar:
        info = infos[kind]
        tar.addfile(info, io.BytesIO(b"x") if info.isreg() else None)
    return sink.getvalue()


BAD_NAMES = ["src/./a", "src/a/", "src//a", "src/../a", "/src/a", "in/manifest.json", "in/x",
             "in/blobs/" + "A" * 64, "in/blobs/abc", "src", "other/a", "src/a\x01"]  # fmt: skip


@pytest.mark.parametrize("name", BAD_NAMES)
def test_stage_in_refuses_bad_names_and_breaks_the_workdir(world: World, name: str) -> None:
    body = world.intent([world.step("a")])
    wd = world.mkwd(body, "a")
    refused(
        world.run(f"stage-in {wd}", _tar([("src/pkg/step.py", b"1"), (name, b"x")])), "bad_member"
    )
    assert not (world.work / wd / "src" / "pkg" / "step.py").exists()
    refused(world.stage(wd), "broken")
    refused(world.submit(body, wd, "a"), "broken")
    assert ok(world.run(f"rmwd {wd}")) == {"removed": True}


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "directory", "fifo", "device", "sparse", "latin1"]
)
def test_stage_in_refuses_non_regular_members(world: World, kind: str) -> None:
    wd = world.mkwd(world.intent([world.step("a")]), "a")
    refused(world.run(f"stage-in {wd}", _odd_tar(kind)), "bad_member")
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    assert rec["broken"] is True and rec["src"] == {}


def test_stage_in_refuses_duplicates_digests_and_garbage(world: World) -> None:
    body = world.intent([world.step("a")])
    wd = world.mkwd(body, "a")
    refused(world.run(f"stage-in {wd}", _tar([("src/a", b"1"), ("src/a", b"2")])), "bad_member")
    wd = world.mkwd(body, "a")
    refused(
        world.run(f"stage-in {wd}", _tar([(f"in/blobs/{_sha(b'x')}", b"y")])), "digest_mismatch"
    )
    wd = world.mkwd(body, "a")
    refused(world.run(f"stage-in {wd}", b"\x01" * 2048), "bad_member")
    wd = world.mkwd(body, "a")
    refused(world.run(f"stage-in {wd}", _tar([("src/a", b"12345")])[:700]), "bad_member")
    wd = world.mkwd(body, "a")
    ok(world.stage(wd, {"a": b"1"}))
    refused(world.stage(wd, {"a": b"1"}), "bad_member")


def test_stage_in_file_then_directory_conflicts(world: World) -> None:
    body = world.intent([world.step("a")])
    wd = world.mkwd(body, "a")
    refused(world.run(f"stage-in {wd}", _tar([("src/a", b"1"), ("src/a/b", b"2")])), "bad_member")
    wd = world.mkwd(body, "a")
    refused(world.run(f"stage-in {wd}", _tar([("src/a/b", b"1"), ("src/a", b"2")])), "bad_member")


def test_stage_in_records_files_over_several_calls_and_caps_them(world: World) -> None:
    body = world.intent([world.step("a")])
    wd = world.mkwd(body, "a")
    out = ok(world.stage(wd, {"x/1": b"one"}, (b"blob",)))
    assert (out["files"], out["bytes"]) == (2, 7) and out["free_bytes"] > 0
    ok(world.stage(wd, {"x/2": b"two"}))
    ok(world.run(f"stage-in {wd}", b""))
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    assert rec["src"] == {"x/1": [_sha(b"one"), 3], "x/2": [_sha(b"two"), 3]}
    assert rec["blobs"] == {_sha(b"blob"): 4} and rec["stage_calls"] == 3
    assert (world.work / wd / "src" / "x" / "1").stat().st_mode & 0o777 == 0o600
    for _ in range(5):
        ok(world.run(f"stage-in {wd}", b""))
    refused(world.run(f"stage-in {wd}", b""), "too_large")


@pytest.mark.parametrize(
    ("cap", "value", "members"),
    [
        ("max_src_files", 1, [("src/a", b"1"), ("src/b", b"2")]),
        ("max_src_bytes", 3, [("src/a", b"1234")]),
        ("max_in_objects", 1, [(f"in/blobs/{_sha(b'1')}", b"1"), (f"in/blobs/{_sha(b'2')}", b"2")]),
        ("max_object_bytes", 1, [(f"in/blobs/{_sha(b'12')}", b"12")]),
        ("max_in_bytes", 2, [(f"in/blobs/{_sha(b'12')}", b"12"), (f"in/blobs/{_sha(b'3')}", b"3")]),
    ],
)
def test_stage_in_caps(
    world: World, cap: str, value: int, members: list[tuple[str, bytes]]
) -> None:
    world.profile["caps"][cap] = value
    world.write_profile()
    wd = world.mkwd(world.intent([world.step("a")]), "a")
    refused(world.run(f"stage-in {wd}", _tar(members)), "too_large")


def test_stage_in_stream_cap_and_low_space(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    caps = world.profile["caps"]
    assert g._stage_cap(caps) == caps["max_src_bytes"] + caps["max_in_bytes"] + 1024 * 200 + (
        1 << 20
    )
    with monkeypatch.context() as patch:
        patch.setattr(g, "_stage_cap", lambda _caps: 4096)
        wd = world.mkwd(world.intent([world.step("a")]), "a")
        refused(world.run(f"stage-in {wd}", _tar([("src/a", b"x" * 8192)])), "too_large")
    assert not (world.work / wd / "src" / "a").exists()
    wd = world.mkwd(world.intent([world.step("a")]), "a")
    world.profile["caps"]["min_free_bytes"] = g.MAX_INT
    world.write_profile()
    refused(world.stage(wd), "low_space")


def test_stage_in_waits_for_the_workdir_lock(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "LOCK_WAIT", 0.2)
    wd = world.mkwd(world.intent([world.step("a")]), "a")
    with held(world.root / "state" / "wd" / f"{wd}.lock"):
        refused(world.stage(wd), "busy")
    ok(world.stage(wd))


def test_stage_in_after_submit_is_sealed_and_unknown_workdirs_are_absent(world: World) -> None:
    _body, wd, _job = _submitted(world)
    refused(world.stage(wd), "sealed")
    other = f"{'0' * 16}-a-{'0' * 8}"
    assert world.run(f"stage-in {other}").code == 3
    assert world.run("stage-in ../x").code == 2


# ---------------------------------------------------------------------------
# submit (B2.0 section 5.3)
# ---------------------------------------------------------------------------


def test_submit_writes_the_gate_files(world: World) -> None:
    body, wd = _one_step(world)
    out = ok(world.submit(body, wd, "a"))
    assert sorted(out) == ["intent_id", "job_id", "log_head", "step", "wd"]
    assert (out["wd"], out["intent_id"], out["step"]) == (wd, _iid(body), "a")
    base = world.work / wd
    names = ("input.json", "job.sbatch", "in/manifest.json")
    assert [(base / name).stat().st_mode & 0o777 for name in names] == [0o600] * 3
    assert not (base / "endpoint.json").exists()
    doc = json.loads((base / "input.json").read_bytes())
    assert doc["config"] == {"threshold": 0.25, "label": None, "step_name": "a"}
    manifest = json.loads((base / "in" / "manifest.json").read_bytes())
    assert list(manifest["objects"]) == [f"{IN_BUCKET}/data/x.bin"]


def test_submit_runs_sbatch_in_a_clean_environment(world: World) -> None:
    _body, wd, _job = _submitted(world)
    base = world.work / wd
    call = world.calls("sbatch")[-1]
    assert call["args"] == ["--parsable", "--export=NONE", f"--chdir={base}", f"{base}/job.sbatch"]
    assert sorted(call["env"]) == ["HOME", "LC_ALL", "LOGNAME", "PATH", "USER"]
    assert (call["env"]["PATH"], call["env"]["LC_ALL"], call["cwd"]) == (
        str(world.bin),
        "C",
        str(base),
    )


def test_submit_records_state(world: World) -> None:
    body, wd, job = _submitted(world)
    state = world.root / "state"
    assert (state / "jobs" / job).read_text() == wd
    nonce = json.loads(_nonce(world, body, "a").read_text())
    assert (nonce["wd"], nonce["job_id"], nonce["submitted"] > 0) == (wd, job, True)
    stored = json.loads((state / "intents" / _iid(body) / "intent.json").read_text())
    assert base64.b64decode(stored["payload"]) == g.canonical_bytes(body, True)
    rec = json.loads((state / "wd" / f"{wd}.json").read_text())
    assert (rec["sealed"], rec["job_id"]) == (True, job)


def test_submit_logs_the_full_intent_id(world: World) -> None:
    body, wd, job = _submitted(world)
    entry = json.loads(world.log.read_text().splitlines()[-1])
    assert (entry["verb"], entry["result"]) == ("submit", "ok")
    assert f"intent={_iid(body)} step=a wd={wd} job={job} signer=SHA256:" in entry["detail"]
    assert g.verify_log(str(world.log))


def test_a_second_step_of_one_intent_passes_and_a_replay_does_not(world: World) -> None:
    body = world.intent([world.step("a"), world.step("b")])
    first, again, second = world.ready(body, "a"), world.ready(body, "a"), world.ready(body, "b")
    ok(world.submit(body, first, "a"))
    refused(world.submit(body, again, "a"), "replayed")
    ok(world.submit(body, second, "b"))
    assert len(world.calls("sbatch")) == 2


def test_expiry_before_the_first_submit_and_max_run_after_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = world.intent([world.step("a"), world.step("b")], max_run_seconds=600)
    wd_a, wd_b = world.ready(body, "a"), world.ready(body, "b")
    start = time.time()
    monkeypatch.setattr(g.time, "time", lambda: start + 3600 + 100)
    ok(world.submit(body, wd_a, "a"))
    monkeypatch.setattr(g.time, "time", lambda: start + 3600 + 650)
    ok(world.submit(body, wd_b, "b"))
    late = world.intent([world.step("a")])
    wd = world.ready(late, "a")
    monkeypatch.setattr(g.time, "time", lambda: start + 3600 + 121)
    refused(world.submit(late, wd, "a"), "expired")


def test_max_run_seconds_closes_later_steps(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    body = world.intent([world.step("a"), world.step("b")], max_run_seconds=600)
    wd_a, wd_b = world.ready(body, "a"), world.ready(body, "b")
    ok(world.submit(body, wd_a, "a"))
    start = time.time()
    monkeypatch.setattr(g.time, "time", lambda: start + 700)
    refused(world.submit(body, wd_b, "b"), "expired")


def _two_step(world: World) -> dict[str, Any]:
    return world.intent([world.step("a"), world.step("b", after=("a",), chained=("a",))])


def _swap_archive(world: World, image: dict[str, Any], data: bytes) -> None:
    (world.archives / f"{image['archive_sha256']}.tar").write_bytes(data)


def _s_usage(w: World) -> Any:
    return lambda: w.run("submit onlyone")


def _s_absent(w: World) -> Any:
    return lambda: w.run(f"submit {'0' * 16}-a-{'0' * 8} a", b"{}")


def _s_busy(w: World) -> Any:
    body, wd = _one_step(w)

    def final() -> Result:
        with held(w.root / "state" / "wd" / f"{wd}.lock"):
            return w.submit(body, wd, "a")

    return final


def _s_wrong_step_record(w: World) -> Any:
    body = w.intent([w.step("a"), w.step("b")])
    wd = w.ready(body, "a")
    return lambda: w.submit(body, wd, "b")


def _s_sealed(w: World) -> Any:
    body, wd, _job = _submitted(w)
    return lambda: w.submit(body, wd, "a")


def _s_broken(w: World) -> Any:
    body = w.intent([w.step("a")])
    wd = w.mkwd(body, "a")
    w.run(f"stage-in {wd}", _tar([("in/manifest.json", b"{}")]))
    return lambda: w.submit(body, wd, "a")


def _s_log_unwritable(w: World) -> Any:
    body, wd = _one_step(w)
    w.log.unlink()
    w.log.mkdir()
    return lambda: w.submit(body, wd, "a")


def _s_stdin_too_large(w: World) -> Any:
    body, wd = _one_step(w)
    return lambda: w.run(f"submit {wd} a", b" " * (g.SUBMIT_MAX_STDIN + 1))


def _s_bad_envelope(w: World) -> Any:
    body, wd = _one_step(w)
    return lambda: w.run(f"submit {wd} a", w.envelope(body, extra_key=1))


def _s_payload_too_large(w: World) -> Any:
    body, wd = _one_step(w)
    payload = b"x" * (g.INTENT_MAX_BYTES + 1)
    data = json.dumps({"payload": base64.b64encode(payload).decode(), "signature": "x"}).encode()
    return lambda: w.run(f"submit {wd} a", data)


def _s_not_signed(w: World) -> Any:
    body, wd = _one_step(w)
    data = json.dumps(
        {"payload": base64.b64encode(g.canonical_bytes(body, True)).decode(), "signature": ""}
    )
    return lambda: w.run(f"submit {wd} a", data.encode())


def _s_other_key(w: World) -> Any:
    body, wd = _one_step(w)
    return lambda: w.submit(body, wd, "a", key=w.keys.other)


def _s_control_namespace(w: World) -> Any:
    body, wd = _one_step(w)
    return lambda: w.submit(body, wd, "a", ns=f"umrs-control-v1@{DOMAIN}")


def _s_key_type(w: World) -> Any:
    body, wd = _one_step(w)
    cfg = json.loads((w.root / "gate.json").read_text())
    (w.root / "gate.json").write_text(json.dumps({**cfg, "allow_non_sk": False}))
    return lambda: w.submit(body, wd, "a")


def _s_not_canonical(w: World) -> Any:
    body, wd = _one_step(w)
    payload = g.canonical_bytes(body, True).replace(b'"alpha":0.5', b'"alpha":5E-1')
    return lambda: w.run(f"submit {wd} a", w.envelope(None, payload=payload))


def _s_bad_schema(w: World) -> Any:
    body, wd = _one_step(w, cpus=2.0)
    return lambda: w.submit(body, wd, "a")


def _s_field(field_: str, value: Any) -> Any:
    def scenario(w: World) -> Any:
        body, wd = _one_step(w)
        body[field_] = value
        wd = w.ready(body, "a", (b"input bytes",))
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_window(issued: int, expires: int) -> Any:
    def scenario(w: World) -> Any:
        now = int(time.time())
        body = w.intent([w.step("a")], issued=now + issued, expires=now + expires)
        wd = w.ready(body, "a")
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_wrong_intent(w: World) -> Any:
    body, wd = _one_step(w)
    other = {**body, "nonce": secrets.token_hex(16)}
    return lambda: w.submit(other, wd, "a")


def _s_changed_entrypoint(w: World) -> Any:
    body, wd = _one_step(w)
    other = json.loads(json.dumps(body))
    other["src_manifest"]["entrypoints"]["a"] = "pkg.evil:Step"
    return lambda: w.submit(other, wd, "a")


def _s_wrong_step_intent(w: World) -> Any:
    body = w.intent([w.step("a")])
    wd = w.ready(body, "zz")
    return lambda: w.submit(body, wd, "zz")


def _s_started_long_ago(w: World) -> Any:
    body, wd = _one_step(w)
    directory = w.root / "state" / "intents" / _iid(body)
    directory.mkdir(parents=True)
    (directory / "started").write_text(str(int(time.time()) - 8000))
    return lambda: w.submit(body, wd, "a")


def _s_step(**over: Any) -> Any:
    def scenario(w: World) -> Any:
        body, wd = _one_step(w, **over)
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_gpu_disabled(w: World) -> Any:
    w.profile["gpu"]["enabled"] = False
    w.write_profile()
    return _s_step(gpus=1)(w)


def _s_sacctmgr(rc: int, assocs: str = "") -> Any:
    def scenario(w: World) -> Any:
        body, wd = _one_step(w)
        w.behave(sacctmgr_rc=rc, assocs=assocs)
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_parent(
    reply: dict[str, Any] | None, outs: dict[str, bytes] | None, collect: bool = True
) -> Any:
    def scenario(w: World) -> Any:
        body = _two_step(w)
        wd_a = w.ready(body, "a")
        job = ok(w.submit(body, wd_a, "a"))["job_id"]
        if reply is not None:
            w.finish(wd_a, job, reply, outs)
            ok(w.run(f"read-reply {wd_a}"))
            if collect:
                ok(w.run(f"collect-out {wd_a}"))
        wd_b = w.ready(body, "b", (b"out-1",))
        return lambda: w.submit(body, wd_b, "b")

    return scenario


def _s_src(src: dict[str, bytes] | None, stray: str = "") -> Any:
    def scenario(w: World) -> Any:
        body = w.intent([w.step("a")])
        wd = w.ready(body, "a", (), src)
        if stray:
            (w.work / wd / stray).write_bytes(b"stray")
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_input(blobs: tuple[bytes, ...], size_delta: int = 0, stray: str = "") -> Any:
    def scenario(w: World) -> Any:
        entry = obj("data/x.bin", b"input bytes")
        entry["size"] += size_delta
        body = w.intent([w.step("a", objects=(entry,))])
        wd = w.ready(body, "a", blobs)
        if stray:
            (w.work / wd / stray).write_bytes(b"stray")
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_image(change: str) -> Any:
    def scenario(w: World) -> Any:
        body, wd = _one_step(w)
        path = w.archives / f"{w.image['archive_sha256']}.tar"
        if change == "absent":
            path.unlink()
        elif change == "bytes":
            path.write_bytes(docker_archive(LABELS, "other")[0])
        elif change == "config":
            data, _id = docker_archive(LABELS, "other")
            path.unlink()
            image = {**w.image, "archive_sha256": _sha(data)}
            (w.archives / f"{_sha(data)}.tar").write_bytes(data)
            body, wd = _one_step(w, image=image)
        else:
            body, wd = _one_step(w, image=w.add_image(labels=json.loads(change)))
        return lambda: w.submit(body, wd, "a")

    return scenario


def _s_credential(endpoint: bool, credential: Any) -> Any:
    def scenario(w: World) -> Any:
        extra = {"endpoint": {"host": "10.0.0.1", "port": 8000, "kind": "vllm"}} if endpoint else {}
        body, wd = _one_step(w, **extra)
        kw = {} if credential is None else {"endpoint_credential": credential}
        return lambda: w.submit(body, wd, "a", **kw)

    return scenario


def _s_input_json(w: World) -> Any:
    body, wd = _one_step(w)
    w.monkeypatch.setattr(g, "INPUT_JSON_MAX_BYTES", 32)
    return lambda: w.submit(body, wd, "a")


def _s_deadline(w: World) -> Any:
    body, wd = _one_step(w)
    return lambda: w.run(f"submit {wd} a", w.envelope(body), deadline=time.monotonic() + 100)


def _s_replayed(w: World) -> Any:
    body, wd = _one_step(w)
    other = w.ready(body, "a", (b"input bytes",))
    ok(w.submit(body, other, "a"))
    return lambda: w.submit(body, wd, "a")


def _s_sbatch_fails(w: World) -> Any:
    body, wd = _one_step(w)
    w.behave(sbatch_rc=1)
    return lambda: w.submit(body, wd, "a")


OK_REPLY = {"status": "ok", "result": {"output_keys": ["o/1"]}}
LABEL_NO_JSON = json.dumps({"org.umrs.step-contract": "1", "org.umrs.step-payload": "pickle"})
LABEL_NO_CONTRACT = json.dumps({"org.umrs.step-payload": "json"})

# (contract step, scenario, exit code, refusal) in the order of B2.0 section 5.3
SUBMIT_MATRIX = [
    ("1 usage", _s_usage, 2, "usage"),
    ("2 absent", _s_absent, 3, "absent"),
    ("2 busy", _s_busy, 4, "busy"),
    ("2 wrong_step", _s_wrong_step_record, 4, "wrong_step"),
    ("2 sealed", _s_sealed, 4, "sealed"),
    ("2 broken", _s_broken, 4, "broken"),
    ("3 log_unwritable", _s_log_unwritable, 4, "log_unwritable"),
    ("4 too_large", _s_stdin_too_large, 4, "too_large"),
    ("5 bad_envelope", _s_bad_envelope, 4, "bad_envelope"),
    ("5 too_large", _s_payload_too_large, 4, "too_large"),
    ("5 not_signed", _s_not_signed, 4, "not_signed"),
    ("6 bad_signature key", _s_other_key, 4, "bad_signature"),
    ("6 bad_signature ns", _s_control_namespace, 4, "bad_signature"),
    ("7 key_type", _s_key_type, 4, "key_type_not_allowed"),
    ("8 not_canonical", _s_not_canonical, 4, "not_canonical"),
    ("9 bad_schema", _s_bad_schema, 4, "bad_schema"),
    ("10 wrong_site", _s_field("site", "s2"), 4, "wrong_site"),
    ("10 wrong_principal", _s_field("principal", "bob"), 4, "wrong_principal"),
    ("10 wrong_login", _s_field("login", "nobody-else"), 4, "wrong_login"),
    ("10 wrong_intent", _s_wrong_intent, 4, "wrong_intent"),
    ("10 changed entrypoint", _s_changed_entrypoint, 4, "wrong_intent"),
    ("10 wrong_step", _s_wrong_step_intent, 4, "wrong_step"),
    ("10 profile_mismatch", _s_field("site_profile_sha256", "b" * 64), 4, "profile_mismatch"),
    ("11 not yet", _s_field("issued", int(time.time()) + 900), 4, "expired"),
    ("11 expired", _s_window(-3000, -200), 4, "expired"),
    ("11 within skew", _s_window(-3700, -110), 0, ""),
    ("11 max_run", _s_started_long_ago, 4, "expired"),
    ("12 partition", _s_step(partition="nope"), 4, "resource_not_allowed"),
    ("12 cpus", _s_step(cpus=17), 4, "resource_not_allowed"),
    ("12 time", _s_step(partition="cpu", time=3601), 4, "resource_not_allowed"),
    ("12 mem", _s_step(partition="cpu", mem="4G"), 4, "resource_not_allowed"),
    ("12 gpus", _s_step(gpus=5), 4, "resource_not_allowed"),
    ("12 gpu disabled", _s_gpu_disabled, 4, "resource_not_allowed"),
    ("12 account", _s_sacctmgr(0, "c1|other||\n"), 4, "account_not_held"),
    ("12 sacctmgr", _s_sacctmgr(1), 5, "scheduler"),
    ("13 no reply", _s_parent(None, None), 4, "parent_not_collected"),
    (
        "13 error reply",
        _s_parent({"status": "error", "phase": "run", "message": "x"}, None),
        4,
        "parent_not_collected",
    ),
    (
        "13 not collected",
        _s_parent(OK_REPLY, {f"{OUT_BUCKET}/o/1": b"out-1"}, False),
        4,
        "parent_not_collected",
    ),
    (
        "13 key not in out",
        _s_parent(
            {"status": "ok", "result": {"output_keys": ["o/2"]}}, {f"{OUT_BUCKET}/o/1": b"out-1"}
        ),
        4,
        "parent_not_collected",
    ),  # fmt: skip
    (
        "13 other bucket",
        _s_parent(
            {"status": "ok", "result": {"output_keys": ["s3://elsewhere/o/1"]}},
            {f"{OUT_BUCKET}/o/1": b"out-1"},
        ),
        4,
        "parent_not_collected",
    ),  # fmt: skip
    ("14 extra src", _s_src({**SRC, "pkg/extra.py": b"x"}), 4, "src_mismatch"),
    ("14 changed src", _s_src({**SRC, "pkg/step.py": b"evil"}), 4, "src_mismatch"),
    ("14 missing src", _s_src({"pkg/step.py": SRC["pkg/step.py"]}), 4, "src_mismatch"),
    ("14 stray file", _s_src(None, "src/pkg/stray.py"), 4, "src_mismatch"),
    ("15 swapped blob", _s_input((b"other bytes",)), 4, "input_mismatch"),
    ("15 signed size", _s_input((b"input bytes",), 1), 4, "input_mismatch"),
    ("15 stray in/", _s_input((b"input bytes",), 0, "in/blobs/extra"), 4, "input_mismatch"),
    ("16 image_absent", _s_image("absent"), 4, "image_absent"),
    ("16 archive bytes", _s_image("bytes"), 4, "image_mismatch"),
    ("16 config sha", _s_image("config"), 4, "image_mismatch"),
    ("16 label no json", _s_image(LABEL_NO_JSON), 4, "image_label"),
    ("16 label no contract", _s_image(LABEL_NO_CONTRACT), 4, "image_label"),
    ("17 credential, no endpoint", _s_credential(False, {"headers": {}}), 4, "bad_envelope"),
    ("17 endpoint, no credential", _s_credential(True, None), 4, "bad_envelope"),
    ("17 bad header", _s_credential(True, {"headers": {"Bad Name": "x"}}), 4, "bad_envelope"),
    ("17 bad shape", _s_credential(True, {"headers": {}, "x": 1}), 4, "bad_envelope"),
    ("18 input.json", _s_input_json, 4, "too_large"),
    ("19 deadline", _s_deadline, 1, "failed"),
    ("20 replayed", _s_replayed, 4, "replayed"),
    ("22 sbatch", _s_sbatch_fails, 5, "scheduler"),
]


@pytest.mark.parametrize(
    ("label", "scenario", "code", "name"), SUBMIT_MATRIX, ids=[m[0] for m in SUBMIT_MATRIX]
)
def test_submit_refusals_in_contract_order(
    world: World, monkeypatch: pytest.MonkeyPatch, label: str, scenario: Any, code: int, name: str
) -> None:
    monkeypatch.setattr(g, "LOCK_WAIT", 0.2)
    world.monkeypatch = monkeypatch  # type: ignore[attr-defined]
    final = scenario(world)
    queued = len(world.jobs())
    result = final()
    if code == 0:  # a control case at the edge of a rule
        assert result.code == 0 and len(world.jobs()) == queued + 1, result
        return
    assert (result.code, result.err["error"]) == (code, name), result
    assert len(world.jobs()) == queued, "a refused submit queued a job"


def test_refusal_order_earlier_checks_win(world: World) -> None:
    body, wd = _one_step(world, partition="nope")
    body["site"] = "s2"
    wd = world.ready(body, "a", (b"input bytes",))
    refused(world.submit(body, wd, "a", key=world.keys.other), "bad_signature")
    refused(world.submit(body, wd, "a"), "wrong_site")
    (world.archives / f"{world.image['archive_sha256']}.tar").unlink()
    body2, wd2 = _one_step(world, cpus=99)
    (world.work / wd2 / "src" / "stray").write_bytes(b"x")
    refused(world.submit(body2, wd2, "a"), "resource_not_allowed")


def test_a_workdir_of_another_step_is_wrong_step_before_sealed(world: World) -> None:
    body = world.intent([world.step("a"), world.step("b")])
    wd = world.ready(body, "a")
    ok(world.submit(body, wd, "a"))
    refused(world.submit(body, wd, "b"), "wrong_step")
    refused(world.submit(body, wd, "a"), "sealed")


def test_lookup_logs_a_job_id_it_resolves(world: World) -> None:
    body, _wd = _one_step(world)
    world.behave(sbatch_rc=1, queue_anyway=True, squeue_rc=1)
    world.submit(body, _wd, "a")
    world.behave()
    ok(world.run(f"lookup {_iid(body)} a"))
    entry = json.loads(world.log.read_text().splitlines()[-1])
    assert (entry["verb"], entry["detail"]) == (
        "lookup",
        f"intent={_iid(body)[:16]} step=a job=1000",
    )
    assert g.verify_log(str(world.log))


def test_failed_signed_submits_are_logged_and_the_chain_holds(world: World) -> None:
    body, wd = _one_step(world)
    refused(world.submit(body, wd, "a", key=world.keys.other), "bad_signature")
    body["site"] = "s2"
    refused(world.submit(body, wd, "a"), "wrong_site")
    entries = [json.loads(line) for line in world.log.read_text().splitlines()]
    assert entries[-2]["detail"] == "bad_signature: before a valid signature"
    assert entries[-1]["detail"].startswith("wrong_site: ")
    assert g.verify_log(str(world.log))


def test_less_than_120_seconds_left_writes_no_nonce(world: World) -> None:
    body, wd = _one_step(world)
    result = world.run(f"submit {wd} a", world.envelope(body), deadline=time.monotonic() + 119)
    assert (result.code, result.err) == (1, {"error": "failed", "detail": "deadline"})
    assert not _nonce(world, body, "a").exists() and not world.calls("sbatch")
    ok(world.submit(body, wd, "a"))


def test_sbatch_failure_that_queued_the_job_is_adopted(world: World) -> None:
    body, wd = _one_step(world)
    world.behave(sbatch_rc=1, queue_anyway=True)
    out = ok(world.submit(body, wd, "a"))
    assert (
        out["job_id"] == "1000"
        and json.loads(_nonce(world, body, "a").read_text())["job_id"] == "1000"
    )


def test_sbatch_failure_without_a_job_frees_the_step_and_keeps_the_workdir_sealed(
    world: World,
) -> None:
    body, wd = _one_step(world)
    world.behave(sbatch_rc=1)
    assert world.submit(body, wd, "a").code == 5
    assert not _nonce(world, body, "a").exists()
    assert not (world.root / "state" / "intents" / _iid(body) / "started").exists()
    refused(world.submit(body, wd, "a"), "sealed")
    world.behave()
    ok(world.submit(body, world.ready(body, "a", (b"input bytes",)), "a"))


def test_sbatch_timeout_leaves_an_unknown_job_that_lookup_reports(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(g, "SBATCH_TIMEOUT", 0.5)
    body, wd = _one_step(world)
    spare = world.ready(body, "a", (b"input bytes",))
    world.behave(sbatch_sleep=2)
    assert world.submit(body, wd, "a").code == 5
    assert json.loads(_nonce(world, body, "a").read_text())["job_id"] == ""
    world.behave()
    out = ok(world.run(f"lookup {_iid(body)} a"))
    assert (out["wd"], out["job_id"], out["state"]) == (wd, "", "UNKNOWN")
    refused(world.submit(body, spare, "a"), "replayed")
    refused(world.run(f"mkwd {_iid(body)} a"), "replayed")


def test_an_unanswered_workdir_match_keeps_the_nonce_and_lookup_resolves_it(world: World) -> None:
    body, wd = _one_step(world)
    world.behave(sbatch_rc=1, queue_anyway=True, squeue_rc=1)
    assert world.submit(body, wd, "a").code == 5
    assert json.loads(_nonce(world, body, "a").read_text())["job_id"] == ""
    world.behave()
    out = ok(world.run(f"lookup {_iid(body)} a"))
    assert (out["job_id"], out["state"]) == ("1000", "PENDING")
    assert (world.root / "state" / "jobs" / "1000").read_text() == wd
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    assert rec["job_id"] == "1000"
    assert ok(world.run("status 1000"))["state"] == "PENDING"
    assert world.run(f"lookup {'e' * 64} a").code == 3


def test_a_log_failure_after_sbatch_cancels_the_job(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, wd = _one_step(world)
    real = g.GateLog.record

    def record(self: Any, verb: str, result: str, detail: str, now: int) -> None:
        if (verb, result) == ("submit", "ok"):
            raise OSError("disk full")
        real(self, verb, result, detail, now)

    monkeypatch.setattr(g.GateLog, "record", record)
    refused(world.submit(body, wd, "a"), "log_unwritable")
    assert world.jobs()["1000"]["state"] == "CANCELLED"


def test_two_concurrent_submits_of_one_intent_both_run(world: World) -> None:
    body = world.intent([world.step("a"), world.step("b")])
    wds = {step: world.ready(body, step) for step in ("a", "b")}
    env = {"HOME": str(world.home), "PATH": "/usr/bin:/bin"}
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", str(GATE), "--site", SITE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**env, "SSH_ORIGINAL_COMMAND": f"submit {wd} {step}"},
        )
        for step, wd in wds.items()
    ]
    envelope = world.envelope(body)
    results = [proc.communicate(envelope, timeout=120) for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0], results
    assert len(world.jobs()) == 2
    intent_dir = world.root / "state" / "intents" / _iid(body)
    assert (intent_dir / "started").exists() and (intent_dir / "intent.json").exists()


def test_endpoint_credential_is_written_for_the_forwarder_only(world: World) -> None:
    endpoint = {"host": "10.0.0.1", "port": 8000, "kind": "vllm"}
    body, wd = _one_step(world, endpoint=endpoint)
    cred = {"headers": {"Authorization": "Bearer sk-step"}}
    ok(world.submit(body, wd, "a", endpoint_credential=cred))
    path = world.work / wd / "endpoint.json"
    assert json.loads(path.read_text()) == cred and path.stat().st_mode & 0o777 == 0o600
    script = (world.work / wd / "job.sbatch").read_text()
    pod = [line for line in script.splitlines() if "umrs.server.step_worker" in line][0]
    assert "endpoint.json" not in pod and "reply/endpoint.sock:/run/umrs/endpoint.sock:rw" in pod
    assert "--upstream 10.0.0.1:8000" in script and "sk-step" not in script


def test_image_archive_check_is_cached_by_file_identity(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submitted(world)
    cache = world.root / "state" / "images" / f"{world.image['archive_sha256']}.ok"
    assert json.loads(cache.read_text())["image_id"] == world.image["image_id"]
    with monkeypatch.context() as patch:
        patch.setattr(g, "_hash_stream", lambda _h: pytest.fail("a cached archive was hashed"))
        body = world.intent([world.step("a")])
        ok(world.submit(body, world.ready(body, "a"), "a"))
    body, wd = _one_step(world)
    path = world.archives / f"{world.image['archive_sha256']}.tar"
    data = path.read_bytes()
    path.unlink()
    path.write_bytes(data[:-1] + b"\x01")
    refused(world.submit(body, wd, "a"), "image_mismatch")


# ---------------------------------------------------------------------------
# read-reply and collect-out (B2.0 section 5.2)
# ---------------------------------------------------------------------------

OUTS = {f"{OUT_BUCKET}/o/1": b"out-1", f"{OUT_BUCKET}/deep/dir/2.txt": b"two"}


def _completed(
    world: World, reply: dict[str, Any] | None = None
) -> tuple[dict[str, Any], str, str]:
    body, wd, job = _submitted(world)
    world.finish(
        wd, job, reply or {"status": "ok", "result": {"output_keys": ["o/1"], "x": 0.5}}, OUTS
    )
    return body, wd, job


def _untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar}  # type: ignore[union-attr]


def test_read_reply_and_collect_out_need_a_completed_job(world: World) -> None:
    _body, wd, job = _submitted(world)
    refused(world.run(f"read-reply {wd}"), "not_finished")
    refused(world.run(f"collect-out {wd}"), "not_finished")
    world.set_state(job, "FAILED")
    refused(world.run(f"read-reply {wd}"), "not_finished")
    wd_new = world.mkwd(world.intent([world.step("a")]), "a")
    refused(world.run(f"read-reply {wd_new}"), "not_finished")


def test_read_reply_records_the_reply_once(world: World) -> None:
    body, wd, _job = _completed(world)
    raw = (world.work / wd / "reply" / "output.json").read_bytes()
    assert world.run(f"read-reply {wd}").raw == raw
    intent_dir = world.root / "state" / "intents" / _iid(body)
    assert (intent_dir / "a.reply.json").read_bytes() == raw
    assert json.loads((intent_dir / "a.json").read_text())["reply_sha256"] == _sha(raw)
    assert world.run(f"read-reply {wd}").raw == raw
    (world.work / wd / "reply" / "output.json").write_text('{"status": "ok", "result": {}}')
    refused(world.run(f"read-reply {wd}"), "digest_mismatch")
    assert (intent_dir / "a.reply.json").read_bytes() == raw


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b"[1]", "bad_reply"),
        (b'{"a": NaN}', "bad_reply"),
        (b'{"a": 1, "a": 2}', "bad_reply"),
        (b"not json", "bad_reply"),
        (b"{}" + b" " * (1 << 20), "too_large"),
        (None, "bad_reply"),
        ("symlink", "bad_reply"),
        ("hardlink", "bad_reply"),
    ],
    ids=["array", "nan", "duplicate", "not-json", "too-large", "missing", "symlink", "hardlink"],
)
def test_read_reply_refusals(world: World, content: Any, name: str) -> None:
    _body, wd, job = _completed(world)
    path = world.work / wd / "reply" / "output.json"
    path.unlink()
    if content == "symlink":
        path.symlink_to(world.root / "gate.json")
    elif content == "hardlink":
        os.link(world.root / "gate.json", path)
    elif content is not None:
        path.write_bytes(content)
    refused(world.run(f"read-reply {wd}"), name)


def test_collect_out_streams_hashes_and_records_the_outputs(world: World) -> None:
    body, wd, _job = _completed(world)
    result = world.run(f"collect-out {wd}")
    assert result.code == 0 and result.out == {"files": 2, "bytes": 8}
    assert _untar(result.raw) == OUTS
    nonce = json.loads(_nonce(world, body, "a").read_text())
    assert nonce["out"] == {rel: [_sha(data), len(data)] for rel, data in OUTS.items()}
    assert _untar(world.run(f"collect-out {wd}").raw) == OUTS
    (world.work / wd / "out" / OUT_BUCKET / "o" / "1").write_bytes(b"OUT-1")
    refused(world.run(f"collect-out {wd}"), "digest_mismatch")
    assert json.loads(_nonce(world, body, "a").read_text())["out"] == nonce["out"]


@pytest.mark.parametrize(
    "problem", ["symlink", "hardlink", "other_bucket", "top_file", "fifo", "files", "bytes"]
)
def test_collect_out_refusals(world: World, problem: str) -> None:
    _body, wd, _job = _completed(world)
    out = world.work / wd / "out"
    if problem == "symlink":
        (out / OUT_BUCKET / "link").symlink_to("/etc/passwd")
    elif problem == "hardlink":
        os.link(out / OUT_BUCKET / "o" / "1", out / OUT_BUCKET / "o" / "again")
    elif problem == "other_bucket":
        (out / "elsewhere").mkdir()
        (out / "elsewhere" / "x").write_bytes(b"x")
    elif problem == "top_file":
        (out / "loose").write_bytes(b"x")
    elif problem == "fifo":
        os.mkfifo(out / OUT_BUCKET / "pipe")
    else:
        world.profile["caps"]["max_out_files" if problem == "files" else "max_out_bytes"] = 1
        world.write_profile()
    expected = "too_large" if problem in ("files", "bytes") else "bad_member"
    refused(world.run(f"collect-out {wd}"), expected)


def test_a_two_step_chained_run(world: World) -> None:
    body = _two_step(world)
    wd_a = world.ready(body, "a")
    job_a = ok(world.submit(body, wd_a, "a"))["job_id"]
    reply = {
        "status": "ok",
        "result": {"output_keys": [f"s3://{OUT_BUCKET}/o/1"], "metadata": {"t": 0.1}},
    }
    world.finish(wd_a, job_a, reply, {f"{OUT_BUCKET}/o/1": b"out-1"})
    ok(world.run(f"read-reply {wd_a}"))
    assert world.run(f"collect-out {wd_a}").code == 0
    refused(world.submit(body, world.ready(body, "b", (b"forged",)), "b"), "input_mismatch")
    wd_b = world.ready(body, "b", (b"out-1",))
    ok(world.submit(body, wd_b, "b"))
    doc = json.loads((world.work / wd_b / "input.json").read_bytes())
    physical = f"s3://{OUT_BUCKET}/runs/{RUN_ID}/a/o/1"
    assert doc["parent_results"] == {"a": {"output_keys": [physical], "metadata": {"t": 0.1}}}
    assert doc["ctx"]["input_keys"] == [physical]
    manifest = json.loads((world.work / wd_b / "in" / "manifest.json").read_bytes())
    assert manifest["objects"][f"{OUT_BUCKET}/runs/{RUN_ID}/a/o/1"]["path"] == "blobs/" + _sha(
        b"out-1"
    )


def test_a_chained_key_that_collides_with_a_listed_object(world: World) -> None:
    collide = obj(f"runs/{RUN_ID}/a/o/1", b"out-1", bucket=OUT_BUCKET)
    body = world.intent(
        [world.step("a"), world.step("b", after=("a",), chained=("a",), objects=(collide,))]
    )
    wd_a = world.ready(body, "a")
    job_a = ok(world.submit(body, wd_a, "a"))["job_id"]
    world.finish(wd_a, job_a, OK_REPLY, {f"{OUT_BUCKET}/o/1": b"out-1"})
    ok(world.run(f"read-reply {wd_a}"))
    world.run(f"collect-out {wd_a}")
    refused(world.submit(body, world.ready(body, "b", (b"out-1",)), "b"), "input_mismatch")


# ---------------------------------------------------------------------------
# status, cancel, usage, tail-log: only this gate's own jobs and workdirs
# ---------------------------------------------------------------------------


def test_status_cancel_and_usage_act_only_on_own_jobs(world: World) -> None:
    _body, wd, job = _submitted(world)
    assert ok(world.run(f"status {job}")) == {"job_id": job, "state": "PENDING"}
    foreign = "1001"  # a job id this gate never submitted
    for verb in ("status", "cancel", "usage"):
        refused(world.run(f"{verb} {foreign}"), "not_mine")
    refused(world.run(f"usage {job} {foreign}"), "not_mine")
    usage = ok(world.run(f"usage {job}"))["jobs"]
    assert usage == [{"job_id": job, "state": "PENDING", "elapsed": "00:01:00", "alloc_tres": "cpu=2,mem=1G,gres/gpu=1",
                      "alloc_cpus": "2", "account": "acct1", "partition": "gpu",
                      "start": "2026-11-08T10:00:00", "end": "2026-11-08T10:01:00",
                      "grant_id": "", "settled": False}]  # fmt: skip
    assert ok(world.run(f"cancel {job}")) == {"job_id": job, "cancelled": True}
    assert ok(world.run(f"status {job}"))["state"] == "CANCELLED"
    assert world.run("status 12x").code == 2 and world.run("usage").code == 2


def test_status_reports_a_scheduler_failure(world: World) -> None:
    _body, _wd, job = _submitted(world)
    world.behave(squeue_rc=1, sacct_rc=1)
    assert world.run(f"status {job}").code == 5


def test_tail_log_reads_only_gate_workdirs_within_the_cap(world: World) -> None:
    _body, wd, _job = _submitted(world)
    assert ok(world.run(f"tail-log {wd}")) == {"tail": ""}
    (world.work / wd / "log" / "slurm.err").write_bytes(b"x" * 5000 + b"\xffend")
    assert ok(world.run(f"tail-log {wd} 4")) == {"tail": "�end"}
    assert len(ok(world.run(f"tail-log {wd}"))["tail"]) == 4096
    assert world.run(f"tail-log {wd} 4097").code == 2
    assert world.run(f"tail-log {'0' * 16}-a-{'0' * 8}").code == 3


# ---------------------------------------------------------------------------
# rmwd and sweep (B2.0 section 5.4)
# ---------------------------------------------------------------------------


def test_rmwd_refuses_a_live_job_and_fails_closed(world: World) -> None:
    _body, wd, job = _submitted(world)
    refused(world.run(f"rmwd {wd}"), "job_live")
    world.set_state(job, "COMPLETED")
    world.behave(sacct_rc=1)
    refused(world.run(f"rmwd {wd}"), "job_live")
    world.behave()
    assert ok(world.run(f"rmwd {wd}")) == {"removed": True}
    assert not (world.work / wd).exists()
    assert world.run(f"rmwd {wd}").code == 3
    assert world.run(f"read-reply {wd}").code == 3


def test_rmwd_removes_a_tree_the_step_made_unreadable(world: World) -> None:
    _body, wd, job = _completed(world)
    hidden = world.work / wd / "out" / "hidden"
    hidden.mkdir()
    (hidden / "f").write_bytes(b"x")
    hidden.chmod(0)
    assert ok(world.run(f"rmwd {wd}")) == {"removed": True}
    assert not (world.work / wd).exists()
    assert job


def test_a_sealed_workdir_without_a_job_id_is_live_until_slurm_says_otherwise(
    world: World,
) -> None:
    """Fixer amendment 2026-11-08: an sbatch failure Slurm could not resolve
    leaves job_id "" while the job may be queued; rmwd and sweep match the
    workdir first and remove only when Slurm certainly ran no job there."""
    body, wd = _one_step(world)
    world.behave(sbatch_rc=1, queue_anyway=True, squeue_rc=1)
    assert world.submit(body, wd, "a").code == 5
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    assert (rec["sealed"], rec["job_id"]) == (True, "")
    _age(world, wd, 7200)
    world.behave(squeue_rc=1)
    refused(world.run(f"rmwd {wd}"), "job_live")
    assert ok(world.run("sweep"))["removed"] == []
    world.behave()
    assert ok(world.run("sweep"))["removed"] == []
    refused(world.run(f"rmwd {wd}"), "job_live")
    rec = json.loads((world.root / "state" / "wd" / f"{wd}.json").read_text())
    assert rec["job_id"] == "1000"
    world.set_state("1000", "COMPLETED")
    assert ok(world.run(f"rmwd {wd}")) == {"removed": True}


def test_a_sealed_workdir_slurm_never_ran_is_removed(world: World) -> None:
    body, wd = _one_step(world)
    world.behave(sbatch_rc=1)
    assert world.submit(body, wd, "a").code == 5
    world.behave()
    assert ok(world.run(f"rmwd {wd}")) == {"removed": True}


def _age(world: World, wd: str, seconds: int) -> None:
    old = time.time() - seconds
    for path in (world.work / wd, world.work / wd / "log"):
        os.utime(path, (old, old))
    rec_path = world.root / "state" / "wd" / f"{wd}.json"
    rec = json.loads(rec_path.read_text())
    rec["created"] = int(old)
    rec_path.write_text(json.dumps(rec))


def test_sweep_removes_idle_workdirs_without_a_live_job(world: World) -> None:
    _body, live_wd, _job = _submitted(world)
    idle = world.mkwd(world.intent([world.step("a")]), "a")
    fresh = world.mkwd(world.intent([world.step("a")]), "a")
    orphan = f"{'f' * 16}-a-{'0' * 8}"
    (world.work / orphan).mkdir()
    for wd in (live_wd, idle):
        _age(world, wd, 7200)
    os.utime(world.work / orphan, (time.time() - 7200,) * 2)
    out = ok(world.run("sweep --older-than 60"))
    assert sorted(out["removed"]) == sorted([idle, orphan]) and out["kept"] == 2
    assert (world.work / live_wd).exists() and (world.work / fresh).exists()


def test_sweep_keeps_everything_when_slurm_does_not_answer(world: World) -> None:
    _body, wd, job = _submitted(world)
    world.set_state(job, "COMPLETED")
    _age(world, wd, 7200)
    world.behave(squeue_rc=1)
    assert ok(world.run("sweep"))["removed"] == []
    world.behave()
    assert ok(world.run("sweep"))["removed"] == [wd]


def test_sweep_prunes_intents_past_their_window(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, wd, job = _submitted(world)
    world.set_state(job, "COMPLETED")
    ok(world.run(f"rmwd {wd}"))
    intent_dir = world.root / "state" / "intents" / _iid(body)
    out = ok(world.run("sweep"))
    assert intent_dir.exists() and out["pruned"] == 1
    later = time.time() + 7200 + g.RETENTION_SECONDS + 10
    monkeypatch.setattr(g.time, "time", lambda: later)
    assert ok(world.run("sweep"))["pruned"] >= 1
    assert not intent_dir.exists() and not (world.root / "state" / "jobs" / job).exists()


def test_sweep_usage(world: World) -> None:
    assert world.run("sweep --older-than").code == 2
    assert world.run("sweep --newer 1").code == 2


# ---------------------------------------------------------------------------
# the job script (B2.0 section 6)
# ---------------------------------------------------------------------------


def _script(
    world: World, wd_path: str = "/scratch/u/wd", **over: Any
) -> tuple[dict[str, Any], str]:
    body = world.intent([world.step("a", **over)])
    return body, g.render_job_script(body, "a", world.profile, wd_path, LOGIN, "/home/u")


def _directives(script: str) -> list[str]:
    return [line[len("#SBATCH ") :] for line in script.splitlines() if line.startswith("#SBATCH ")]


def test_job_script_directives_are_the_allow_list(world: World) -> None:
    body, script = _script(world)
    i16 = _iid(body)[:16]
    assert script.splitlines()[0] == "#!/bin/bash -p"
    assert _directives(script) == [
        f"--job-name=umrs-gated-{i16}-a", f"--comment=umrs-gate:{i16}:a", "--no-requeue",
        "--export=NONE", "--time=10", "--partition=gpu", "--account=acct1", "--cpus-per-task=2",
        "--deadline=now+1500", "--output=/scratch/u/wd/log/slurm.out",
        "--error=/scratch/u/wd/log/slurm.err",
    ]  # fmt: skip
    assert not re.search(r"(^|[;&|]\s*)(source|\.)\s", script, re.M)
    assert "venue.sh" not in script and "input.pkl" not in script and "${_step_image}" not in script


def _missing(haystack: str, needles: Any) -> list[str]:
    return [needle for needle in needles if needle not in haystack]


def _pod_line(script: str) -> str:
    pods = [line for line in script.splitlines() if "umrs.server.step_worker" in line]
    assert len(pods) == 1
    return pods[0]


GATED_MOUNT_SPECS = ("in:/work/in:ro", "out:/work/out:rw", "reply:/work/reply:rw", "src:/src:ro",
                     "input.json:/work/input.json:ro")  # fmt: skip


def test_job_script_runs_the_signed_image_id_and_entrypoint(world: World) -> None:
    _body, script = _script(world)
    image_id = world.image["image_id"]
    wanted = [
        f"--entrypoint python {image_id} -m umrs.server.step_worker",
        *(f"/scratch/u/wd/{mount}" for mount in GATED_MOUNT_SPECS),
        *(f"-e {env}" for env in (*g.GATED_ENV, f"UMRS_STEP_ENTRYPOINT={ENTRY}")),
        "--network=none",
        "--pull=never",
    ]
    assert _missing(_pod_line(script), wanted) == []
    trusted = world.trusted["image_id"]
    ensured = [
        f"_gate_ensure_image {image_id} {world.image['archive_sha256']}",
        f"_gate_ensure_image {trusted} {world.trusted['archive_sha256']}",
        f"--entrypoint python {trusted} -I -c",
    ]
    assert _missing(script, ensured) == []


@pytest.mark.parametrize(
    ("partition", "over", "expected"),
    [
        ({"gpu_request": "gres", "gres": ""}, {"gpus": 2}, ["--gres=gpu:2"]),
        ({"gpu_request": "gres", "gres": "gpu:h100:1"}, {"gpus": 1}, ["--gres=gpu:h100:1"]),
        ({"gpu_request": "", "gpu_flag": "gpus"}, {"gpus": 2}, ["--gpus=2"]),
        ({"gpu_request": "", "gpu_flag": "gres"}, {"gpus": 3}, ["--gres=gpu:3"]),
        ({"gpu_request": "none"}, {"gpus": 1}, []),
        ({"exclusive": True}, {}, ["--exclusive"]),
        ({}, {"mem": "16G"}, []),
    ],
)
def test_job_script_gpu_and_memory_directives(
    world: World, partition: dict[str, Any], over: dict[str, Any], expected: list[str]
) -> None:
    world.profile["partitions"]["gpu"].update(partition)
    _body, script = _script(world, **over)
    lines = _directives(script)
    extra = [d for d in lines if d.startswith(("--gres", "--gpus", "--exclusive"))]
    assert extra == expected
    assert ("--mem=16G" in lines) == (over.get("mem") == "16G")
    assert {d.split("=")[0] for d in lines} <= set(g.SBATCH_OPTIONS)


def test_job_script_forwarder_only_with_an_endpoint(world: World) -> None:
    _body, plain = _script(world)
    assert _missing(plain, ["step_endpoint_forwarder", "UMRS_STEP_ENDPOINT_SOCKET"]) == [
        "step_endpoint_forwarder",
        "UMRS_STEP_ENDPOINT_SOCKET",
    ]
    endpoint = {"host": "gpu-node-1", "port": 8001, "kind": "bentoml"}
    _body, script = _script(world, endpoint=endpoint)
    fwd = [line for line in script.splitlines() if "step_endpoint_forwarder" in line][0]
    wanted = ["--network=host", "/scratch/u/wd/endpoint.json:/umrs-endpoint.json:ro",
              world.trusted["image_id"], "--kind bentoml", "--max-total-bytes 16777216",
              "--upstream gpu-node-1:8001"]  # fmt: skip
    assert _missing(fwd, wanted) == []


@pytest.mark.parametrize(
    ("form", "device"),
    [
        ("cdi", "--device nvidia.com/gpu=all"),
        ("rocm", "--device /dev/kfd"),
        ("nvidia-devices", '"${_step_gpu[@]}"'),
    ],
)
def test_job_script_gpu_devices(world: World, form: str, device: str) -> None:
    world.profile["gpu"]["form"] = form
    _body, script = _script(world, gpus=1)
    pod = [line for line in script.splitlines() if "umrs.server.step_worker" in line][0]
    assert device in pod
    assert ("_step_gpu=()" in script) == (form == "nvidia-devices")


@pytest.mark.parametrize("variant", ["plain", "gpu", "endpoint", "cgroups_disabled", "backend"])
def test_job_script_is_valid_bash(world: World, variant: str) -> None:
    over: dict[str, Any] = {}
    if variant == "gpu":
        world.profile["gpu"]["form"] = "nvidia-devices"
        over = {"gpus": 1}
    elif variant == "endpoint":
        over = {"endpoint": {"host": "h", "port": 1, "kind": "vllm"}}
    elif variant == "cgroups_disabled":
        world.profile["podman"].update(cgroups="disabled", nproc_limit=64)
    elif variant == "backend":
        world.profile["venue"]["network_backend"] = "netavark"
    _body, script = _script(world, **over)
    proc = subprocess.run(["bash", "-n"], input=script.encode(), capture_output=True)
    assert proc.returncode == 0, proc.stderr


def test_render_job_script_refuses_unsafe_values(world: World) -> None:
    body = world.intent([world.step("a")])
    for wd_path in ("relative/wd", "/a b/wd", "/a/$(id)/wd", "/a/../b"):
        with pytest.raises(ValueError):
            g.render_job_script(body, "a", world.profile, wd_path, LOGIN, "/home/u")
    with pytest.raises(ValueError):
        g.render_job_script(body, "a", world.profile, "/w", "bad login", "/home/u")
    with pytest.raises(g.ContractError):
        g.render_job_script(body, "zz", world.profile, "/w", LOGIN, "/home/u")


def _without(entries: Any, first: str) -> list[Any]:
    return [entry for entry in entries if entry[0] != first]


def test_shared_vector_with_the_staged_lane_constants() -> None:
    sse = pytest.importorskip("umrs.server.slurm_step_executor")
    posture = (g.POD_POSTURE, g.POD_BASE_ENV, g.SECCOMP_FILTER_PY)
    assert posture == (sse._POD_POSTURE, sse._POD_BASE_ENV, sse.SECCOMP_FILTER_PY)
    assert _without(sse._STAGED_MOUNTS, "input.pkl") == _without(g.GATED_MOUNTS, "input.json")
    assert ("input.json", "/work/input.json", "ro") in g.GATED_MOUNTS
    staged_env = [e for e in sse._STAGED_ENV if not e.startswith("UMRS_STEP_INPUT=")]
    assert staged_env == list(g.GATED_ENV[1:])
    endpoint = (g.ENDPOINT_SOCKET_ENV, g.ENDPOINT_SOCKET_PATH, g.ENDPOINT_CREDENTIAL_FILE)
    assert endpoint == (
        sse.ENDPOINT_SOCKET_ENV,
        sse.ENDPOINT_SOCKET_PATH,
        sse.ENDPOINT_CREDENTIAL_FILE,
    )


def test_shared_vector_with_the_staged_lane_script(world: World) -> None:
    sse = pytest.importorskip("umrs.server.slurm_step_executor")
    _body, script = _script(world)
    wanted = [*sse._POD_POSTURE, *(f"-e {e}" for e in sse._POD_BASE_ENV)]
    assert _missing(_pod_line(script), wanted) == []


# -- running the script: fake podman, real bash, coreutils -----------------------

FAKE_PODMAN = r"""#!__PYTHON__
import hashlib, json, os, re, sys, tarfile

STATE = __STATE__
args = sys.argv[1:]
with open(os.path.join(STATE, "podman.jsonl"), "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args and args[0].startswith("--cgroup-manager"):
    args = args[1:]
store = os.path.join(STATE, "store")
os.makedirs(store, exist_ok=True)


def inspect(fmt, ref):
    path = os.path.join(store, ref + ".json")
    if not os.path.exists(path):
        sys.stderr.write("Error: no such image\n")
        sys.exit(125)
    labels = json.load(open(path)).get("config", {}).get("Labels") or {}
    if fmt == "{{.Id}}":
        print(ref)
    else:
        print(labels.get(re.search(r'"([^"]+)"', fmt).group(1), "<no value>"))


def load(path):
    with tarfile.open(path) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        config = tar.extractfile(manifest[0]["Config"]).read()
    open(os.path.join(store, hashlib.sha256(config).hexdigest() + ".json"), "wb").write(config)
    print("Loaded image: sha256:" + hashlib.sha256(config).hexdigest())


def run(rest):
    if "-c" in rest:
        print("{}")
    elif "/sys/fs/cgroup/pids.max" in rest:
        print([a for a in rest if a.startswith("--pids-limit=")][0].split("=")[1])
    else:
        with open(os.path.join(STATE, "pod.json"), "w") as fh:
            json.dump(rest, fh)
        print("step ran")


if args[:2] == ["image", "inspect"]:
    inspect(args[3], args[4])
elif args[:2] == ["load", "-i"]:
    load(args[2])
elif args[:1] == ["info"]:
    print(os.path.join(STATE, "seccomp.json") if "SECCOMP" in args[2] else "")
elif args[:1] == ["run"]:
    run(args[1:])
"""


@dataclass
class Node:
    """A compute node for the script: fake podman, a venue, a workdir."""

    state: Path
    bin: Path
    wd: Path
    xdg: Path

    def env(self, home: Path) -> dict[str, str]:
        return {"UMRS_GATE_CLEAN": "1", "PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(home),
                "SLURM_JOB_ID": "4242", "XDG_RUNTIME_DIR": str(self.xdg), "LANG": "C.UTF-8"}  # fmt: skip

    def podman_calls(self) -> list[list[str]]:
        path = self.state / "podman.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def node(world: World) -> Node:
    base = world.tmp / "node"
    state, bindir, wd, xdg = base / "state", base / "bin", base / "wd", base / "xdg"
    for directory in (state, bindir, xdg):
        directory.mkdir(parents=True, mode=0o700)
    (state / "seccomp.json").write_text("{}")
    podman = bindir / "podman"
    podman.write_text(
        FAKE_PODMAN.replace("__PYTHON__", sys.executable).replace("__STATE__", repr(str(state)))
    )
    podman.chmod(0o755)
    for sub in ("in", "out", "reply", "src", "log"):
        (wd / sub).mkdir(parents=True, mode=0o700)
    for directory in (base, wd):
        directory.chmod(0o700)
    (wd / "input.json").write_text("{}")
    return Node(state, bindir, wd, xdg)


def _run_script(
    world: World, node: Node, login: str = LOGIN, **over: Any
) -> subprocess.CompletedProcess[bytes]:
    body = world.intent([world.step("a", **over)])
    script = g.render_job_script(body, "a", world.profile, str(node.wd), login, str(world.home))
    path = node.wd / "job.sbatch"
    path.write_text(script)
    return subprocess.run(
        ["/bin/bash", "-p", str(path)], env=node.env(world.home), capture_output=True, timeout=120
    )


def fnmatch_names(directory: Path, pattern: str) -> list[str]:
    return sorted(path.name for path in directory.glob(pattern))


def _loads(node: Node) -> list[list[str]]:
    return [call for call in node.podman_calls() if call[:1] == ["load"]]


def test_the_job_loads_both_images_from_private_copies(world: World, node: Node) -> None:
    proc = _run_script(world, node)
    assert proc.returncode == 0, proc.stderr.decode()
    store = sorted(os.listdir(node.state / "store"))
    assert store == sorted([world.image["image_id"] + ".json", world.trusted["image_id"] + ".json"])
    copies = sorted(os.path.dirname(call[2]) for call in _loads(node))
    assert copies == [str(world.tmp / "venue")] * 2
    assert fnmatch_names(world.tmp / "venue", "umrs-image.*") == []
    again = _run_script(world, node)
    assert (again.returncode, len(_loads(node))) == (0, 2)


def test_the_job_runs_the_step_in_a_private_venue(world: World, node: Node) -> None:
    assert _run_script(world, node).returncode == 0
    pod = json.loads((node.state / "pod.json").read_text())
    assert pod[-4:] == ["python", world.image["image_id"], "-m", "umrs.server.step_worker"]
    modes = [(world.tmp / name).stat().st_mode & 0o777 for name in ("venue", "venue/storage")]
    assert modes == [0o700, 0o700]
    conf = (node.xdg / "rivault-umrs-gated-4242" / "containers" / "storage.conf").read_text()
    assert f'graphroot = "{world.tmp}/venue/storage"' in conf


def test_a_swapped_archive_is_refused_by_the_job(world: World, node: Node) -> None:
    """The operator rewrites the archive after the gate checked it: the job
    hashes its own private copy and stops before loading anything."""
    path = world.archives / f"{world.image['archive_sha256']}.tar"
    path.write_bytes(docker_archive(LABELS, "swapped")[0])
    proc = _run_script(world, node)
    assert proc.returncode == 78
    assert b"does not hash to its name" in proc.stderr
    assert not [c for c in node.podman_calls() if c[:1] == ["load"]]


def test_the_job_checks_the_labels_of_an_image_already_in_storage(world: World, node: Node) -> None:
    store = node.state / "store"
    store.mkdir()
    (store / f"{world.image['image_id']}.json").write_text(json.dumps({"config": {"Labels": {}}}))
    proc = _run_script(world, node)
    assert proc.returncode == 78 and b"lacks org.umrs.step-contract=1" in proc.stderr


@pytest.mark.parametrize("problem", ["open_mode", "symlink", "storage_link"])
def test_the_job_refuses_a_venue_it_does_not_own_privately(
    world: World, node: Node, problem: str
) -> None:
    venue = world.tmp / "venue"
    if problem == "open_mode":
        venue.mkdir(mode=0o755)
        venue.chmod(0o755)
    elif problem == "symlink":
        target = world.tmp / "planted"
        target.mkdir(mode=0o700)
        venue.symlink_to(target)
    else:
        venue.mkdir(mode=0o700)
        (venue / "storage").symlink_to(world.tmp)
    proc = _run_script(world, node)
    assert proc.returncode == 78 and b"is not a private directory" in proc.stderr
    assert not (node.state / "pod.json").exists()


def _open_dir(path: Path, mode: int = 0o777) -> Path:
    path.mkdir(parents=True)
    path.chmod(mode)
    return path


def _workdir_under(node: Node, parent: Path) -> None:
    node.wd = parent / "wd"
    for sub in ("in", "out", "reply", "src", "log"):
        (node.wd / sub).mkdir(parents=True, mode=0o700)
    node.wd.chmod(0o700)
    (node.wd / "input.json").write_text("{}")


@pytest.mark.parametrize(
    "where", ["venue_root", "storage_root", "network_config_dir", "workdir", "sticky_not_root"]
)
def test_the_job_refuses_paths_under_a_directory_others_can_change(
    world: World, node: Node, where: str
) -> None:
    """Fixer amendment 2026-11-08: a private leaf is not enough. Another
    account that can write the parent can rename the checked directory and
    put its own (storage.conf, code, inputs) in its place after the check."""
    _place_under_open_parent(world, node, where)
    proc = _run_script(world, node)
    assert proc.returncode == 78, proc.stderr.decode()
    said = (b"another account can change", b"only root or")
    assert [m for m in said if m in proc.stderr]
    used = [call for call in node.podman_calls() if call[:1] not in (["kill"], ["rm"])]
    assert not (node.state / "pod.json").exists()
    assert used == []


def _place_under_open_parent(world: World, node: Node, where: str) -> None:
    mode = 0o1777 if where == "sticky_not_root" else 0o777
    parent = _open_dir(world.tmp / "open", mode)
    if where == "workdir":
        _workdir_under(node, parent)
        return
    key = "root" if where in ("venue_root", "sticky_not_root") else where
    world.profile["venue"][key] = str(parent / ("venue" if key == "root" else where))


def test_the_job_keeps_its_runtime_dir_below_its_own_venue(world: World, node: Node) -> None:
    """No usable node runtime dir (unset, or under an open parent): the
    container configuration lives under ``<venue root>/run``, never under a
    guessable ``/tmp`` name another account could create first."""
    venue = world.tmp / "venue"
    for xdg in ("", str(_open_dir(world.tmp / "shared") / "run")):
        env = node.env(world.home)
        env["XDG_RUNTIME_DIR"] = xdg
        if xdg:
            Path(xdg).mkdir(mode=0o700)
        body = world.intent([world.step("a")])
        path = node.wd / "job.sbatch"
        path.write_text(
            g.render_job_script(body, "a", world.profile, str(node.wd), LOGIN, str(world.home))
        )
        proc = subprocess.run(["/bin/bash", "-p", str(path)], env=env, capture_output=True)
        assert proc.returncode == 0, proc.stderr.decode()
        conf = venue / "run" / "rivault-umrs-gated-4242" / "containers" / "storage.conf"
        assert f'runroot = "{venue}/run/containers"' in conf.read_text()
        assert (venue / "run").stat().st_mode & 0o777 == 0o700
        conf.unlink()


def test_the_job_refuses_another_login_and_another_arch(world: World, node: Node) -> None:
    assert _run_script(world, node, login="someone-else").returncode == 78
    world.profile["partitions"]["gpu"]["arch"] = "aarch64" if ARCH == "x86_64" else "x86_64"
    proc = _run_script(world, node)
    assert proc.returncode == 78 and b"this node is" in proc.stderr


def test_the_job_reexecs_with_a_clean_environment(world: World, tmp_path: Path) -> None:
    _body, script = _script(world)
    lines = script.splitlines()
    end = lines.index('    exec /usr/bin/env -i "${_gate_env[@]}" /bin/bash -p "$0"') + 2
    probe = tmp_path / "probe.sh"
    dump = tmp_path / "env.txt"
    marker = tmp_path / "bash_env_ran"
    probe.write_text(
        "\n".join(
            [
                *lines[:end],
                f"env > {dump}",
                f"type evil >/dev/null 2>&1 && touch {marker}",
                "exit 0",
                "",
            ]
        )
    )
    (tmp_path / "evil_env").write_text(f"touch {marker}\n")
    env = {
        "PATH": "/usr/bin:/bin", "HOME": "/elsewhere", "FOO": "bar", "BASH_ENV": str(tmp_path / "evil_env"),
        "SLURM_JOB_ID": "77", "SLURM_JOB_GPUS": "0;rm -rf", "CUDA_VISIBLE_DEVICES": "0,1",
        "XDG_RUNTIME_DIR": "/run/user/1000", "BASH_FUNC_evil%%": "() { echo pwned; }",
        "LD_PRELOAD": "/tmp/x.so", "UMRS_GATE_CLEAN": "0",
    }  # fmt: skip
    proc = subprocess.run(["/bin/bash", "-p", str(probe)], env=env, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    seen = dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)
    allowed = {"UMRS_GATE_CLEAN", "PATH", "HOME", "LANG", "USER", "LOGNAME", "SLURM_JOB_ID",
               "CUDA_VISIBLE_DEVICES", "XDG_RUNTIME_DIR", "PWD", "SHLVL", "_", "OLDPWD"}  # fmt: skip
    assert set(seen) <= allowed, set(seen) - allowed
    assert (seen["PATH"], seen["HOME"], seen["SLURM_JOB_ID"]) == (g.CLEAN_PATH, "/home/u", "77")
    assert seen["CUDA_VISIBLE_DEVICES"] == "0,1" and not marker.exists()


def test_the_job_needs_a_slurm_job_id(world: World, node: Node) -> None:
    body = world.intent([world.step("a")])
    path = node.wd / "job.sbatch"
    path.write_text(
        g.render_job_script(body, "a", world.profile, str(node.wd), LOGIN, str(world.home))
    )
    env = {k: v for k, v in node.env(world.home).items() if k != "SLURM_JOB_ID"}
    assert (
        subprocess.run(["/bin/bash", "-p", str(path)], env=env, capture_output=True).returncode
        == 78
    )


# ---------------------------------------------------------------------------
# the gate as sshd runs it: a whole chained run per interpreter (python3.9 too)
# ---------------------------------------------------------------------------


def _sub_ok(result: Result) -> Any:
    assert result.code == 0, result
    return result.out


def _sub_stage_submit(
    world: World, interp: str, body: dict[str, Any], step: str, blob: bytes
) -> tuple[str, str]:
    """mkwd, stage-in and submit of one step through the gate script."""
    wd = _sub_ok(world.run_sub(interp, f"mkwd {_iid(body)} {step}"))["wd"]
    members = [(f"src/{p}", d) for p, d in sorted(SRC.items())] + [(f"in/blobs/{_sha(blob)}", blob)]
    assert _sub_ok(world.run_sub(interp, f"stage-in {wd}", _tar(members)))["files"] == 3
    job = _sub_ok(world.run_sub(interp, f"submit {wd} {step}", world.envelope(body)))["job_id"]
    return wd, job


@pytest.mark.parametrize("interp", INTERPRETERS, ids=lambda p: Path(p).name)
def test_a_chained_run_through_the_gate_script(world: World, interp: str) -> None:
    body = _two_step(world)
    data = b"input bytes"
    body["steps"][0]["inputs"]["objects"] = [obj("data/x.bin", data)]
    run = world.run_sub
    wd_a, job_a = _sub_stage_submit(world, interp, body, "a", data)
    assert _sub_ok(run(interp, f"status {job_a}"))["state"] == "PENDING"
    world.finish(wd_a, job_a, OK_REPLY, {f"{OUT_BUCKET}/o/1": b"out-1"})
    assert json.loads(run(interp, f"read-reply {wd_a}").raw) == OK_REPLY
    collected = run(interp, f"collect-out {wd_a}")
    assert (collected.code, collected.err) == (0, {"files": 1, "bytes": 5})
    assert _untar(collected.raw) == {f"{OUT_BUCKET}/o/1": b"out-1"}
    wd_b, job_b = _sub_stage_submit(world, interp, body, "b", b"out-1")
    _finish_through_the_gate_script(world, interp, body, (wd_a, job_a), (wd_b, job_b))


def _finish_through_the_gate_script(
    world: World, interp: str, body: dict[str, Any], first: tuple[str, str], second: tuple[str, str]
) -> None:
    run = world.run_sub
    assert _sub_ok(run(interp, f"lookup {_iid(body)} b"))["job_id"] == second[1]
    usage = _sub_ok(run(interp, f"usage {first[1]} {second[1]}"))["jobs"]
    assert [job["job_id"] for job in usage] == [first[1], second[1]]
    assert _sub_ok(run(interp, f"cancel {second[1]}"))["cancelled"] is True
    assert _sub_ok(run(interp, f"rmwd {first[0]}")) == {"removed": True}
    refused(run(interp, f"submit {second[0]} b", world.envelope(body)), "sealed")
    assert g.verify_log(str(world.log))


@pytest.mark.parametrize("interp", INTERPRETERS, ids=lambda p: Path(p).name)
def test_refusals_through_the_gate_script(world: World, interp: str) -> None:
    body, wd = _one_step(world)
    refused(
        world.run_sub(interp, f"submit {wd} a", world.envelope(body, key=world.keys.other)),
        "bad_signature",
    )
    refused(
        world.run_sub(interp, f"submit {wd} a", world.envelope({**body, "site": "s2"})),
        "wrong_site",
    )
    refused(world.run_sub(interp, f"stage-in {wd}", _tar([("src/../x", b"1")])), "bad_member")
    refused(world.run_sub(interp, "status 999"), "not_mine")
    assert world.run_sub(interp, "mkwd x y").code == 2
    assert world.run_sub(interp, f"rmwd {'0' * 16}-a-{'0' * 8}").code == 3
