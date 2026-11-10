"""``r4 workflow trigger --site``: the signed intent (ADR-112 wave B2, lane R).

Plan §5 B2.0 item 11 and the R tests of item 12: r4 recomputes the code
manifest from the export it read itself, and the step graph and configs with
the gate's shared functions; it refuses with a diff, signing nothing, on any
mismatch; the signature verifies with ``ssh-keygen -Y verify`` under
``umrs-intent-v1@<domain>`` and no other namespace; the Companion shell and a
missing terminal are refused. Keys are made with ``ssh-keygen`` in the
scratch HOME at test time; nothing reads ``~/.ssh``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from hpc_intent_fixtures import (
    ACCOUNT,
    DOMAIN,
    INPUT_FILES,
    LISTED_BYTES,
    LOGIN,
    PROFILE,
    PROJECT,
    RUN_ID,
    SITE,
    SOURCES,
    USER_PARAMS,
    WORKFLOW,
    canonical,
    export_zip,
    honest_intent,
    manifest_files,
    proposal_answer,
)
from r4_contracts import hpc_gate

from r4 import hpc_intents as hi
from r4 import hpc_signing as hs
from r4 import hpc_site_profile as sp
from r4.knowledge import COMPANION_SHELL_ENV

KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
needs_keygen = pytest.mark.skipif(not os.access(KEYGEN, os.X_OK), reason="needs ssh-keygen")
PROFILE_SHA = hashlib.sha256(canonical(PROFILE)).hexdigest()


@pytest.fixture
def recorded(monkeypatch):
    """What ``r4 hpc add`` and ``verify`` recorded for the site."""
    hs.update_site_record(
        SITE, venue=SITE, domain=DOMAIN, login=LOGIN, site_profile_sha256=PROFILE_SHA
    )
    sp.remember(SITE, sp.SiteProfile(profile=PROFILE, data=canonical(PROFILE), sha256=PROFILE_SHA))


@pytest.fixture
def person(monkeypatch, recorded):
    """A person at a terminal with an ed25519 signing key the site's gate trusts."""
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(hs, "_ALLOW_UNENCRYPTED_KEYS", True)
    key = hs.key_path("ed25519")
    key.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(key)], check=True)
    hs.record_site_signer(SITE, "ed25519", key)
    return key


class FakeSite:
    """UMRS as the B2.0 contract answers; ``mutate`` plays a dishonest server."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.config = SimpleNamespace(project_id=PROJECT)
        self.workflows = SimpleNamespace(export_bytes=self._export)
        self.zip = export_zip()
        self.mutate = lambda intent: None
        self.account = {"venue": SITE, "status": "active", "verified_login": LOGIN}
        self.links = [{"project_id": PROJECT, "slurm_account": ACCOUNT, "status": "active"}]

    def site_me(self):
        return {"user_id": "alice"}

    def hpc_account(self):
        return {"account": self.account, "links": self.links}

    def workflow_intent_proposal(self, name, body):
        self.calls.append(("proposal", name, body))
        intent = honest_intent(profile_sha256=PROFILE_SHA)
        self.mutate(intent)
        self.intent = intent
        return proposal_answer(intent)

    def _export(self, name, *, version, max_bytes):
        self.calls.append(("export", name, version))
        return self.zip

    def workflow_trigger_signed(self, name, body):
        self.calls.append(("trigger", name, body))
        return {"execution_id": RUN_ID, "status": "queued"}


def request(tmp_path: Path, **overrides: Any) -> hi.TriggerRequest:
    data = tmp_path / "a.csv"
    data.write_bytes(LISTED_BYTES)
    fields: dict[str, Any] = {
        "workflow": "wf",
        "version": "1.0",
        "params": dict(USER_PARAMS),
        "input_files": list(INPUT_FILES),
        "project": PROJECT,
        "listed": (hi.listed_input("prep", "s3://data/raw/a.csv", data),),
        "site": SITE,
        "yes": True,
    }
    fields.update(overrides)
    return hi.TriggerRequest(**fields)


def _no_signing(*_a, **_k):
    raise AssertionError("nothing may be signed")


# ── the code manifest (§3) ───────────────────────────────────────────────────


def test_manifest_is_deterministic_whatever_the_zip_order():
    first = hi.read_export(export_zip())
    again = hi.read_export(export_zip(reverse=True))
    assert first.files == again.files == manifest_files()
    paths = [e["path"] for e in first.files]
    assert paths == sorted(paths, key=lambda p: p.encode("utf-8"))
    assert {"workflow.json", "EXPORT.json"}.isdisjoint(paths)


def test_manifest_is_what_the_gate_expects():
    """Entry for entry what the gate's ``src_file_entry`` gives, and an intent
    carrying it passes the gate's ``validate_intent``."""
    files = hi.read_export(export_zip()).files
    order = sorted(SOURCES, key=lambda p: p.encode("utf-8"))
    assert files == [hpc_gate.src_file_entry(p, SOURCES[p]) for p in order]
    intent = honest_intent(profile_sha256=PROFILE_SHA)
    intent["src_manifest"]["files"] = files
    hpc_gate.validate_intent(hpc_gate.parse_strict(canonical(intent), json_values=True))


def test_never_trusts_export_json_digests():
    members = dict(SOURCES)
    tree = hi.read_export(export_zip(members))
    members["steps/prep.py"] += b"# changed\n"
    changed = hi.read_export(export_zip(members))
    assert tree.files != changed.files


def _zip(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("workflow.json", json.dumps(WORKFLOW))
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def _symlink(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = 0o120777 << 16
    return info


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        ([("steps/", b"")], "directory entry"),
        ([("a.py", b"1"), ("a.py", b"2")], "duplicate"),
        ([("a/../b.py", b"")], "not a normalized path"),
        ([("./a.py", b"")], "not a normalized path"),
        ([("/abs.py", b"")], "not a normalized path"),
        ([("a//b.py", b"")], "not a normalized path"),
        ([(_symlink("link.py"), b"/etc/passwd")], "not a regular file"),
        ([("a", b"x"), ("a/b.py", b"y")], "both a file and a directory"),
    ],
)
def test_a_bad_export_is_refused(entries, reason):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on the duplicate name
        data = _zip(entries)
    with pytest.raises(hi.IntentRefused, match=reason):
        hi.read_export(data)


def test_a_member_whose_size_differs_from_its_header_is_refused():
    data = bytearray(_zip([("a.py", b"0123456789")]))
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("a.py")
    # shrink the uncompressed size in the central directory record (stored member)
    central = data.rfind(b"PK\x01\x02")
    while data[central + 46 : central + 46 + 4] != b"a.py":
        central = data.rfind(b"PK\x01\x02", 0, central)
    data[central + 24 : central + 28] = (info.file_size - 1).to_bytes(4, "little")
    with pytest.raises(hi.IntentRefused):
        hi.read_export(bytes(data))


def test_normal_key_matches_umrs_normalize_key():
    staged = pytest.importorskip("umrs.pipeline_sdk.staged_s3")
    for value in [
        "a",
        "a/b",
        "naïve/x",
        "a//b",
        "/a",
        "a/",
        "./a",
        "a/./b",
        "a/../b",
        "..",
        "",
        "a\x00b",
        "a\nb",
        "x" * 256,
        "a/" + "y" * 255,
        "\udcff",
    ]:
        try:
            fixed = staged.normalize_key(value) == value
        except ValueError:
            fixed = False
        assert hi.is_normal_key(value) is fixed, repr(value)


# ── the happy path: sign, verify, trigger ───────────────────────────────────


def _verify(signature: str, payload: bytes, namespace: str, key: Path, tmp: Path) -> int:
    key_type, key_b64, _ = hs.public_key_info(key)
    signers = tmp / "allowed_signers"
    signers.write_text(hs.allowed_signers_line("alice", DOMAIN, key_type, key_b64) + "\n")
    sig = tmp / "intent.sig"
    sig.write_text(signature)
    argv = [KEYGEN, "-Y", "verify", "-f", str(signers), "-I", "alice", "-n", namespace]
    return subprocess.run([*argv, "-s", str(sig)], input=payload, capture_output=True).returncode


def _run_happy(tmp_path: Path) -> tuple[FakeSite, dict, dict]:
    site = FakeSite()
    answer = hi.run_gated_trigger(site, request(tmp_path), confirm=lambda p: True)
    (trigger,) = [c[2] for c in site.calls if c[0] == "trigger"]
    return site, answer, trigger


@needs_keygen
def test_the_proposal_carries_your_params_and_your_listed_digests(person, tmp_path):
    site, _, _ = _run_happy(tmp_path)
    (proposal,) = [c for c in site.calls if c[0] == "proposal"]
    listed = {
        "bucket": "data",
        "key": "raw/a.csv",
        "sha256": hashlib.sha256(LISTED_BYTES).hexdigest(),
        "size": len(LISTED_BYTES),
    }
    assert proposal[2] == {
        "project_id": PROJECT,
        "version": "1.0",
        "params": {**USER_PARAMS, "input_files": INPUT_FILES},
        "inputs": {"prep": [listed]},
    }
    assert ("export", "wf", "1.0") in site.calls


@needs_keygen
def test_the_trigger_carries_the_exact_intent_bytes_and_params(person, tmp_path, capsys):
    site, answer, trigger = _run_happy(tmp_path)
    payload = base64.b64decode(trigger["intent"])
    assert (payload, trigger["params"]) == (canonical(site.intent), site.intent["params"])
    assert (trigger["version"], trigger["project_id"]) == ("1.0", PROJECT)
    ident = hashlib.sha256(payload).hexdigest()
    assert answer["intent_id"] == ident
    assert ident in hi.signed_ids()
    out = capsys.readouterr().out
    assert hi.D10_TEXT in out
    assert ident in out


@needs_keygen
def test_the_signature_verifies_under_the_intent_namespace_only(person, tmp_path):
    _, _, trigger = _run_happy(tmp_path)
    payload, signature = base64.b64decode(trigger["intent"]), trigger["signature"]
    intent_ns = hpc_gate.namespace("intent", DOMAIN)
    control_ns = hpc_gate.namespace("control", DOMAIN)
    assert _verify(signature, payload, intent_ns, person, tmp_path) == 0
    assert _verify(signature, payload, control_ns, person, tmp_path) != 0
    assert _verify(signature, payload + b" ", intent_ns, person, tmp_path) != 0


@needs_keygen
def test_declining_signs_nothing(person, tmp_path, monkeypatch):
    site = FakeSite()
    monkeypatch.setattr(hi, "sign", _no_signing)
    with pytest.raises(hs.HpcSigningError, match="declined"):
        hi.run_gated_trigger(site, request(tmp_path, yes=False), confirm=lambda p: False)
    assert not [c for c in site.calls if c[0] == "trigger"]


# ── every mismatch refuses before signing (§11 step 5) ─────────────────────


def _set(path: list, value: Any):
    def mutate(intent: dict) -> None:
        target = intent
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    return mutate


def _extra_file(intent):
    intent["src_manifest"]["files"].append({"path": "zz_extra.py", "sha256": "0" * 64, "size": 1})


def _changed_file(intent):
    intent["src_manifest"]["files"][0]["sha256"] = "0" * 64


def _relabel(intent):
    intent["steps"][0]["inputs"]["objects"][0]["mode"] = "server_listed"


def _claim_server_object(intent):
    intent["steps"][0]["inputs"]["objects"][1]["mode"] = "listed"


def _listed_digest(intent):
    intent["steps"][0]["inputs"]["objects"][0]["sha256"] = "1" * 64


def _swap_steps(intent):
    intent["steps"].reverse()


def _future(intent):
    intent["issued"] += 1000
    intent["expires"] += 1000


def _no_chain(intent):
    intent["steps"][1]["inputs"]["chained"] = []


MISMATCHES = {
    "extra src file": (_extra_file, "src_manifest"),
    "changed src file": (_changed_file, "src_manifest"),
    "changed entrypoint": (
        _set(["src_manifest", "entrypoints", "prep"], "steps.prep:Other"),
        "entrypoints",
    ),
    "changed config value": (_set(["steps", 0, "config", "threshold"], 0.25), "config"),
    "int for float in config": (_set(["steps", 1, "config", "temperature"], 1), "config"),
    "extra param": (lambda i: i["params"].update(extra="x"), "params.extra"),
    "changed param": (lambda i: i["params"].update(alpha=0.5), "params.alpha"),
    "steps reordered": (_swap_steps, "steps:"),
    "chained dropped": (_no_chain, "chained"),
    "prefixes changed": (_set(["steps", 0, "inputs", "prefixes"], ["data/other/"]), "prefixes"),
    "another principal": (_set(["principal"], "mallory"), "principal:"),
    "another site": (_set(["site"], "other"), "site:"),
    "another login": (_set(["login"], "root"), "login:"),
    "another account": (_set(["account"], "alloc-2"), "account:"),
    "another project": (_set(["project"], "proj-2"), "project:"),
    "another version": (_set(["version"], "2.0"), "version:"),
    "listed relabelled": (_relabel, "relabelled"),
    "server object claimed as yours": (_claim_server_object, "did not list it"),
    "listed digest changed": (_listed_digest, "r4 hashed"),
    "another profile": (_set(["site_profile_sha256"], "9" * 64), "site_profile_sha256"),
    "future issued": (_future, "issued is"),
    "oversized max_run_seconds": (_set(["max_run_seconds"], 8400 + 1), "max_run_seconds"),
}


@needs_keygen
@pytest.mark.parametrize("name", sorted(MISMATCHES))
def test_a_mismatch_refuses_before_signing(person, tmp_path, monkeypatch, name):
    site = FakeSite()
    site.mutate, reason = MISMATCHES[name]
    monkeypatch.setattr(hi, "sign", _no_signing)
    with pytest.raises(hi.IntentRefused) as caught:
        hi.run_gated_trigger(site, request(tmp_path), confirm=lambda p: True)
    text = str(caught.value)
    assert "nothing was signed" in text
    assert reason in text, text  # named by r4's own comparison, not only the validator
    assert not [c for c in site.calls if c[0] == "trigger"]
    assert hi.signed_ids() == set()


def test_a_changed_config_value_is_shown_as_a_diff(tmp_path, recorded):
    site = FakeSite()
    site.mutate = _set(["steps", 0, "config", "threshold"], 0.25)
    with pytest.raises(hi.IntentRefused) as caught:
        hi.prepare(site, request(tmp_path), SITE)
    assert "threshold: proposed 0.25, r4 computed 0.5" in str(caught.value)


def test_the_listed_account_must_be_an_active_link(tmp_path, recorded):
    site = FakeSite()
    site.links = [{"project_id": PROJECT, "slurm_account": ACCOUNT, "status": "suspended"}]
    with pytest.raises(hi.IntentRefused, match="account"):
        hi.prepare(site, request(tmp_path), SITE)


def test_a_login_r4_never_recorded_is_refused(tmp_path, recorded):
    hs.update_site_record(SITE, login="someone-else")
    with pytest.raises(hi.IntentRefused, match="recorded"):
        hi.prepare(site := FakeSite(), request(tmp_path), SITE)
    assert site.calls  # the proposal was asked for, nothing else


def test_an_intent_whose_bytes_are_not_canonical_is_refused():
    intent = honest_intent(profile_sha256=PROFILE_SHA)
    payload = json.dumps(intent, indent=1).encode()
    with pytest.raises(hi.IntentRefused):
        hi.parse_proposal({"intent_b64": base64.b64encode(payload).decode()})


def test_an_intent_id_that_is_not_its_digest_is_refused():
    answer = proposal_answer(honest_intent(profile_sha256=PROFILE_SHA))
    answer["intent_id"] = "0" * 64
    with pytest.raises(hi.IntentRefused, match="intent_id"):
        hi.parse_proposal(answer)


# ── who may sign (I-6) ───────────────────────────────────────────────────────


def test_the_companion_shell_is_refused_before_any_request(monkeypatch, tmp_path, recorded):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    site = MagicMock()
    with pytest.raises(hs.HpcSigningError, match="person's act"):
        hi.run_gated_trigger(site, request(tmp_path), confirm=lambda p: True)
    assert site.mock_calls == []


def test_no_terminal_is_refused_before_any_request(monkeypatch, tmp_path, recorded):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: False)
    site = MagicMock()
    with pytest.raises(hs.HpcSigningError, match="needs a terminal"):
        hi.run_gated_trigger(site, request(tmp_path), confirm=lambda p: True)
    assert site.mock_calls == []


@needs_keygen
def test_signing_itself_refuses_in_the_companion_shell(person, monkeypatch):
    proposal = hi.parse_proposal(proposal_answer(honest_intent(profile_sha256=PROFILE_SHA)))
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    with pytest.raises(hs.HpcSigningError, match="person's act"):
        hi.sign_intent(proposal, site=SITE, domain=DOMAIN)
    assert hi.signed_ids() == set()


@needs_keygen
def test_an_ambiguous_legacy_key_refuses_a_new_intent(person):
    hs.record_site_signer("other-site", "ed25519", person)
    proposal = hi.parse_proposal(proposal_answer(honest_intent(profile_sha256=PROFILE_SHA)))
    with pytest.raises(hs.HpcSigningError, match="also recorded for other-site"):
        hi.sign_intent(proposal, site=SITE, domain=DOMAIN)
    assert hi.signed_ids() == set()


def test_the_client_refuses_a_signed_trigger_in_the_companion_shell(monkeypatch):
    from r4.client import Client
    from r4.config import Config
    from r4.errors import R4Error

    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client.http.post = MagicMock(return_value={})
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    with pytest.raises(R4Error, match="person's act"):
        client.workflow_trigger_signed("wf", {"intent": "x", "signature": "y"})
    assert client.http.post.call_count == 0


@pytest.mark.parametrize(
    "command",
    [
        "r4 workflow trigger wf --site bdrt",
        "r4 workflow trigger wf@1.0 --project p --site=bdrt --yes",
        "r4 workflow trigger wf --si bdrt",
        "r4 workflow run ./wf --site bdrt",
        "r4 workflow 'trigger' wf '--site' bdrt",
        "r4 hpc profile bdrt",
    ],
)
def test_signing_commands_are_fenced_never_auto(command):
    from r4.companion.permissions import _shell_command_fence

    assert _shell_command_fence("shell_exec", {"command": command}) == "never_auto"


@pytest.mark.parametrize(
    "command", ["r4 workflow trigger wf --param site=x", "r4 workflow list", "r4 workflow status x"]
)
def test_plain_workflow_commands_are_not_fenced(command):
    from r4.companion.permissions import _shell_command_fence

    assert _shell_command_fence("shell_exec", {"command": command}) is None


# ── the summary (§11 step 6, D-10) ──────────────────────────────────────────


def _summary(intent: dict, show_inputs: bool = False) -> str:
    proposal = hi.parse_proposal(proposal_answer(intent))
    steps = hi.expected_steps(
        hi.read_export(export_zip()), intent["params"], intent["input_bucket"]
    )
    return hi.render_summary(proposal, steps, show_inputs=show_inputs)


def test_the_summary_says_who_chose_each_input():
    text = _summary(honest_intent(profile_sha256=PROFILE_SHA))
    lines = text.splitlines()
    assert [line.strip() for line in lines if line.strip().startswith("s3://")] == [
        f"s3://data/raw/a.csv  8 B  sha256 {hashlib.sha256(LISTED_BYTES).hexdigest()[:12]}"
        "  [chosen by you]",
        "s3://data/raw/b.csv  2.0 KiB  sha256 ffffffffffff  [data chosen by UMRS]",
    ]
    assert "      outputs of step prep  [outputs of step prep]" in lines
    assert "      may list under data/raw/" in lines
    assert text.endswith("\n\n" + hi.D10_TEXT)


def test_the_d10_sentence_says_exactly_what_is_pinned():
    """Fixer finding 2026-11-08 (ADR-112 D-10 forbids unqualified claims):
    listed inputs are pinned at signing, earlier steps' outputs when the
    gate collects them; a step that reads a model endpoint gets live,
    unpinned answers, and the summary says so whenever (and only when) one
    does."""
    plain = _summary(honest_intent(profile_sha256=PROFILE_SHA))
    assert "every listed input are pinned" in hi.D10_TEXT and "when it collects them" in hi.D10_TEXT
    assert "every input are pinned" not in hi.D10_TEXT
    assert hi.D10_ENDPOINT_TEXT not in plain and hi.LABEL_ENDPOINT not in plain
    intent = honest_intent(profile_sha256=PROFILE_SHA)
    intent["steps"][0]["endpoint"] = {"host": "vllm.example", "port": 8000, "kind": "vllm"}
    text = _summary(intent)
    assert text.endswith(f"\n\n{hi.D10_TEXT} {hi.D10_ENDPOINT_TEXT}")
    assert f"endpoint vllm.example:8000 (vllm)  [{hi.LABEL_ENDPOINT}]" in text


def test_the_summary_shows_the_run_and_only_changed_config():
    text = _summary(honest_intent(profile_sha256=PROFILE_SHA))
    fields = (SITE, LOGIN, ACCOUNT, PROJECT, "wf@1.0", RUN_ID, "steps.fit:Fit", "no network")
    assert [f for f in fields if f not in text] == []
    assert "threshold" not in text  # unchanged from workflow.json
    assert "alpha = 0.25" in text  # a param that became config


def test_many_inputs_are_counted_unless_shown():
    intent = honest_intent(profile_sha256=PROFILE_SHA)
    objects = [
        {
            "mode": "server_listed",
            "bucket": "data",
            "key": f"raw/{i:03d}",
            "sha256": "f" * 64,
            "size": 10,
        }
        for i in range(25)
    ]
    intent["steps"][0]["inputs"]["objects"] = objects
    short = _summary(intent)
    assert "25 objects, 250 B  [data chosen by UMRS]" in short and "raw/007" not in short
    assert "raw/007" in _summary(intent, show_inputs=True)


# ── the witness (I-9) ───────────────────────────────────────────────────────


def _log(tmp_path: Path, details: list[tuple[str, str, str]]) -> bytes:
    path = tmp_path / "gate.jsonl"
    log = hpc_gate.GateLog(str(path))
    log.open()
    try:
        for n, (verb, result, detail) in enumerate(details):
            log.append(verb, result, detail, 1_700_000_000 + n)
    finally:
        log.close()
    return path.read_bytes()


def test_the_witness_flags_an_intent_r4_did_not_sign(tmp_path):
    mine, theirs = "a" * 64, "b" * 64
    hi.record_signed(mine, SITE)
    data = _log(
        tmp_path,
        [
            ("hello", "ok", ""),
            ("submit", "ok", f"intent={mine} step=prep wd=w job=1 signer=SHA256:x"),
            ("submit", "ok", f"intent={theirs} step=fit wd=w job=2 signer=SHA256:x"),
        ],
    )
    report = hi.witness_site(SITE, data)
    assert report.chain_ok and not report.forked
    assert [i for _, i in report.submits] == [mine, theirs] and report.unknown == [theirs]
    assert hs.read_site_record(SITE)["witness"] == report.head == {"n": 3, "h": report.head["h"]}
    again = hi.witness_site(SITE, data)
    assert again.submits == [] and again.unknown == [theirs]  # fixer 2026-11-08: kept


def test_an_acknowledged_unknown_intent_is_no_longer_reported(tmp_path):
    theirs = "b" * 64
    data = _log(tmp_path, [("submit", "ok", f"intent={theirs} step=fit wd=w job=2 signer=x")])
    assert hi.witness_site(SITE, data).unknown == [theirs]
    assert hi.acknowledge_unknown(SITE, theirs) is True
    assert hi.acknowledge_unknown(SITE, theirs) is False
    assert hi.witness_site(SITE, data).unknown == []


def test_an_unacknowledged_unknown_intent_is_reported_on_every_witness(tmp_path):
    """Fixer finding 2026-11-08 (I-9): the head moves on after the first
    witness; the alert must not, or a missed first warning is lost."""
    theirs = "c" * 64
    first = _log(tmp_path, [("submit", "ok", f"intent={theirs} step=a wd=w job=1 signer=x")])
    assert hi.witness_site(SITE, first).unknown == [theirs]
    path = tmp_path / "gate.jsonl"
    log = hpc_gate.GateLog(str(path))
    log.open()
    try:
        log.append("hello", "ok", "", 1_700_000_100)
    finally:
        log.close()
    for _ in range(2):
        report = hi.witness_site(SITE, path.read_bytes())
        assert report.submits == [] and report.unknown == [theirs]


def test_status_witness_exits_1_until_the_person_acks(tmp_path, monkeypatch, capsys):
    from r4 import hpc_remote
    from r4._cli import hpc as hpc_cli
    from r4.cli import _build_parser

    theirs = "d" * 64
    data = _log(tmp_path, [("submit", "ok", f"intent={theirs} step=a wd=w job=1 signer=x")])
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda _host: data)
    monkeypatch.setattr(hpc_cli, "_host", lambda _args: "login.example")
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: SimpleNamespace(hpc_gate=dict))
    parse = _build_parser().parse_args
    assert hpc_cli._witness(parse(["hpc", "status", SITE, "--witness"])) == 1
    assert hpc_cli._witness(parse(["hpc", "status", SITE, "--witness"])) == 1
    assert f"intent {theirs} was not signed" in capsys.readouterr().err
    acked = parse(["hpc", "status", SITE, "--witness", "--ack", theirs])
    assert hpc_cli._witness(acked) == 0
    assert f"acknowledged: intent {theirs}" in capsys.readouterr().out


def test_the_witness_detects_a_fork_and_keeps_the_head(tmp_path):
    data = _log(tmp_path, [("hello", "ok", ""), ("probe", "refused", "")])
    head = hi.witness_site(SITE, data).head
    (tmp_path / "other").mkdir()
    other = _log(tmp_path / "other", [("hello", "ok", "x")])
    report = hi.witness_site(SITE, other)
    assert report.forked and hs.read_site_record(SITE)["witness"] == head


def test_the_witness_refuses_a_broken_chain(tmp_path):
    data = _log(tmp_path, [("hello", "ok", ""), ("probe", "refused", "")])
    broken = data.replace(b'"probe"', b'"hello"')
    assert hi.witness_site(SITE, broken).chain_ok is False
    assert "witness" not in hs.read_site_record(SITE)


# ── the CLI ─────────────────────────────────────────────────────────────────


def _parse(argv: list[str]) -> Namespace:
    from r4.cli import _build_parser

    return _build_parser().parse_args(["workflow", *argv])


def test_trigger_parses_the_gated_options(tmp_path):
    args = _parse(
        [
            "trigger",
            "wf@1.0",
            "--site",
            SITE,
            "--listed-input",
            "prep",
            "s3://data/raw/a.csv",
            str(tmp_path / "a.csv"),
            "--show-inputs",
            "--yes",
            "--project",
            PROJECT,
        ]
    )
    assert (args.site, args.yes, args.show_inputs) == (SITE, True, True)
    assert args.listed_input == [["prep", "s3://data/raw/a.csv", str(tmp_path / "a.csv")]]
    assert hi.split_name(args.workflow_name) == ("wf", "1.0")
    assert hi.split_name("wf") == ("wf", None)
    assert _parse(["trigger", "wf"]).site is None


def test_trigger_with_site_runs_the_gated_flow(monkeypatch, tmp_path, capsys):
    from r4._cli import hpc as hpc_cli
    from r4._cli import workflow as wf_cli

    data = tmp_path / "a.csv"
    data.write_bytes(LISTED_BYTES)
    seen = {}
    site = SimpleNamespace(config=SimpleNamespace(project_id=PROJECT))
    monkeypatch.setattr(hpc_cli, "_site_client", lambda name: site)

    def fake_run(client, req, *, confirm):
        seen.update(client=client, req=req)
        return {"execution_id": RUN_ID, "status": "queued", "intent_id": "f" * 64}

    monkeypatch.setattr(hi, "run_gated_trigger", fake_run)
    args = _parse(
        [
            "trigger",
            "wf@1.0",
            "--site",
            SITE,
            "--param",
            "label=run",
            "--listed-input",
            "prep",
            "s3://data/raw/a.csv",
            str(data),
        ]
    )
    wf = SimpleNamespace(trigger=lambda **k: pytest.fail("no plain trigger"))
    assert wf_cli._cmd_trigger(wf, args) == 0
    req = seen["req"]
    assert seen["client"] is site and (req.workflow, req.version, req.site) == ("wf", "1.0", SITE)
    assert req.project == PROJECT and req.params == {"label": "run"}
    assert req.listed[0].sha256 == hashlib.sha256(LISTED_BYTES).hexdigest()
    assert "intent_id    : " + "f" * 64 in capsys.readouterr().out


def test_a_428_from_a_plain_trigger_starts_the_gated_flow(monkeypatch):
    from r4._cli import workflow as wf_cli
    from r4.errors import APIError

    seen = {}

    def plain(**_k):
        raise APIError(
            "signature required", status_code=428, error_code="hpcaccess.signature_required"
        )

    def fake_run(client, req, *, confirm):
        seen.update(client=client, req=req)
        return {"execution_id": RUN_ID}

    monkeypatch.setattr(hi, "run_gated_trigger", fake_run)
    home = SimpleNamespace(config=SimpleNamespace(project_id=PROJECT))
    wf = SimpleNamespace(trigger=plain, _parent=home)
    assert wf_cli._cmd_trigger(wf, _parse(["trigger", "wf"])) == 0
    assert seen["client"] is home and seen["req"].site is None


def test_a_428_fallback_ignores_yes_and_asks_at_the_terminal(monkeypatch):
    """Fixer finding 2026-11-08 (D-8): the never-auto fence matches only a
    ``--site`` trigger, so a plain ``trigger --yes`` that meets a 428 must
    not skip the person's own confirmation."""
    from r4._cli import workflow as wf_cli
    from r4.errors import APIError

    seen = {}

    def plain(**_k):
        raise APIError(
            "signature required", status_code=428, error_code="hpcaccess.signature_required"
        )

    def fake_run(client, req, *, confirm):
        seen["yes"] = req.yes
        return {"execution_id": RUN_ID}

    monkeypatch.setattr(hi, "run_gated_trigger", fake_run)
    home = SimpleNamespace(config=SimpleNamespace(project_id=PROJECT))
    wf = SimpleNamespace(trigger=plain, _parent=home)
    args = _parse(["trigger", "wf", "--yes"])
    assert args.yes is True
    assert wf_cli._cmd_trigger(wf, args) == 0
    assert seen == {"yes": False}


def test_a_gated_refusal_exits_1_with_the_diff(monkeypatch, capsys):
    from r4._cli import hpc as hpc_cli
    from r4._cli import workflow as wf_cli

    monkeypatch.setattr(hpc_cli, "_site_client", lambda name: SimpleNamespace(config=None))

    def refuse(*_a, **_k):
        raise hi.IntentRefused(["params.extra: added by the server"])

    monkeypatch.setattr(hi, "run_gated_trigger", refuse)
    assert wf_cli._cmd_trigger(SimpleNamespace(), _parse(["trigger", "wf", "--site", SITE])) == 1
    err = capsys.readouterr().err
    assert "nothing was signed" in err and "params.extra" in err


@pytest.mark.parametrize(("direct", "prefix"), [(False, "/gw"), (True, "/umrs")])
def test_the_b2_client_routes_match_the_contract(direct, prefix, monkeypatch):
    from r4.client import Client
    from r4.config import Config

    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client._is_umrs_direct = direct
    client.http.get = MagicMock(return_value={})
    client.http.post = MagicMock(return_value={})
    client.hpc_site_profile()
    client.hpc_gate()
    client.workflow_intent_proposal("my wf", {"params": {}})
    client.workflow_trigger_signed("my wf", {"intent": "i", "signature": "s"})
    base = f"http://gw:8080{prefix}"
    assert [c.args[0] for c in client.http.get.call_args_list] == [
        f"{base}/sites/me/hpc/site-profile",
        f"{base}/sites/me/hpc/gate",
    ]
    posts = client.http.post.call_args_list
    assert [c.args[0] for c in posts] == [
        f"{base}/workflows/my%20wf/intent-proposal",
        f"{base}/workflows/my%20wf/trigger",
    ]
    assert posts[0].kwargs["timeout"] == 330


def test_export_bytes_asks_for_the_version_and_caps_the_body(monkeypatch):
    from r4.errors import R4Error
    from r4.workflows import WorkflowClient

    seen = {}

    def stream(self, name, handle, *, version=None):
        seen.update(name=name, version=version)
        handle.write(b"x" * 10)

    monkeypatch.setattr(WorkflowClient, "_stream_export", stream)
    wf = WorkflowClient(SimpleNamespace())
    assert wf.export_bytes("wf", version="1.0", max_bytes=10) == b"x" * 10
    assert seen == {"name": "wf", "version": "1.0"}
    with pytest.raises(R4Error, match="larger than 9"):
        wf.export_bytes("wf", version="1.0", max_bytes=9)


def test_a_listed_input_must_name_a_normalized_s3_key(tmp_path):
    data = tmp_path / "f"
    data.write_bytes(b"1")
    for uri in ("data/raw/a.csv", "s3://data", "s3://data/a//b", "s3:///a"):
        with pytest.raises(hs.HpcSigningError, match="--listed-input"):
            hi.listed_input("prep", uri, data)
    entry = hi.listed_input("prep", "s3://data/a.csv", data)
    assert (entry.bucket, entry.key, entry.size) == ("data", "a.csv", 1)
