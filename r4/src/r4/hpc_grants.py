"""Standing grants: bounded unattended work under my own HPC login.

``r4 hpc grant`` signs one grant (``umrs-grant-v1@<domain>``): UMRS may then
start runs of the listed workflow versions at one site, under the listed
account and project, within the caps, until the grant expires or is
cancelled. The gate on the site checks every run against the grant and keeps
the counters (plan §6, the C/D.0 wire contract, items C1 and C5).

r4 never signs a digest it has only been told: for each workflow it reads
the export of the exact version itself and recomputes the source manifest,
the step skeleton and the parameter schema with the gate's own shared code,
then refuses with a diff when the server's proposal differs. A parameter
that could carry code (a code-typed name, a keyword outside the allowed
subset, an ``object`` or ``array`` type) refuses, naming the parameter.

Signing is the person's act: it refuses in a Companion tool shell and
without a terminal, the command is fenced as never-auto, and the key asks for
the touch or the passphrase. While a fork of the site's gate log is
recorded, r4 signs no grant (it still signs ``grant-cancel``).
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from r4_contracts import hpc_gate

from .errors import R4Error
from .hpc_intents import (
    EXPORT_MAX_BYTES,
    IntentRefused,
    _fields,
    _log_entries,
    _shared,
    file_digest,
    read_export,
    shown,
)
from .hpc_signing import (
    HpcSigningError,
    check_domain,
    check_login,
    check_principal,
    check_site,
    fork_refusal,
    person_refusal,
    read_site_record,
    sign,
    signed_control,
    signing_dir,
    site_signer,
    unambiguous_site_signer,
)

#: A grant lives at most this many days (``GRANT_MAX_WINDOW`` is 30 days).
GRANT_DAYS_MAX = 30
DEFAULT_MAX_CONCURRENT = 4
MAX_WORKFLOWS = 16
INPUT_MODES = ("listed", "chained", "any")
CAP_LIMITS = {
    "max_jobs": (1, 10_000),
    "max_gpu_hours": (0, 1_000_000),
    "max_core_hours": (0, 10_000_000),
    "max_concurrent": (1, 64),
}
#: A ``grant-cancel`` control lives this long (``expires - issued``).
CANCEL_TTL_S = 300
#: r4's local grant records (``{site, venue, grant_sha256, expires, caps}``):
#: the witness flags a grant the log names that is not here, and counts each
#: grant's submits against its ``max_jobs``.
GRANTS_DIR_NAME = "grants"
_GRANT_ID_RE = re.compile(r"[0-9a-f]{32}")

# ── the honest wording (D-10, K16): binds this summary and the manual ──────
D10_FIRST = "UMRS stores nothing that can act as you without your signature."
D10_DATA = (
    "Under a standing permission you give, UMRS chooses the data your approved workflows run on."
)
RESIDUALS = (
    "Outputs that pass through UMRS are visible to its operators.",
    "The same team publishes r4 and the gate.",
)
UMRS_DECIDES = (
    "whether and when to start runs, up to the limits above",
    "the parameter values, within the bounds shown",
    "the data, per the data rule above",
    "each step's partition, CPUs, memory, GPUs and walltime, within the site "
    "profile installed at your gate (only the hour limits bound them)",
    "which listed account and project a run uses",
)
UMRS_CANNOT = (
    "run code other than the listed versions, or images other than the listed ones",
    "change the pinned step graph or step configs",
    "call a model endpoint (none is allowed under a grant)",
    "exceed the limits on jobs, hours or concurrent jobs",
    "run anything after expiry, a cancel or a re-enrolment of your account",
)
EXPIRY_TEXT = "A run still going at expiry stops there."
FREE_TEXT = "free text UMRS chooses"
DEFAULT_MAX_LENGTH = 1024


def _refused(*problems: str) -> IntentRefused:
    return IntentRefused(list(problems))


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _utf8(value: str) -> bytes:
    return value.encode("utf-8")


# ── the request ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GrantRequest:
    """What the person asked for on the command line."""

    site: str
    project: str
    workflows: tuple[tuple[str, Optional[str]], ...]
    max_jobs: int
    max_gpu_hours: int
    days: int
    max_core_hours: int = 0
    max_concurrent: Optional[int] = None
    inputs: str = "chained"
    listed_paths: tuple[Path, ...] = ()
    yes: bool = False

    def caps(self) -> dict[str, int]:
        concurrent = self.max_concurrent
        if concurrent is None:
            concurrent = min(DEFAULT_MAX_CONCURRENT, self.max_jobs)
        return {
            "max_jobs": self.max_jobs,
            "max_gpu_hours": self.max_gpu_hours,
            "max_core_hours": self.max_core_hours,
            "max_concurrent": concurrent,
        }


def _cap_problems(caps: dict[str, int]) -> list[str]:
    problems = [
        f"--{name.replace('_', '-')} must be an integer from {low} to {high}"
        for name, (low, high) in CAP_LIMITS.items()
        if type(caps[name]) is not int or not low <= caps[name] <= high
    ]
    if not problems and caps["max_concurrent"] > caps["max_jobs"]:
        problems.append("--max-concurrent must be at most --max-jobs")
    return problems


def _input_problems(request: GrantRequest) -> list[str]:
    if request.inputs not in INPUT_MODES:
        return [f"--inputs must be one of {', '.join(INPUT_MODES)}"]
    if request.inputs == "listed" and not request.listed_paths:
        return ["--inputs listed needs at least one --listed-input FILE"]
    if request.inputs == "any" and request.listed_paths:
        return ["--listed-input is refused with --inputs any (UMRS chooses every input)"]
    return []


def request_problems(request: GrantRequest) -> list[str]:
    """The command line's own limits, before any request is made."""
    problems = _cap_problems(request.caps()) + _input_problems(request)
    if not 1 <= request.days <= GRANT_DAYS_MAX:
        problems.append(f"--days must be from 1 to {GRANT_DAYS_MAX}")
    if not 1 <= len(request.workflows) <= MAX_WORKFLOWS:
        problems.append(f"name 1 to {MAX_WORKFLOWS} workflows (--workflow NAME[@VERSION])")
    if len({name for name, _ in request.workflows}) != len(request.workflows):
        problems.append("each workflow may be named once")
    return problems


def listed_inputs(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """``inputs.listed``: ``{sha256, size}`` of files r4 hashed itself,
    strictly increasing by ``sha256`` (one entry per distinct digest)."""
    entries = {}
    for path in paths:
        sha256, size = file_digest(Path(path))
        entries[sha256] = {"sha256": sha256, "size": size}
    return [entries[key] for key in sorted(entries)]


# ── what r4 knows itself ────────────────────────────────────────────────────


@dataclass(frozen=True)
class GrantContext:
    """The site, login, account and principal from r4's own records and the
    person's own account view, independent of the proposal."""

    site: str
    venue: str
    domain: str
    login: str
    principal: str
    account: str
    project: str


def _active_account(client: Any) -> tuple[dict[str, Any], list[Any]]:
    view = client.hpc_account() or {}
    account, links = view.get("account"), view.get("links")
    if not isinstance(account, dict) or account.get("status") != "active":
        raise HpcSigningError("no active HPC account at this site: run `r4 hpc add <site>`.")
    return account, links if isinstance(links, list) else []


def _link_account(links: list[Any], project: str) -> str:
    for link in links:
        if isinstance(link, dict) and link.get("project_id") == project:
            if link.get("status") == "active":
                return check_login(link.get("slurm_account"))
    raise HpcSigningError(
        f"project {project} has no active link here: run `r4 hpc link --project {project} ...`."
    )


def _recorded_login(account: dict[str, Any], record: dict[str, Any]) -> str:
    login = check_login(account.get("verified_login"))
    if record.get("login") not in (None, login):
        raise HpcSigningError(
            f"the site verified login {shown(login)!r}, but r4 recorded "
            f"{shown(record.get('login'))!r} at `r4 hpc add`: run `r4 hpc verify`."
        )
    return login


def gather_context(client: Any, request: GrantRequest) -> GrantContext:
    record = read_site_record(request.site)
    if not (record.get("venue") and record.get("domain")):
        raise HpcSigningError(
            f"r4 has no record of the gate at {request.site}: run `r4 hpc add {request.site}`."
        )
    account, links = _active_account(client)
    return GrantContext(
        site=request.site,
        venue=check_site(record["venue"]),
        domain=check_domain(record["domain"]),
        login=_recorded_login(account, record),
        principal=check_principal((client.site_me() or {}).get("user_id")),
        account=_link_account(links, request.project),
        project=request.project,
    )


# ── the proposal and the export of each workflow ────────────────────────────


def proposal_body(request: GrantRequest) -> dict[str, Any]:
    """``POST grant-proposal``: ``{project_id, workflows: [{name, version?}]}``."""
    workflows = [
        {"name": name, **({"version": version} if version else {})}
        for name, version in request.workflows
    ]
    return {"project_id": request.project, "workflows": workflows}


def _identity_problems(answer: dict[str, Any], ctx: GrantContext) -> list[str]:
    checks = (
        ("site", answer.get("site"), ctx.venue),
        ("domain", answer.get("domain"), ctx.domain),
        ("login", answer.get("login"), ctx.login),
        ("accounts", answer.get("accounts"), [ctx.account]),
    )
    return [
        f"proposal {label}: {shown(_canon(got))}, r4 expected {shown(_canon(want))}"
        for label, got, want in checks
        if _canon(got) != _canon(want)
    ]


def _proposed_entry(
    proposed: list[Any], name: str, version: Optional[str]
) -> Optional[dict[str, Any]]:
    for entry in proposed:
        if isinstance(entry, dict) and entry.get("name") == name:
            if version in (None, entry.get("version")):
                return entry
    return None


def proposed_workflows(answer: dict[str, Any], request: GrantRequest) -> list[dict[str, Any]]:
    """One proposal entry per requested workflow, and nothing else."""
    raw = answer.get("workflows")
    proposed = raw if isinstance(raw, list) else []
    found, problems = [], []
    for name, version in request.workflows:
        entry = _proposed_entry(proposed, name, version)
        if entry is None or not isinstance(entry.get("version"), str):
            problems.append(f"the proposal has no entry for {shown(name)}@{shown(version or '*')}")
            continue
        found.append(entry)
    named = len(request.workflows)
    if len(proposed) != named:
        problems.append(f"the proposal lists {len(proposed)} workflows; you named {named}")
    if problems:
        raise _refused(*problems)
    return found


@dataclass(frozen=True)
class GrantWorkflow:
    """One workflow version as r4 hashed it from its export."""

    name: str
    version: str
    src_manifest_sha256: str
    steps_sha256: str
    schema: dict[str, Any]
    declared: dict[str, Any]
    images: tuple[dict[str, Any], ...]
    trusted_image: dict[str, Any]

    def entry(self) -> dict[str, Any]:
        """Its ``workflows[]`` entry; ``params`` lists only its own names."""
        return {
            "name": self.name,
            "version": self.version,
            "src_manifest_sha256": self.src_manifest_sha256,
            "steps_sha256": self.steps_sha256,
            "params": sorted(self.schema["properties"], key=_utf8),
        }


def entrypoints(workflow: dict[str, Any]) -> dict[str, Any]:
    """``src_manifest.entrypoints``: each step's ``entrypoint`` by its
    ``step_graph`` name (the same map an intent carries)."""
    steps = workflow["steps"]
    graph = list(_shared("the export's workflow.json", hpc_gate.step_graph, steps))
    names = [node["name"] if isinstance(node, dict) else node[0] for node in graph]
    return {str(name): raw.get("entrypoint") for name, raw in zip(names, steps)}


def declared_properties(workflow: dict[str, Any]) -> dict[str, Any]:
    """The parameter properties exactly as ``workflow.json`` declares them
    (to show a narrowed bound or a default length against the grant's)."""
    for key in ("parameter_schema", "parameters_schema", "parameters"):
        candidate = workflow.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("properties"), dict):
            return dict(candidate["properties"])
        if isinstance(candidate, list):
            return {
                item["name"]: item
                for item in candidate
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
    return {}


def _image_text(image: Any) -> str:
    if not isinstance(image, dict):
        return shown(image)
    return f"{shown(image.get('ref'))} (id {shown(image.get('image_id'))[:12]})"


def declared_images(workflow: dict[str, Any], trusted_ref: Any) -> set[Any]:
    """The image refs this version may run: each step's declared
    ``routing_hints.image``, and the platform image (a step that declares
    none runs on it)."""
    refs = {trusted_ref}
    for step in workflow["steps"]:
        hints = step.get("routing_hints")
        if isinstance(hints, dict) and hints.get("image"):
            refs.add(hints["image"])
    return refs


def _image_objects(
    entry: dict[str, Any], label: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trusted, raw = entry.get("trusted_image"), entry.get("images")
    if not (isinstance(trusted, dict) and isinstance(raw, list)):
        raise _refused(f"{label}: the proposal carries no images and trusted image")
    images = [i for i in raw if isinstance(i, dict)]
    if len(images) != len(raw):
        raise _refused(f"{label}: the proposal's images are not image objects")
    return images, trusted


def image_list(entry: dict[str, Any], workflow: dict[str, Any], label: str) -> list[dict[str, Any]]:
    """The proposal's step images, including the platform image used by a step."""
    images, trusted = _image_objects(entry, label)
    allowed = declared_images(workflow, trusted.get("ref"))
    problems = [
        f"{label}: image {_image_text(image)} is not declared by workflow.json "
        "nor the platform image"
        for image in images
        if image.get("ref") not in allowed
    ]
    if problems:
        raise _refused(*problems)
    return images


def _digest_problems(entry: dict[str, Any], src: str, steps: str, label: str) -> list[str]:
    pairs = (("src_manifest_sha256", src), ("steps_sha256", steps))
    return [
        f"{label} {key}: proposed {shown(entry.get(key))}, r4 computed {value} from the export"
        for key, value in pairs
        if entry.get(key) != value
    ]


def prepare_workflow(client: Any, entry: dict[str, Any]) -> GrantWorkflow:
    """Read the export of the proposed version, recompute both digests and
    the parameter schema, and compare (refusing with a diff)."""
    name, version = str(entry["name"]), str(entry["version"])
    label = f"workflow {shown(name)}@{shown(version)}"
    data = client.workflows.export_bytes(name, version=version, max_bytes=EXPORT_MAX_BYTES)
    tree = read_export(data)
    manifest = {"files": tree.files, "entrypoints": entrypoints(tree.workflow)}
    src = _shared(label, hpc_gate.src_manifest_sha256, manifest)
    skeleton = _shared(label, hpc_gate.workflow_steps_skeleton, tree.workflow)
    steps = _shared(label, hpc_gate.steps_sha256, skeleton)
    problems = _digest_problems(entry, src, steps, label)
    if problems:
        raise _refused(*problems)
    schema = _shared(f"{label} parameters", hpc_gate.grant_param_schema, tree.workflow)
    images = image_list(entry, tree.workflow, label)
    _, trusted = _image_objects(entry, label)
    return GrantWorkflow(
        name,
        version,
        src,
        steps,
        schema,
        declared_properties(tree.workflow),
        tuple(images),
        trusted,
    )


# ── the grant's parts ───────────────────────────────────────────────────────


def merge_schemas(workflows: list[GrantWorkflow]) -> dict[str, Any]:
    """One ``param_schema`` (the gate's shared merge): the union of the
    properties (a name declared differently by two workflows refuses, "grant
    them separately"), ``required`` the intersection."""
    schemas = [wf.schema for wf in workflows]
    return dict(_shared("the parameters", hpc_gate.merge_grant_param_schemas, schemas))


def _merge_image_objects(images: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Distinct image objects by id, in canonical order."""
    by_id: dict[str, dict[str, Any]] = {}
    for image in images:
        key = str(image.get("image_id"))
        if key in by_id and _canon(by_id[key]) != _canon(image):
            raise _refused(f"image id {shown(key)[:12]} names two different images")
        by_id[key] = dict(image)
    return [by_id[key] for key in sorted(by_id, key=_utf8)]


def merge_images(workflows: list[GrantWorkflow]) -> list[dict[str, Any]]:
    """The combined image allow-list, compatible with the original grant shape."""
    return _merge_image_objects(
        image for wf in workflows for image in (*wf.images, wf.trusted_image)
    )


def merge_trusted_images(workflows: list[GrantWorkflow]) -> list[dict[str, Any]]:
    """The helper/posture images, bound separately in newly signed grants."""
    return _merge_image_objects(wf.trusted_image for wf in workflows)


def build_grant(
    ctx: GrantContext,
    request: GrantRequest,
    workflows: list[GrantWorkflow],
    listed: list[dict[str, Any]],
    now: float,
) -> dict[str, Any]:
    """The grant body (C1), its times stamped from ``now``."""
    entries = sorted(
        (wf.entry() for wf in workflows), key=lambda e: (_utf8(e["name"]), _utf8(e["version"]))
    )
    body = {
        "v": 1,
        "type": "grant",
        "grant_id": secrets.token_hex(16),
        "site": ctx.venue,
        "login": ctx.login,
        "principal": ctx.principal,
        "accounts": [ctx.account],
        "projects": [ctx.project],
        "workflows": entries,
        "images": merge_images(workflows),
        "trusted_images": merge_trusted_images(workflows),
        "param_schema": merge_schemas(workflows),
        "inputs": {"mode": request.inputs, "listed": listed},
        "caps": request.caps(),
        "delegable": False,
        "nonce": secrets.token_hex(16),
    }
    return stamp(body, now, request.days)


def stamp(body: dict[str, Any], now: float, days: int) -> dict[str, Any]:
    """``issued = not_before = now`` and ``expires = now + days``: set again
    after the confirmation, just before signing, so a slow answer never
    leaves ``issued`` outside the store's window (K8)."""
    issued = int(now)
    return {**body, "issued": issued, "not_before": issued, "expires": issued + days * 86400}


# ── the summary the person signs (C5 step 7, K16) ───────────────────────────


def _local_time(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def data_rule(inputs: dict[str, Any]) -> str:
    """The inputs mode in words; the ``any`` sentence verbatim (D-10)."""
    count = len(inputs["listed"])
    if inputs["mode"] == "listed":
        return f"only the {count} files you listed"
    if inputs["mode"] == "chained" and count:
        return (
            f"only the {count} files you listed and outputs of runs under this "
            "permission, which UMRS picks"
        )
    if inputs["mode"] == "chained":
        return "only outputs of runs under this permission, which UMRS picks"
    return D10_DATA


def _range_text(prop: dict[str, Any]) -> str:
    low, high = prop.get("minimum"), prop.get("maximum")
    if low is not None and high is not None:
        return f"from {low} to {high}"
    if low is not None:
        return f"at least {low}"
    return f"at most {high}" if high is not None else "any value"


def bounds_text(prop: dict[str, Any]) -> str:
    if "enum" in prop:
        return "one of " + ", ".join(shown(_canon(v), 40) for v in prop["enum"])
    if prop["type"] == "boolean":
        return "true or false"
    if prop["type"] == "string":
        return f"at most {prop['maxLength']} characters"
    return _range_text(prop)


def _declared_notes(prop: dict[str, Any], declared: dict[str, Any]) -> list[str]:
    notes = []
    for key in ("minimum", "maximum"):
        raw = declared.get(key)
        if isinstance(raw, float) and raw != prop.get(key):
            notes.append(f"declared {key} {raw}, narrowed to a whole number")
    if prop["type"] == "string" and "maxLength" not in declared:
        notes.append(f"no length declared; r4 set {prop['maxLength']}")
    return notes


def param_line(name: str, prop: dict[str, Any], declared: dict[str, Any]) -> str:
    notes = _declared_notes(prop, declared)
    if prop["type"] == "string" and "enum" not in prop:
        notes.append(FREE_TEXT)
    tail = f"  ({'; '.join(notes)})" if notes else ""
    return f"    {shown(name):<24} {prop['type']:<8} {bounds_text(prop)}{tail}"


def _param_lines(body: dict[str, Any], workflows: list[GrantWorkflow]) -> list[str]:
    declared: dict[str, Any] = {}
    for wf in workflows:
        for name, raw in wf.declared.items():
            declared.setdefault(name, raw if isinstance(raw, dict) else {})
    props = body["param_schema"]["properties"]
    if not props:
        return ["  parameters  none"]
    lines = ["  parameters  (UMRS chooses each value within these bounds)"]
    return lines + [param_line(n, props[n], declared.get(n, {})) for n in sorted(props)]


def _cap_lines(caps: dict[str, int]) -> list[str]:
    core = caps["max_core_hours"]
    core_text = "no limit (0 means no core-hour cap)" if core == 0 else f"at most {core}"
    return [
        "  limits",
        f"    jobs        at most {caps['max_jobs']} in all",
        f"    at a time   at most {caps['max_concurrent']} jobs",
        f"    GPU-hours   at most {caps['max_gpu_hours']}",
        f"    core-hours  {core_text}",
    ]


def _header_lines(body: dict[str, Any], days: int) -> list[str]:
    return [
        "You are asked to sign a standing permission (a grant):",
        f"  site        {shown(body['site'])}   login {shown(body['login'])}   "
        f"account {', '.join(shown(a) for a in body['accounts'])}",
        f"  project     {', '.join(shown(p) for p in body['projects'])}",
        f"  grant       {body['grant_id']}",
        f"  expires     {_local_time(body['expires'])} (in {days} days)",
    ]


def _workflow_lines(body: dict[str, Any]) -> list[str]:
    lines = ["  workflows"]
    for wf in body["workflows"]:
        lines.append(
            f"    {shown(wf['name'])}@{shown(wf['version'])}   source manifest sha256 "
            f"{wf['src_manifest_sha256'][:16]}   steps sha256 {wf['steps_sha256'][:16]}"
        )
    lines = (
        lines
        + ["  images (steps and, for legacy grants, helpers)"]
        + [f"    {_image_text(image)}" for image in body["images"]]
    )
    if "trusted_images" in body:
        lines += ["  helper/posture images (separately bound)"]
        lines += [f"    {_image_text(image)}" for image in body["trusted_images"]]
    return lines


def _honest_lines(body: dict[str, Any]) -> list[str]:
    lines = ["", D10_FIRST]
    lines += ["UMRS decides:"] + [f"  - {item}" for item in UMRS_DECIDES]
    lines += ["UMRS also sees every output.", "UMRS cannot:"]
    lines += [f"  - {item}" for item in UMRS_CANNOT]
    return lines + [EXPIRY_TEXT, *RESIDUALS]


def render_summary(body: dict[str, Any], workflows: list[GrantWorkflow], days: int) -> str:
    """Everything signed that a person can judge, and what UMRS still decides."""
    lines = _header_lines(body, days) + _workflow_lines(body)
    lines += _param_lines(body, workflows) + _cap_lines(body["caps"])
    lines += ["  data", f"    {data_rule(body['inputs'])}"]
    return "\n".join(lines + _honest_lines(body))


def budget_warning(answer: dict[str, Any], gpu_hours: int) -> Optional[str]:
    """The project's remaining GPU-hours against the grant's cap (advisory)."""
    budget = answer.get("budget")
    remaining = budget.get("remaining_gpu_hours") if isinstance(budget, dict) else None
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        return None
    if gpu_hours <= remaining:
        return None
    return (
        f"warning: the project has {remaining:g} GPU-hours left and this grant asks for "
        f"{gpu_hours}; the server will refuse to store it."
    )


# ── signing, the local record and the store ─────────────────────────────────


def grants_dir() -> Path:
    return signing_dir().parent / GRANTS_DIR_NAME


def check_grant_id(value: Any) -> str:
    if not isinstance(value, str) or not _GRANT_ID_RE.fullmatch(value):
        raise HpcSigningError(f"refused: {shown(value)!r} is not a grant id (32 hex).")
    return value


def _write_grant_record(grant_id: str, record: dict[str, Any]) -> None:
    folder = grants_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / f"{check_grant_id(grant_id)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def record_grant(grant: dict[str, Any], payload: bytes, site: str) -> None:
    """Keep what the witness needs about each grant this r4 signed."""
    record = {
        "site": site,
        "venue": grant["site"],
        "grant_sha256": hashlib.sha256(payload).hexdigest(),
        "expires": grant["expires"],
        "caps": grant["caps"],
    }
    _write_grant_record(grant["grant_id"], record)


def read_grant_record(grant_id: str) -> dict[str, Any]:
    path = grants_dir() / f"{check_grant_id(grant_id)}.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def grant_record_ids() -> set[str]:
    """All grants with local evidence, including cancellation-only records."""
    try:
        names = [p.name for p in grants_dir().iterdir()]
    except OSError:
        return set()
    return {n[:-5] for n in names if n.endswith(".json") and _GRANT_ID_RE.fullmatch(n[:-5])}


def signed_grant_ids() -> set[str]:
    """Only records of grants this r4 signed, never mere cancel requests."""
    return {
        grant_id
        for grant_id in grant_record_ids()
        if re.fullmatch(r"[0-9a-f]{64}", str(read_grant_record(grant_id).get("grant_sha256", "")))
    }


def record_cancel_request(grant_id: str, site: str, requested_at: int) -> None:
    """Persist a request before either delivery route, for the witness."""
    check_grant_id(grant_id)
    check_site(site)
    if type(requested_at) is not int or requested_at < 0:
        raise HpcSigningError("cancel request time must be a non-negative integer")
    record = {
        **read_grant_record(grant_id),
        "site": site,
        "cancel_requested_at": requested_at,
    }
    _write_grant_record(grant_id, record)


def grant_payload(body: dict[str, Any]) -> bytes:
    """The exact bytes to sign: strict canonical, no float or null, checked
    with the gate's own ``validate_grant``."""
    payload = bytes(_shared("the grant", hpc_gate.canonical_bytes, body, json_values=False))
    if len(payload) > hpc_gate.GRANT_MAX_BYTES:
        raise _refused(f"the grant is over {hpc_gate.GRANT_MAX_BYTES} bytes")
    _shared("the grant", hpc_gate.validate_grant, hpc_gate.parse_strict(payload))
    return payload


def sign_grant(body: dict[str, Any], ctx: GrantContext) -> dict[str, str]:
    """Sign under ``umrs-grant-v1@<domain>`` with the key the gate trusts,
    and record the grant before it leaves this machine."""
    payload = grant_payload(body)
    namespace = _shared("the signing domain", hpc_gate.namespace, "grant", ctx.domain)
    _, key = unambiguous_site_signer(ctx.site)
    signature = sign(payload, namespace, key)
    record_grant(body, payload, ctx.site)
    return {"payload": base64.b64encode(payload).decode("ascii"), "signature": signature}


def _refuse_unless_person(action: str, site: Optional[str] = None) -> None:
    """The person at a terminal; with ``site``, also no recorded fork."""
    reason = person_refusal(action) or (fork_refusal(site, action) if site else None)
    if reason:
        raise HpcSigningError(reason)


def prepare_grant(
    client: Any, request: GrantRequest, *, out: Callable[[str], None], now: float
) -> tuple[GrantContext, dict[str, Any], list[GrantWorkflow]]:
    """Gather, propose, recompute, compare; the unsigned body."""
    problems = request_problems(request)
    if problems:
        raise _refused(*problems)
    listed = listed_inputs(request.listed_paths)
    ctx = gather_context(client, request)
    answer = client.hpc_grant_proposal(proposal_body(request)) or {}
    problems = _identity_problems(answer, ctx)
    if problems:
        raise _refused(*problems)
    workflows = [prepare_workflow(client, e) for e in proposed_workflows(answer, request)]
    warning = budget_warning(answer, request.max_gpu_hours)
    if warning:
        out(warning)
    return ctx, build_grant(ctx, request, workflows, listed, now), workflows


def run_grant(
    client: Any,
    request: GrantRequest,
    *,
    confirm: Callable[[str], bool],
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """The whole C5 flow: refuse unless a person at a terminal (and no
    recorded fork), propose, recompute, compare, show, confirm, stamp the
    times, sign, record, store."""
    _refuse_unless_person("grant", request.site)
    ctx, body, workflows = prepare_grant(client, request, out=out, now=clock())
    out(render_summary(body, workflows, request.days))
    if not request.yes and not confirm("Sign this standing permission with your signing key?"):
        raise HpcSigningError("not signed: declined.")
    body = stamp(body, clock(), request.days)
    envelope = sign_grant(body, ctx)
    answer = client.hpc_grant_store(envelope)
    return dict(answer or {}, grant_id=body["grant_id"])


# ── grant-cancel ────────────────────────────────────────────────────────────


def cancel_body(venue: str, principal: Any, grant_id: str, now: float) -> dict[str, Any]:
    """The ``grant-cancel`` control (C3), built and validated by the gate's
    shared code: no ``key_id``; it lives 300 s."""
    args = (check_site(venue), check_principal(principal), check_grant_id(grant_id))
    return dict(
        _shared("the cancel", hpc_gate.grant_control_body, *args, now=now, ttl=CANCEL_TTL_S)
    )


def cancel_site(grant_id: str, site: Optional[str]) -> str:
    """``--site``, else the site r4 recorded when it signed the grant."""
    found = site or read_grant_record(grant_id).get("site")
    if not isinstance(found, str) or not found:
        raise HpcSigningError(f"r4 did not sign grant {grant_id} here: pass --site.")
    return found


@dataclass(frozen=True)
class CancelResult:
    answer: dict[str, Any]
    #: A shape-matching relay claim; ``recorded`` is the independently checked fact.
    relayed: bool
    fork_note: Optional[str]
    #: Either route's verified gate receipt, never just a transport claim.
    recorded: bool = False
    direct: bool = False
    mirror_recorded: bool = False
    mirror_pending: bool = False


def _log_head_ok(head: Any) -> bool:
    if not isinstance(head, dict):
        return False
    return (
        type(head.get("n")) is int
        and head["n"] > 0
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(head.get("h", ""))))
    )


def gate_cancel_receipt(answer: Any, grant_id: str) -> bool:
    """A gate's own receipt for this grant, not an opaque transport boolean."""
    if not isinstance(answer, dict):
        return False
    return (
        answer.get("ok") is True
        and answer.get("op") == "grant-cancel"
        and answer.get("grant_id") == grant_id
        and _log_head_ok(answer.get("log_head"))
    )


def _relay_cancel_receipt(answer: dict[str, Any], grant_id: str) -> bool:
    return answer.get("relayed") is True and gate_cancel_receipt(answer.get("result"), grant_id)


def _independent_cancel_receipt(answer: Any, data: bytes, grant_id: str) -> bool:
    """Match the returned head to this cancel in a log read over the person's SSH."""
    if not gate_cancel_receipt(answer, grant_id):
        return False
    entries = _log_entries(data)
    if entries is None:
        return False
    head = answer["log_head"]
    index = head["n"] - 1
    if not 0 <= index < len(entries):
        return False
    entry = entries[index]
    fields = _fields(entry.get("detail", ""))
    return (
        entry.get("h") == head["h"]
        and entry.get("verb") == "grant-cancel"
        and entry.get("result") == "ok"
        and fields.get("grant") == grant_id
    )


def _verified_relay_cancel(answer: dict[str, Any], grant_id: str, gate_log: Any) -> bool:
    if gate_log is None or not _relay_cancel_receipt(answer, grant_id):
        return False
    try:
        data = gate_log()
    except (R4Error, OSError, TypeError, ValueError):
        return False
    return _independent_cancel_receipt(answer.get("result"), data, grant_id)


def _mirror_cancelled(answer: dict[str, Any]) -> bool:
    view = answer.get("grant")
    return (
        isinstance(view, dict)
        and view.get("status") == "cancelled"
        and view.get("gate_cancel") == "recorded"
    )


def _mirror_pending(answer: dict[str, Any]) -> bool:
    view = answer.get("grant")
    return (
        isinstance(view, dict)
        and view.get("status") == "cancelled"
        and not _mirror_cancelled(answer)
    )


def _relay_cancel(
    client: Any, grant_id: str, envelope: dict[str, str], direct: Any
) -> dict[str, Any]:
    try:
        return client.hpc_grant_cancel(grant_id, envelope) or {}
    except R4Error as exc:
        if direct is None:
            raise
        return {"relayed": False, "transport_error": str(exc)[:200]}


def cancel_grant(
    client: Any,
    grant_id: str,
    site: Optional[str],
    *,
    clock: Callable[[], float] = time.time,
    direct: Optional[Callable[[dict[str, str]], dict[str, Any]]] = None,
    gate_log: Optional[Callable[[], bytes]] = None,
) -> CancelResult:
    """Sign and send ``grant-cancel``. A relay succeeds only after its claimed
    entry is found at that exact head of a chain read over the person's SSH."""
    _refuse_unless_person("grant-cancel")
    site = cancel_site(check_grant_id(grant_id), site)
    record = read_site_record(site)
    if not (record.get("venue") and record.get("domain")):
        raise HpcSigningError(f"r4 has no record of the gate at {site}: run `r4 hpc add`.")
    principal = (client.site_me() or {}).get("user_id")
    requested_at = int(clock())
    body = cancel_body(record["venue"], principal, grant_id, requested_at)
    _, key = site_signer(site)
    envelope = signed_control(body, key, record["domain"])
    record_cancel_request(grant_id, site, requested_at)
    answer = _relay_cancel(client, grant_id, envelope, direct)
    relayed = _relay_cancel_receipt(answer, grant_id)
    recorded = _verified_relay_cancel(answer, grant_id, gate_log)
    used_direct = False
    if not recorded and direct is not None:
        direct_answer = direct(envelope) or {}
        recorded = gate_cancel_receipt(direct_answer, grant_id)
        used_direct = recorded
    return CancelResult(
        answer=answer,
        relayed=relayed,
        fork_note=fork_refusal(site, "grant-cancel"),
        recorded=recorded,
        direct=used_direct,
        mirror_recorded=_mirror_cancelled(answer),
        mirror_pending=_mirror_pending(answer),
    )


# ── the grants list (``r4 hpc grants``) ────────────────────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _hours(seconds: Any) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return "?"
    whole = -(-int(seconds * 100) // 3600)  # rounded up to two decimals
    return f"{whole / 100:.2f}"


def _usage_text(view: dict[str, Any]) -> str:
    caps, usage = _as_dict(view.get("caps")), _as_dict(view.get("usage"))
    core_cap = caps.get("max_core_hours")
    reserved = _hours(usage.get("reserved_gpu_seconds", 0))
    return (
        f"jobs {usage.get('jobs', 0)}/{caps.get('max_jobs', '?')}   "
        f"GPU-h {_hours(usage.get('gpu_seconds', 0))} used + "
        f"{reserved} reserved / {caps.get('max_gpu_hours', '?')}   "
        f"core-h {_hours(usage.get('core_seconds', 0))} / "
        f"{'no cap' if core_cap == 0 else core_cap}"
    )


def _workflows_text(view: dict[str, Any]) -> str:
    raw = view.get("workflows")
    items = raw if isinstance(raw, list) else []
    return ", ".join(
        f"{shown(w.get('name'))}@{shown(w.get('version'))}" for w in items if isinstance(w, dict)
    )


def _warnings(view: dict[str, Any]) -> list[str]:
    lines = []
    stale = view.get("stale")
    if isinstance(stale, list) and stale:
        reasons = "; ".join(shown(s) for s in stale)
        lines.append(f"    ✗ covers nothing now ({reasons}): sign a new grant with `r4 hpc grant`")
    if view.get("gate_cancel") == "pending":
        lines.append(
            "    ✗ UMRS stopped using it, but your gate has not recorded the cancel: "
            f"run `r4 hpc grant-cancel {shown(view.get('grant_id'))}` again"
        )
    return lines


def grant_lines(site: str, view: dict[str, Any]) -> list[str]:
    reason = view.get("ended_reason")
    status = shown(view.get("status"), 16) + (f" ({shown(reason, 24)})" if reason else "")
    return [
        f"{shown(view.get('grant_id'), 32)}  {status}  site {site}  "
        f"expires {shown(view.get('expires_at'), 40)}",
        f"    workflows {_workflows_text(view)}   data {shown(view.get('inputs_mode'), 16)}",
        f"    {_usage_text(view)}",
        *_warnings(view),
    ]
