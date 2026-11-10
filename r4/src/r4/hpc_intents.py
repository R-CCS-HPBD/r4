"""Signed intents for one gated HPC run (ADR-112 D-8, D-10; plan §5 B2.0 item 11).

``r4 workflow trigger <name> --site <s>`` on a site that runs the person's
work under their own login: UMRS proposes an intent (one workflow run at one
site), r4 recomputes everything it can from bytes it read itself, refuses
with a diff on any difference, shows the person what they are about to
approve (who chose each input), and only then signs it with the dedicated
signing key under ``umrs-intent-v1@<domain>``. The gate on the site checks
the signature, the digests and the profile again before it submits anything.

r4 never signs a digest it has only been told (D-8):

- the code manifest comes from ``GET /umrs/workflows/{name}/export?version=``,
  read in memory (never extracted: a macOS filesystem would re-normalize
  Unicode names), never from ``EXPORT.json`` or from the proposal;
- step names, ``after``, ``chained`` and each step's ``config`` come from the
  gate's own ``step_graph`` and ``expected_step_config`` over the archive's
  ``workflow.json`` and the intent's params, so the server's proposal and
  r4's recomputation cannot drift;
- login, account, principal and site come from the person's own account view
  and from what r4 recorded at ``r4 hpc add`` / ``verify``;
- the site profile sha is the one r4 installed.

Signing refuses in a Companion tool shell and without a terminal
(``hpc_signing.sign``); the command is also fenced as never-auto.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from r4_contracts import hpc_gate

from .hpc_signing import (
    HpcSigningError,
    check_domain,
    fork_refusal,
    person_refusal,
    read_site_record,
    sign,
    signing_dir,
    unambiguous_site_signer,
    update_site_record,
)

#: r4 refuses an ``issued`` further than this from its own clock.
ISSUED_SKEW_S = 300
#: Above this many objects in one step, the summary shows counts and bytes
#: per label (``--show-inputs`` lists them all).
SUMMARY_INPUT_LIMIT = 20
#: The export is read in memory: refuse a bigger body (the server caps the
#: tree at 200 MiB; the zip adds headers).
EXPORT_MAX_BYTES = 256 << 20
#: Root members of the export that are not part of the source tree (§3).
EXPORT_NOT_SOURCE = ("workflow.json", "EXPORT.json")
#: Root of the person's own signed-intent ids (the witness compares the log
#: with these).
SIGNED_DIR_NAME = "signed"

LABEL_LISTED = "chosen by you"
LABEL_SERVER = "data chosen by UMRS"

#: ADR-112 D-10: the claim, the qualifier and the residuals. Exact for what
#: is pinned (fixer finding 2026-11-08): listed inputs at signing, outputs of
#: earlier steps when the gate collects them.
D10_TEXT = (
    "UMRS stores nothing that can act as you without your signature. Signing "
    "approves this one run as shown: the code and every listed input are "
    "pinned by digest, outputs of earlier steps are pinned by your gate when "
    "it collects them, and inputs marked 'data chosen by UMRS' were chosen by "
    "UMRS, not by you. Outputs that pass through UMRS are visible to its "
    "operators, and the same team publishes r4 and the gate."
)
#: Added whenever a step reads a model endpoint: its answers are live.
D10_ENDPOINT_TEXT = (
    "Steps marked 'endpoint' also read answers from a model endpoint UMRS "
    "chose; those answers are not pinned."
)
LABEL_ENDPOINT = "chosen by UMRS, answers not pinned"

_DIFF_CAP = 12
_SHOWN_CAP = 120
_UTF8_NAME_FLAG = 0x800
_ENCRYPTED_FLAG = 0x1
_SUBMIT_INTENT_RE = re.compile(r"\bintent=([0-9a-f]{64})\b")
_GRANT_ID_RE = re.compile(r"[0-9a-f]{32}")
_MISSING = object()


class IntentRefused(HpcSigningError):
    """r4 will not sign: what the proposal says differs from what r4 computed."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        body = "\n".join(f"  - {line}" for line in self.problems)
        super().__init__(f"refused to sign (nothing was signed):\n{body}")


def _shared(what: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a shared gate function; its refusal is r4's refusal."""
    try:
        return fn(*args, **kwargs)
    except (ValueError, TypeError, KeyError, hpc_gate.GateError) as exc:
        raise IntentRefused([f"{what}: {exc}"]) from exc


def _canon(value: Any) -> str:
    """Exact comparison text: ``1``, ``1.0`` and ``true`` stay distinct."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def shown(value: Any, cap: int = _SHOWN_CAP) -> str:
    """Server-supplied text for the terminal: no control or format characters."""
    text = "".join(c if c.isprintable() else "?" for c in str(value))
    return text if len(text) <= cap else text[: cap - 1] + "…"


# ── the proposal ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Proposal:
    """A proposed intent: the exact bytes to sign, parsed and validated."""

    payload: bytes
    intent: dict[str, Any]
    intent_id: str


def _proposal_bytes(answer: Any) -> bytes:
    raw = answer.get("intent_b64") if isinstance(answer, dict) else None
    if not isinstance(raw, str):
        raise IntentRefused(["the proposal carries no intent_b64"])
    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IntentRefused(["the proposed intent is not base64"]) from exc
    if len(payload) > hpc_gate.INTENT_MAX_BYTES:
        raise IntentRefused([f"the proposed intent is over {hpc_gate.INTENT_MAX_BYTES} bytes"])
    return payload


def parse_proposal(answer: Any) -> Proposal:
    """Strict-parse and validate ``intent_b64`` with the gate's own code
    (``parse_strict(..., json_values=True)``, then ``validate_intent``)."""
    payload = _proposal_bytes(answer)
    intent = _shared("the proposed intent", hpc_gate.parse_strict, payload, json_values=True)
    _shared("the proposed intent", hpc_gate.validate_intent, intent)
    ident = hpc_gate.intent_id(payload)
    if answer.get("intent_id") not in (None, ident):
        raise IntentRefused(["the proposal's intent_id is not the sha256 of its bytes"])
    return Proposal(payload=payload, intent=intent, intent_id=ident)


# ── the code: the export, read in memory (§3) ───────────────────────────────


def is_normal_key(value: Any) -> bool:
    """True iff ``value`` is a fixed point of UMRS ``staged_s3.normalize_key``
    (the gate's own rule, shared so r4 and the gate cannot disagree)."""
    return bool(hpc_gate.is_normal_key(value))


def _kind_problem(info: zipfile.ZipInfo) -> Optional[str]:
    kind = stat.S_IFMT(info.external_attr >> 16)  # 0: the archiver recorded no type
    if info.is_dir():
        return "is a directory entry"
    if kind and kind != stat.S_IFREG:
        return "is not a regular file (a symlink or a special file)"
    if info.flag_bits & _ENCRYPTED_FLAG:
        return "is encrypted"
    return None


def _name_problem(info: zipfile.ZipInfo, seen: dict[str, bytes]) -> Optional[str]:
    name = info.filename
    if info.orig_filename != name or not (name.isascii() or info.flag_bits & _UTF8_NAME_FLAG):
        return "has a name that does not decode unambiguously"
    if name in seen:
        return "is a duplicate name"
    if not is_normal_key(name):
        return "is not a normalized path"
    return None


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Optional[bytes]:
    """The member's bytes, or None when the read size differs from its header."""
    try:
        with archive.open(info) as handle:
            body = handle.read(info.file_size + 1)
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error) as exc:
        problem = f"export member {shown(info.filename)!r} is unreadable: {exc}"
        raise IntentRefused([problem]) from exc
    return body if len(body) == info.file_size else None


def _read_members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if sum(info.file_size for info in infos) > EXPORT_MAX_BYTES:
        raise IntentRefused([f"the export unpacks to more than {EXPORT_MAX_BYTES} bytes"])
    members: dict[str, bytes] = {}
    problems: list[str] = []
    for info in infos:
        problem = _kind_problem(info) or _name_problem(info, members)
        body = None if problem else _read_member(archive, info)
        if body is None:
            problems.append(
                f"export member {shown(info.filename)!r} "
                f"{problem or 'has a read size that differs from its header'}"
            )
            continue
        members[info.filename] = body
    if problems:
        raise IntentRefused(problems)
    return members


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not allowed")


def _parse_workflow(raw: Optional[bytes]) -> dict[str, Any]:
    if raw is None:
        raise IntentRefused(["the export has no root workflow.json"])
    try:
        text = raw.decode("utf-8")
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise IntentRefused([f"the export's workflow.json is not strict JSON: {exc}"]) from exc
    workflow: dict[str, Any] = obj if isinstance(obj, dict) else {}
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        raise IntentRefused(["the export's workflow.json has no list of step objects"])
    return workflow


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _tree_problems(paths: list[str]) -> list[str]:
    """A path that is also a directory of another cannot be extracted."""
    directories = {d for path in paths for d in _ancestors(path)}
    return [
        f"export member {shown(p)!r} is both a file and a directory"
        for p in paths
        if p in directories
    ]


def file_entry(path: str, data: bytes) -> dict[str, Any]:
    """One ``src_manifest.files`` entry of bytes r4 read itself (the gate's
    ``src_file_entry``, so the digest form is the one the gate compares)."""
    return dict(_shared(f"export member {shown(path)!r}", hpc_gate.src_file_entry, path, data))


@dataclass(frozen=True)
class ExportTree:
    """The source tree of one workflow version, as r4 hashed it."""

    files: list[dict[str, Any]]
    workflow: dict[str, Any]


def read_export(data: bytes) -> ExportTree:
    """Read the export zip in memory: refuse a directory entry, a duplicate
    name, a name that is not a ``normalize_key`` fixed point, a symlink or
    non-regular entry, or a member whose read size differs from its header;
    then hash every member except the root ``workflow.json`` and
    ``EXPORT.json``, sorted by UTF-8 bytes."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise IntentRefused([f"the export is not a zip archive: {exc}"]) from exc
    with archive:
        members = _read_members(archive)
    workflow = _parse_workflow(members.get("workflow.json"))
    sources = {p: b for p, b in members.items() if p not in EXPORT_NOT_SOURCE}
    paths = sorted(sources, key=lambda p: p.encode("utf-8"))
    problems = _tree_problems(paths)
    if problems:
        raise IntentRefused(problems)
    return ExportTree(files=[file_entry(p, sources[p]) for p in paths], workflow=workflow)


# ── what the steps must be (§1.3), recomputed ───────────────────────────────


@dataclass(frozen=True)
class ExpectedStep:
    name: str
    after: list[str]
    chained: list[str]
    config: dict[str, Any]
    entrypoint: Any
    prefixes: list[str]
    authored_config: dict[str, Any] = field(default_factory=dict)


def _prefix_entry(item: Any, input_bucket: str) -> str:
    """``s3://<b>/<p>`` → ``<b>/<p>``; a bare prefix lives in the input bucket."""
    if not isinstance(item, str):
        raise IntentRefused([f"stage_inputs.prefixes entry {shown(item)!r} is not a string"])
    if item.startswith("s3://"):
        bucket, _, key = item[len("s3://") :].partition("/")
        return f"{bucket}/{key}"
    return f"{input_bucket}/{item}"


def step_prefixes(workflow_step: dict[str, Any], input_bucket: str) -> list[str]:
    """The step's declared ``stage_inputs.prefixes`` as ``<bucket>/<prefix>``,
    deduplicated and strictly increasing."""
    block = workflow_step.get("stage_inputs")
    raw = block.get("prefixes") if isinstance(block, dict) else None
    entries = {_prefix_entry(item, input_bucket) for item in raw or []}
    return sorted(entries, key=lambda s: s.encode("utf-8"))


def _node(node: Any) -> tuple[str, list[str], list[str]]:
    if isinstance(node, dict):
        return str(node["name"]), list(node["after"]), list(node["chained"])
    name, after, chained = node
    return str(name), list(after), list(chained)


def expected_steps(
    tree: ExportTree, params: dict[str, Any], input_bucket: str
) -> list[ExpectedStep]:
    """Names, ``after``, ``chained``, ``config``, entrypoint and prefixes of
    every step, from the archive's ``workflow.json`` and the intent's params."""
    steps = tree.workflow["steps"]
    graph = list(_shared("the export's workflow.json", hpc_gate.step_graph, steps))
    if len(graph) != len(steps):
        raise IntentRefused(["step_graph does not give one node per step"])
    out = []
    for raw, node in zip(steps, graph):
        name, after, chained = _shared("the export's workflow.json", _node, node)
        config = _shared(f"step {name} config", hpc_gate.expected_step_config, raw, name, params)
        authored = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        prefixes = step_prefixes(raw, input_bucket)
        out.append(
            ExpectedStep(name, after, chained, config, raw.get("entrypoint"), prefixes, authored)
        )
    return out


# ── comparison (§11 step 5) ─────────────────────────────────────────────────


def _brief(value: Any) -> str:
    return shown(_canon(value), 100)


def _file_diff(path: str, proposed: Any, local: Any) -> str:
    if proposed is None:
        return f"    {shown(path)}: in the export, missing from the proposal"
    if local is None:
        return f"    {shown(path)}: in the proposal, not in the export"
    return f"    {shown(path)}: proposed {proposed}, the export has {local}"


def _capped(head: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    more = [f"    … and {len(lines) - _DIFF_CAP} more"] if len(lines) > _DIFF_CAP else []
    return [head + "\n" + "\n".join(lines[:_DIFF_CAP] + more)]


def _by_path(files: Any) -> dict[str, tuple[Any, Any]]:
    return {e["path"]: (e["sha256"], e["size"]) for e in files}


def manifest_problems(proposed: list[dict[str, Any]], local: list[dict[str, Any]]) -> list[str]:
    """``src_manifest.files`` against the export r4 hashed itself."""
    a, b = _by_path(proposed), _by_path(local)
    lines = [
        _file_diff(path, a.get(path), b.get(path))
        for path in sorted(set(a) | set(b), key=lambda p: p.encode("utf-8"))
        if _canon(a.get(path)) != _canon(b.get(path))
    ]
    if not lines and _canon(proposed) != _canon(local):
        lines = ["    the entries are not in the export's order or form"]
    return _capped("src_manifest differs from the export r4 hashed:", lines)


def code_problems(intent: dict[str, Any], tree: ExportTree, steps: list[ExpectedStep]) -> list[str]:
    manifest = intent["src_manifest"]
    problems = manifest_problems(manifest["files"], tree.files)
    wanted = {step.name: step.entrypoint for step in steps}
    if _canon(manifest["entrypoints"]) != _canon(wanted):
        problems.append(
            f"entrypoints: proposed {_brief(manifest['entrypoints'])}, "
            f"workflow.json gives {_brief(wanted)}"
        )
    return problems


def _slot(mapping: dict[str, Any], key: str) -> str:
    return _brief(mapping[key]) if key in mapping else "<absent>"


def _config_problem(name: str, got: Any, want: dict[str, Any]) -> list[str]:
    if _canon(got) == _canon(want):
        return []
    got = got if isinstance(got, dict) else {}
    lines = [
        f"    {shown(k)}: proposed {_slot(got, k)}, r4 computed {_slot(want, k)}"
        for k in sorted(set(got) | set(want))
        if _slot(got, k) != _slot(want, k)
    ]
    return _capped(f"step {name} config differs from what r4 computed:", lines)


def _step_problems(step: dict[str, Any], want: ExpectedStep) -> list[str]:
    inputs = step["inputs"]
    pairs = (
        ("after", step["after"], want.after),
        ("chained", inputs["chained"], want.chained),
        ("prefixes", inputs["prefixes"], want.prefixes),
    )
    problems = [
        f"step {want.name} {label}: proposed {_brief(got)}, r4 computed {_brief(exp)}"
        for label, got, exp in pairs
        if _canon(got) != _canon(exp)
    ]
    return problems + _config_problem(want.name, step["config"], want.config)


def step_problems(intent: dict[str, Any], steps: list[ExpectedStep]) -> list[str]:
    """Names and order, then each step's ``after``, ``chained``, prefixes and config."""
    names = [step["name"] for step in intent["steps"]]
    wanted = [step.name for step in steps]
    if names != wanted:
        return [f"steps: proposed {_brief(names)}, workflow.json gives {_brief(wanted)}"]
    problems: list[str] = []
    for step, want in zip(intent["steps"], steps):
        problems += _step_problems(step, want)
    return problems


def params_problems(proposed: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """The params you passed plus exactly ``project_id`` and ``input_files``."""
    problems = []
    for key in sorted(set(proposed) | set(expected)):
        got, want = proposed.get(key, _MISSING), expected.get(key, _MISSING)
        if got is _MISSING:
            problems.append(f"params.{shown(key)}: missing from the proposal")
        elif want is _MISSING:
            problems.append(f"params.{shown(key)}: added by the server ({_brief(got)})")
        elif _canon(got) != _canon(want):
            problems.append(
                f"params.{shown(key)}: proposed {_brief(got)}, you passed {_brief(want)}"
            )
    return problems


@dataclass(frozen=True)
class ListedInput:
    """An input r4 hashed itself (``mode: listed``, "chosen by you")."""

    step: str
    bucket: str
    key: str
    sha256: str
    size: int

    def body(self) -> dict[str, Any]:
        return {"bucket": self.bucket, "key": self.key, "sha256": self.sha256, "size": self.size}


def listed_input(step: str, uri: str, path: Path) -> ListedInput:
    """``--listed-input STEP s3://bucket/key FILE``: r4 hashes FILE itself."""
    bucket, sep, key = (
        uri[len("s3://") :].partition("/") if uri.startswith("s3://") else ("", "", "")
    )
    if not (sep and bucket and is_normal_key(key)):
        raise HpcSigningError(f"--listed-input: {uri!r} is not s3://<bucket>/<normalized key>")
    sha256, size = file_digest(path)
    return ListedInput(step, bucket, key, sha256, size)


def file_digest(path: Path) -> tuple[str, int]:
    """``(sha256, size)`` of a local file r4 reads itself (``--listed-input``)."""
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise HpcSigningError(f"--listed-input: cannot read {path}: {exc}") from exc
    return digest.hexdigest(), size


def _object_problem(
    step: str, obj: dict[str, Any], mine: dict[tuple[str, str, str], ListedInput]
) -> Optional[str]:
    where = f"step {step} input s3://{shown(obj['bucket'])}/{shown(obj['key'])}"
    entry = mine.get((step, obj["bucket"], obj["key"]))
    if entry is None:
        return (
            f"{where} is labelled '{LABEL_LISTED}' but r4 did not list it"
            if obj["mode"] == "listed"
            else None
        )
    if obj["mode"] != "listed":
        return f"{where} was listed by you but relabelled {obj['mode']!r}"
    if (obj["sha256"], obj["size"]) != (entry.sha256, entry.size):
        return (
            f"{where}: proposed sha256 {obj['sha256'][:12]}… size {obj['size']}, "
            f"r4 hashed {entry.sha256[:12]}… size {entry.size}"
        )
    return None


def listed_problems(intent: dict[str, Any], listed: Iterable[ListedInput]) -> list[str]:
    """Your own ``listed`` entries unchanged and still labelled yours; no other
    entry labelled as chosen by you."""
    mine = {(e.step, e.bucket, e.key): e for e in listed}
    seen = set()
    problems = []
    for step in intent["steps"]:
        for obj in step["inputs"]["objects"]:
            seen.add((step["name"], obj["bucket"], obj["key"]))
            problem = _object_problem(step["name"], obj, mine)
            if problem:
                problems.append(problem)
    missing = sorted(set(mine) - seen)
    return problems + [
        f"step {s} input s3://{b}/{k} you listed is missing from the proposal"
        for s, b, k in missing
    ]


def time_problems(intent: dict[str, Any], now: float, queue_seconds: Optional[int]) -> list[str]:
    """The window, ``issued`` against r4's clock, and the ``max_run_seconds`` bound."""
    issued, expires = intent["issued"], intent["expires"]
    problems = []
    if not 0 < expires - issued <= hpc_gate.INTENT_MAX_WINDOW:
        problems.append(
            f"expires - issued is {expires - issued} s (at most {hpc_gate.INTENT_MAX_WINDOW})"
        )
    if abs(issued - now) > ISSUED_SKEW_S:
        problems.append(
            f"issued is {int(issued - now)} s from this machine's clock (at most ±{ISSUED_SKEW_S})"
        )
    if expires <= now:
        problems.append("the intent has already expired")
    if queue_seconds is None:
        return problems + ["no site profile installed by this r4: run `r4 hpc profile <site>`"]
    bound = sum(step["time"] for step in intent["steps"]) + len(intent["steps"]) * queue_seconds
    if intent["max_run_seconds"] > bound:
        problems.append(
            f"max_run_seconds {intent['max_run_seconds']} is above {bound} "
            "(the step times plus the queue time per step)"
        )
    return problems


@dataclass(frozen=True)
class Expectations:
    """What r4 knows itself, independently of the proposal."""

    principal: Any
    venue: Any
    login: Any
    account: Any
    project: Any
    workflow: str
    version: Optional[str]
    params: dict[str, Any]
    listed: tuple[ListedInput, ...]
    record: dict[str, Any]
    queue_seconds: Optional[int]
    now: float


def _identity_checks(intent: dict[str, Any], exp: Expectations) -> list[tuple[str, Any, Any, str]]:
    checks = [
        ("principal", intent["principal"], exp.principal, "your UMRS user"),
        ("site", intent["site"], exp.venue, "this site's venue"),
        ("site", intent["site"], exp.record.get("venue"), "the site r4 recorded at `r4 hpc add`"),
        ("login", intent["login"], exp.login, "the login the site verified for you"),
        (
            "login",
            intent["login"],
            exp.record.get("login"),
            "the login r4 recorded at `r4 hpc add`/`verify`",
        ),
        ("account", intent["account"], exp.account, "your active link for this project"),
        ("project", intent["project"], exp.project, "the project you asked for"),
        ("workflow", intent["workflow"], exp.workflow, "the workflow you asked for"),
    ]
    if exp.version:
        checks.append(("version", intent["version"], exp.version, "the version you asked for"))
    return checks


def identity_problems(intent: dict[str, Any], exp: Expectations) -> list[str]:
    problems = [
        f"{label}: proposed {shown(got)!r}, expected {shown(want)!r} ({why})"
        for label, got, want, why in _identity_checks(intent, exp)
        if got != want
    ]
    installed = exp.record.get("site_profile_sha256")
    if intent["site_profile_sha256"] != installed:
        problems.append(
            "site_profile_sha256 is not the profile this r4 installed: "
            "run `r4 hpc profile <site>`, then trigger again"
        )
    return problems


def all_problems(
    proposal: Proposal, tree: ExportTree, steps: list[ExpectedStep], exp: Expectations
) -> list[str]:
    intent = proposal.intent
    return (
        identity_problems(intent, exp)
        + params_problems(intent["params"], exp.params)
        + code_problems(intent, tree, steps)
        + step_problems(intent, steps)
        + listed_problems(intent, exp.listed)
        + time_problems(intent, exp.now, exp.queue_seconds)
    )


# ── the summary the person signs (§11 step 6, D-10) ─────────────────────────


def duration(seconds: int) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes = rest // 60
    days, hours = divmod(hours, 24)
    text = f"{hours}h {minutes:02d}m"
    return f"{days}d {text}" if days else text


def _image(image: dict[str, Any]) -> str:
    return f"{shown(image['ref'])} (id {shown(image['image_id'])[:12]})"


def _size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _label(obj: dict[str, Any]) -> str:
    return LABEL_LISTED if obj["mode"] == "listed" else LABEL_SERVER


def _object_line(obj: dict[str, Any]) -> str:
    return (
        f"      s3://{shown(obj['bucket'])}/{shown(obj['key'])}  {_size(obj['size'])}  "
        f"sha256 {obj['sha256'][:12]}  [{_label(obj)}]"
    )


def _counted(objects: list[dict[str, Any]]) -> list[str]:
    totals: dict[str, list[int]] = {}
    for obj in objects:
        count = totals.setdefault(_label(obj), [0, 0])
        count[0] += 1
        count[1] += obj["size"]
    lines = [
        f"      {n} objects, {_size(b)}  [{label}]" for label, (n, b) in sorted(totals.items())
    ]
    return lines + ["      (--show-inputs lists every one)"]


def _input_lines(step: dict[str, Any], show_inputs: bool) -> list[str]:
    inputs = step["inputs"]
    objects = inputs["objects"]
    many = len(objects) > SUMMARY_INPUT_LIMIT and not show_inputs
    lines = _counted(objects) if many else [_object_line(obj) for obj in objects]
    lines += [
        f"      outputs of step {shown(p)}  [outputs of step {shown(p)}]" for p in inputs["chained"]
    ]
    lines += [f"      may list under {shown(p)}" for p in inputs["prefixes"]]
    return ["    inputs" + ("" if lines else "      none")] + lines


def _network(step: dict[str, Any]) -> str:
    endpoint = step.get("endpoint")
    if not endpoint:
        return "no network"
    return (
        f"endpoint {shown(endpoint['host'])}:{endpoint['port']} ({shown(endpoint['kind'])})"
        f"  [{LABEL_ENDPOINT}]"
    )


def _config_lines(config: dict[str, Any], authored: dict[str, Any]) -> list[str]:
    changed = [
        k for k in sorted(config) if k not in authored or _canon(config[k]) != _canon(authored[k])
    ]
    return [f"      {shown(k)} = {_brief(config[k])}" for k in changed]


def _step_lines(
    index: int,
    total: int,
    step: dict[str, Any],
    intent: dict[str, Any],
    authored: dict[str, Any],
    show_inputs: bool,
) -> list[str]:
    mem = step["mem"] or "site default"
    lines = [
        f"  step {index}/{total} {shown(step['name'])}",
        f"    entrypoint  {shown(intent['src_manifest']['entrypoints'][step['name']])}",
        f"    image       {_image(step['image'])}",
        f"    resources   partition {shown(step['partition'])}, cpus {step['cpus']}, "
        f"mem {shown(mem)}, gpus {step['gpus']}, time {duration(step['time'])}",
        f"    network     {_network(step)}",
    ]
    config = _config_lines(step["config"], authored)
    if config:
        lines += ["    config (differs from workflow.json)", *config]
    return lines + _input_lines(step, show_inputs)


def _local_time(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _header(intent: dict[str, Any], intent_id: str) -> list[str]:
    return [
        "You are asked to sign one run:",
        f"  site        {shown(intent['site'])}   login {shown(intent['login'])}   "
        f"account {shown(intent['account'])}",
        f"  project     {shown(intent['project'])}",
        f"  workflow    {shown(intent['workflow'])}@{shown(intent['version'])}   "
        f"(source sha256 {shown(intent['source_sha256'])[:16]})",
        f"  run         {shown(intent['run_id'])}   intent {intent_id}",
        f"  expires     {_local_time(intent['expires'])} (the first step must start before then)",
        f"  later steps may still start up to {duration(intent['max_run_seconds'])} "
        "after the first submit",
        f"  platform image {_image(intent['trusted_image'])} "
        "(sandbox setup and the endpoint forwarder)",
    ]


def render_summary(
    proposal: Proposal, steps: list[ExpectedStep], *, show_inputs: bool = False
) -> str:
    """Everything signed that a person can judge, and who chose each input."""
    intent = proposal.intent
    lines = _header(intent, proposal.intent_id)
    authored = {step.name: step.authored_config for step in steps}
    total = len(intent["steps"])
    for index, step in enumerate(intent["steps"], 1):
        lines += _step_lines(
            index, total, step, intent, authored.get(step["name"], {}), show_inputs
        )
    return "\n".join(lines + ["", d10_text(intent)])


def d10_text(intent: dict[str, Any]) -> str:
    """The D-10 sentence for this intent: the endpoint qualifier whenever a
    step reads a model endpoint, never otherwise."""
    if any(step.get("endpoint") for step in intent["steps"]):
        return f"{D10_TEXT} {D10_ENDPOINT_TEXT}"
    return D10_TEXT


# ── the request, signing and the trigger ────────────────────────────────────


@dataclass(frozen=True)
class TriggerRequest:
    """What the person asked for on the command line."""

    workflow: str
    version: Optional[str]
    params: dict[str, Any]
    input_files: list[str]
    project: Optional[str]
    listed: tuple[ListedInput, ...] = ()
    site: Optional[str] = None
    yes: bool = False
    show_inputs: bool = False


def split_name(name: str) -> tuple[str, Optional[str]]:
    """``name@ver`` → ``(name, ver)``; a bare name → ``(name, None)``."""
    base, sep, version = name.rpartition("@")
    return (base, version) if sep and base and version else (name, None)


def expected_params(request: TriggerRequest, project: str) -> dict[str, Any]:
    params = dict(request.params)
    if request.input_files:
        params["input_files"] = list(request.input_files)
    params["project_id"] = project
    return params


def proposal_body(request: TriggerRequest) -> dict[str, Any]:
    """``{project_id, version?, params, inputs?}`` (§10.3)."""
    params = dict(request.params)
    if request.input_files:
        params["input_files"] = list(request.input_files)
    body: dict[str, Any] = {"project_id": request.project, "params": params}
    if request.version:
        body["version"] = request.version
    inputs: dict[str, list[dict[str, Any]]] = {}
    for entry in request.listed:
        inputs.setdefault(entry.step, []).append(entry.body())
    if inputs:
        body["inputs"] = inputs
    return body


def trigger_body(proposal: Proposal, signature: str) -> dict[str, Any]:
    """The existing trigger body plus ``intent`` and ``signature``; the params
    are the intent's own, so the server's equality check passes (§10.3)."""
    intent = proposal.intent
    return {
        "intent": base64.b64encode(proposal.payload).decode("ascii"),
        "signature": signature,
        "version": intent["version"],
        "params": intent["params"],
        "project_id": intent["project"],
    }


def signed_dir() -> Path:
    return signing_dir().parent / SIGNED_DIR_NAME


def record_signed(intent_id: str, site: str) -> None:
    """Keep the id of every intent this r4 signed (the witness flags others)."""
    folder = signed_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    fd = os.open(folder / intent_id, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps({"site": site, "signed_at": int(time.time())}) + "\n")


def signed_ids() -> set[str]:
    try:
        return {p.name for p in signed_dir().iterdir() if re.fullmatch(r"[0-9a-f]{64}", p.name)}
    except OSError:
        return set()


def sign_intent(proposal: Proposal, *, site: str, domain: str) -> str:
    """Sign the exact payload with the key the site's gate trusts, under
    ``umrs-intent-v1@<domain>``; the key enforces the touch or PIN."""
    namespace = _shared("the signing domain", hpc_gate.namespace, "intent", check_domain(domain))
    _, key = unambiguous_site_signer(site)
    signature = sign(proposal.payload, namespace, key)
    record_signed(proposal.intent_id, site)
    return signature


def _active_link(links: Any, project: Optional[str]) -> Optional[str]:
    for link in links if isinstance(links, list) else []:
        if (
            isinstance(link, dict)
            and link.get("project_id") == project
            and link.get("status") == "active"
        ):
            return link.get("slurm_account")
    return None


def _account_view(client: Any) -> tuple[dict[str, Any], list[Any]]:
    view = client.hpc_account() or {}
    account, links = view.get("account"), view.get("links")
    return (account if isinstance(account, dict) else {}), (
        links if isinstance(links, list) else []
    )


def gather_expectations(
    client: Any, request: TriggerRequest, site: str, now: Optional[float] = None
) -> Expectations:
    """r4's own view: the account view, ``site_me``, the site record."""
    from .hpc_site_profile import queue_seconds

    account, links = _account_view(client)
    active = account.get("status") == "active"
    project = str(request.project)
    return Expectations(
        principal=(client.site_me() or {}).get("user_id"),
        venue=account.get("venue"),
        login=account.get("verified_login") if active else None,
        account=_active_link(links, project),
        project=project,
        workflow=request.workflow,
        version=request.version,
        params=expected_params(request, project),
        listed=tuple(request.listed),
        record=read_site_record(site),
        queue_seconds=queue_seconds(site),
        now=time.time() if now is None else now,
    )


def site_key(client: Any, request: TriggerRequest) -> str:
    """The site record to use: ``--site``, else this gateway's venue."""
    if request.site:
        return request.site
    venue = _account_view(client)[0].get("venue")
    if not isinstance(venue, str) or not venue:
        raise HpcSigningError("no HPC account at this site: run `r4 hpc add <site>` first.")
    return venue


def prepare(client: Any, request: TriggerRequest, site: str) -> tuple[Proposal, list[ExpectedStep]]:
    """Ask for the proposal, recompute, compare; refuse with a diff."""
    if not request.project:
        raise HpcSigningError("a gated run needs a project: pass --project.")
    proposal = parse_proposal(
        client.workflow_intent_proposal(request.workflow, proposal_body(request))
    )
    intent = proposal.intent
    data = client.workflows.export_bytes(
        request.workflow, version=intent["version"], max_bytes=EXPORT_MAX_BYTES
    )
    tree = read_export(data)
    steps = expected_steps(tree, intent["params"], intent["input_bucket"])
    problems = all_problems(proposal, tree, steps, gather_expectations(client, request, site))
    if problems:
        raise IntentRefused(problems)
    return proposal, steps


def run_gated_trigger(
    client: Any,
    request: TriggerRequest,
    *,
    confirm: Callable[[str], bool],
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """The whole §11 flow: refuse unless a person at a terminal, propose,
    recompute, compare, show, confirm, sign, trigger."""
    reason = person_refusal("trigger --site", command="r4 workflow")
    if reason:
        raise HpcSigningError(reason)
    site = site_key(client, request)
    forked = fork_refusal(site, "workflow trigger --site")
    if forked:
        raise HpcSigningError(forked)
    domain = read_site_record(site).get("domain")
    if not domain:
        raise HpcSigningError(f"r4 has no record of the gate at {site}: run `r4 hpc add {site}`.")
    proposal, steps = prepare(client, request, site)
    out(render_summary(proposal, steps, show_inputs=request.show_inputs))
    if not request.yes and not confirm("Sign this run with your signing key?"):
        raise HpcSigningError("not signed: declined.")
    signature = sign_intent(proposal, site=site, domain=domain)
    answer = client.workflow_trigger_signed(request.workflow, trigger_body(proposal, signature))
    return dict(answer or {}, intent_id=proposal.intent_id)


# ── the witness (I-9; §11 ``r4 hpc status --witness``; plan §6 D5) ─────────


@dataclass(frozen=True)
class GrantSubmit:
    """One ``submit ok grant=…`` entry of the gate log."""

    n: int
    grant_id: str
    run: str
    step: str


@dataclass(frozen=True)
class GrantSettlement:
    """One fixed-form ``settle ok grant=…`` charge receipt."""

    n: int
    grant_id: str
    run: str
    step: str
    job: str
    gpu_s: int
    core_s: int
    bound: bool


@dataclass(frozen=True)
class GrantTotal:
    """Submits, reservation and independently logged settled charge."""

    jobs: int
    gpu_s: int
    core_s: int
    charged_gpu_s: int = 0
    charged_core_s: int = 0


@dataclass(frozen=True)
class GrantBook:
    """Local grants, signed caps and cancel requests (seconds here)."""

    signed: frozenset[str] = frozenset()
    max_jobs: dict[str, int] = field(default_factory=dict)
    max_gpu_s: dict[str, int] = field(default_factory=dict)
    max_core_s: dict[str, int] = field(default_factory=dict)
    cancel_requested: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class WitnessReport:
    chain_ok: bool
    forked: bool
    head: dict[str, Any]
    submits: list[tuple[int, str]]
    unknown: list[str]
    grant_submits: list[GrantSubmit] = field(default_factory=list)
    grant_settles: list[GrantSettlement] = field(default_factory=list)
    grant_cancels: list[tuple[int, str]] = field(default_factory=list)
    unknown_grants: list[str] = field(default_factory=list)
    grant_totals: dict[str, GrantTotal] = field(default_factory=dict)
    over_cap: list[str] = field(default_factory=list)
    over_hour_cap: list[str] = field(default_factory=list)
    cancel_problems: list[str] = field(default_factory=list)
    fork_reason: str = ""


def _log_entries(data: bytes) -> Optional[list[dict[str, Any]]]:
    """The entries when ``hpc_gate.verify_log`` accepts the chain, else None."""
    with tempfile.TemporaryDirectory(prefix="r4-witness-") as tmp:
        path = os.path.join(tmp, "gate.jsonl")
        with open(path, "wb") as handle:
            handle.write(data)
        if not hpc_gate.verify_log(path):
            return None
    return [json.loads(line) for line in data.splitlines() if line]


def _extends(entries: list[dict[str, Any]], stored: Any) -> bool:
    if not isinstance(stored, dict) or not stored.get("n"):
        return True
    n = stored["n"]
    return (
        isinstance(n, int) and 0 < n <= len(entries) and entries[n - 1].get("h") == stored.get("h")
    )


def split_view(entries: list[dict[str, Any]], server_head: Any) -> str:
    """The server's view of the log head (``GET …/hpc/gate``) must be a
    point on the log r4 read: ``n_s`` within it and entry ``n_s`` carrying
    ``h_s``. Otherwise the gate shows the server and the person different
    logs (a split view). ``""`` when consistent or not checked."""
    if not isinstance(server_head, dict):
        return ""
    n, h = server_head.get("n"), server_head.get("h")
    if type(n) is not int or not 0 <= n <= len(entries):
        return f"split view: the site reports a log of {shown(n)} entries; r4 read {len(entries)}"
    want = entries[n - 1].get("h") if n else hpc_gate.GENESIS
    if h != want:
        return (
            f"split view: the site reports head {shown(h)[:16]} at entry {n}; "
            f"r4 read {shown(want)[:16]}"
        )
    return ""


def _fork_reason(entries: list[dict[str, Any]], stored: Any, server_head: Any) -> str:
    if not _extends(entries, stored):
        return (
            f"the log does not extend the head r4 witnessed last (entry {stored.get('n')}, "
            f"{shown(stored.get('h'))[:16]}): it was rewritten or replaced"
        )
    return split_view(entries, server_head)


def _ok(entry: dict[str, Any], verb: str) -> bool:
    return entry.get("verb") == verb and entry.get("result") == "ok"


def _submits(entries: list[dict[str, Any]], since: int) -> list[tuple[int, str]]:
    found = []
    for entry in entries[since:]:
        match = _SUBMIT_INTENT_RE.search(str(entry.get("detail", "")))
        if _ok(entry, "submit") and match:
            found.append((int(entry["n"]), match.group(1)))
    return found


def _fields(detail: Any) -> dict[str, str]:
    """``key=value`` tokens of a fixed-form log detail (the first one wins)."""
    out: dict[str, str] = {}
    for token in str(detail).split():
        key, sep, value = token.partition("=")
        if sep:
            out.setdefault(key, value)
    return out


def _grant_fields(entry: dict[str, Any]) -> Optional[dict[str, str]]:
    if not _ok(entry, "submit"):
        return None
    fields = _fields(entry.get("detail", ""))
    return fields if _GRANT_ID_RE.fullmatch(fields.get("grant", "")) else None


def _grant_submits(entries: list[dict[str, Any]], since: int) -> list[GrantSubmit]:
    found = []
    for entry in entries[since:]:
        fields = _grant_fields(entry)
        if fields is not None:
            run, step = fields.get("run", ""), fields.get("step", "")
            found.append(GrantSubmit(int(entry["n"]), fields["grant"], run, step))
    return found


def _seconds(fields: dict[str, str], key: str) -> int:
    value = fields.get(key, "")
    return int(value) if value.isdigit() else 0


def _grant_settlements(entries: list[dict[str, Any]], since: int = 0) -> list[GrantSettlement]:
    found = []
    for entry in entries[since:]:
        fields = _fields(entry.get("detail", ""))
        if not _ok(entry, "settle") or not _GRANT_ID_RE.fullmatch(fields.get("grant", "")):
            continue
        found.append(
            GrantSettlement(
                int(entry["n"]),
                fields["grant"],
                fields.get("run", ""),
                fields.get("step", ""),
                fields.get("job", ""),
                _seconds(fields, "gpu_s"),
                _seconds(fields, "core_s"),
                fields.get("bound") == "1",
            )
        )
    return found


def grant_totals(entries: list[dict[str, Any]]) -> dict[str, GrantTotal]:
    """Each grant's whole-log submits, reservation and settled charge."""
    totals: dict[str, list[int]] = {}
    for entry in entries:
        fields = _grant_fields(entry)
        if fields is None:
            continue
        total = totals.setdefault(fields["grant"], [0, 0, 0, 0, 0])
        total[0] += 1
        total[1] += _seconds(fields, "gpu_s")
        total[2] += _seconds(fields, "core_s")
    for receipt in _grant_settlements(entries):
        total = totals.setdefault(receipt.grant_id, [0, 0, 0, 0, 0])
        total[3] += receipt.gpu_s
        total[4] += receipt.core_s
    return {grant: GrantTotal(*values) for grant, values in totals.items()}


def _grant_cancels(entries: list[dict[str, Any]], since: int) -> list[tuple[int, str]]:
    return [
        (int(entry["n"]), _fields(entry.get("detail", "")).get("grant", ""))
        for entry in entries[since:]
        if _ok(entry, "grant-cancel")
    ]


def over_cap(totals: dict[str, GrantTotal], max_jobs: dict[str, int]) -> list[str]:
    """Grants whose logged submits exceed the ``max_jobs`` r4 signed (K17)."""
    return sorted(
        grant
        for grant, total in totals.items()
        if type(max_jobs.get(grant)) is int and total.jobs > max_jobs[grant]
    )


def over_hour_cap(totals: dict[str, GrantTotal], book: GrantBook) -> list[str]:
    """Grants whose independently logged charge exceeds signed hour caps."""
    return sorted(
        grant
        for grant, total in totals.items()
        if (grant in book.max_gpu_s and total.charged_gpu_s > book.max_gpu_s[grant])
        or (
            grant in book.max_core_s
            and book.max_core_s[grant] > 0
            and total.charged_core_s > book.max_core_s[grant]
        )
    )


def cancel_witness_problems(entries: list[dict[str, Any]], book: GrantBook) -> list[str]:
    """Persistent cancellation alarms over the whole log, not one page."""
    problems: list[str] = []
    cancelled = {grant for _n, grant in _grant_cancels(entries, since=0)}
    submits = _grant_submits(entries, since=0)
    for grant, requested in sorted(book.cancel_requested.items()):
        if grant not in cancelled:
            problems.append(f"grant {grant}: no grant-cancel ok entry after the recorded request")
        for submit in submits:
            timestamp = entries[submit.n - 1].get("t")
            after = isinstance(timestamp, (int, float)) and timestamp > requested
            if submit.grant_id == grant and after:
                problems.append(
                    f"grant {grant}: submit for step {submit.step} was logged "
                    "after cancel was requested"
                )
    return problems


def _since(stored: Any) -> int:
    return stored["n"] if isinstance(stored, dict) and isinstance(stored.get("n"), int) else 0


def witness(
    data: bytes,
    stored: Any,
    signed: set[str],
    grants: Optional[GrantBook] = None,
    server_head: Any = None,
) -> WitnessReport:
    """Check the gate log: the chain, that it extends the head r4 stored last
    and holds the server's view of its head, and which intents and grants
    were used since; flag any r4 did not sign and any grant over its jobs."""
    entries = _log_entries(data)
    if entries is None:
        return WitnessReport(False, False, {}, [], [])
    head = {"n": len(entries), "h": entries[-1]["h"] if entries else hpc_gate.GENESIS}
    reason = _fork_reason(entries, stored, server_head)
    if reason:
        return WitnessReport(True, True, head, [], [], fork_reason=reason)
    since, book = _since(stored), grants or GrantBook()
    submits = _submits(entries, since)
    grant_submits = _grant_submits(entries, since)
    totals = grant_totals(entries)
    settles = _grant_settlements(entries, since)
    return WitnessReport(
        chain_ok=True,
        forked=False,
        head=head,
        submits=submits,
        unknown=sorted({ident for _, ident in submits if ident not in signed}),
        grant_submits=grant_submits,
        grant_settles=settles,
        grant_cancels=_grant_cancels(entries, since),
        unknown_grants=sorted({s.grant_id for s in grant_submits} - book.signed),
        grant_totals=totals,
        over_cap=over_cap(totals, book.max_jobs),
        over_hour_cap=over_hour_cap(totals, book),
        cancel_problems=cancel_witness_problems(entries, book),
    )


def _signed_cap_seconds(
    records: dict[str, Any], signed: frozenset[str]
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    max_jobs: dict[str, int] = {}
    max_gpu_s: dict[str, int] = {}
    max_core_s: dict[str, int] = {}
    for grant in signed:
        caps = records[grant].get("caps")
        value = caps.get("max_jobs") if isinstance(caps, dict) else None
        if type(value) is int:
            max_jobs[grant] = value
        for target, name in (("max_gpu_hours", max_gpu_s), ("max_core_hours", max_core_s)):
            hours = caps.get(target) if isinstance(caps, dict) else None
            if type(hours) is int:
                name[grant] = hours * 3600
    return max_jobs, max_gpu_s, max_core_s


def _cancel_requests(records: dict[str, Any]) -> dict[str, int]:
    requested: dict[str, int] = {}
    for grant, record in records.items():
        timestamp = record.get("cancel_requested_at")
        if type(timestamp) is int:
            requested[grant] = timestamp
    return requested


def grant_book(site: Optional[str] = None) -> GrantBook:
    """This site's signed grants and every local cancel-request timestamp."""
    from .hpc_grants import grant_record_ids, read_grant_record, signed_grant_ids

    all_records = {grant: read_grant_record(grant) for grant in grant_record_ids()}
    records = {
        grant: record
        for grant, record in all_records.items()
        if site is None or record.get("site") == site
    }
    signed = frozenset(set(records) & signed_grant_ids())
    max_jobs, max_gpu_s, max_core_s = _signed_cap_seconds(records, signed)
    return GrantBook(signed, max_jobs, max_gpu_s, max_core_s, _cancel_requests(records))


def record_fork(site: str, stored: Any, got: Any, reason: str) -> None:
    """``witness_fork``: r4 signs no intent, grant or rotate for the site
    until the person clears it (``--clear-fork``)."""
    seen = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fork = {"seen_at": seen, "stored": stored, "got": got, "reason": reason}
    update_site_record(site, witness_fork=fork)


def witness_site(
    site: str, data: bytes, server_head: Any = None, *, clear_fork: bool = False
) -> WitnessReport:
    """Witness ``data`` against the site record and store the new head when
    the log verifies and extends it. A fork keeps the old head and records
    ``witness_fork``; ``clear_fork`` takes this log as the new baseline (the
    person investigated) and clears the field when nothing else forks.

    An intent or grant this r4 did not sign stays in the record
    (``witness_unknown``, ``witness_unknown_grants``) and is reported on
    every witness until the person acknowledges it
    (:func:`acknowledge_unknown`): a missed first warning (a cron job, a
    scrolled terminal) is not lost when the head moves on (I-9; fixer
    finding 2026-11-08)."""
    record = read_site_record(site)
    stored = None if clear_fork else record.get("witness")
    report = witness(data, stored, signed_ids(), grant_book(site), server_head)
    if not report.chain_ok:
        got = server_head or {"unverified": True}
        record_fork(site, record.get("witness"), got, "the gate log's hash chain is broken")
        return report
    if report.forked:
        got = server_head if report.fork_reason.startswith("split view") else report.head
        record_fork(site, record.get("witness"), got, report.fork_reason)
        return report
    pending = sorted(set(_pending(record, _UNKNOWN_INTENTS)) | set(report.unknown))
    grants = sorted(set(_pending(record, _UNKNOWN_GRANTS)) | set(report.unknown_grants))
    cleared = {"witness_fork": None} if clear_fork else {}
    update_site_record(
        site, witness=report.head, witness_unknown=pending, witness_unknown_grants=grants, **cleared
    )
    return replace(report, unknown=pending, unknown_grants=grants)


def live_fork(site: str, live_head: Any) -> str:
    """``--live``: the live log head against the stored one, without reading
    the log. A shorter log, or the same length with another head, is a fork
    (recorded); ``""`` otherwise."""
    stored = read_site_record(site).get("witness")
    if not (isinstance(stored, dict) and isinstance(live_head, dict)):
        return ""
    n_s, n = stored.get("n"), live_head.get("n")
    if type(n_s) is not int or type(n) is not int:
        return ""
    if n < n_s:
        reason = f"rollback: the live log has {n} entries; r4 witnessed {n_s}"
    elif n == n_s and live_head.get("h") != stored.get("h"):
        reason = f"the live log's head at entry {n} differs from the one r4 witnessed"
    else:
        return ""
    record_fork(site, stored, live_head, reason)
    return reason


_UNKNOWN_INTENTS = "witness_unknown"
_UNKNOWN_GRANTS = "witness_unknown_grants"


def _pending(record: dict[str, Any], key: str) -> list[str]:
    raw = record.get(key)
    return [i for i in raw if isinstance(i, str)] if isinstance(raw, list) else []


def acknowledge_unknown(site: str, ident: str) -> bool:
    """Clear one reported unknown intent (64 hex) or grant (32 hex)
    (``--ack``); False when it was not pending."""
    key = _UNKNOWN_GRANTS if _GRANT_ID_RE.fullmatch(ident or "") else _UNKNOWN_INTENTS
    pending = _pending(read_site_record(site), key)
    if ident not in pending:
        return False
    update_site_record(site, **{key: [i for i in pending if i != ident]})
    return True
