"""``r4 hpc grant`` and ``grant-cancel``: signed standing permissions.

Plan §6, the C/D.0 wire contract (C1, C3, C5) and the lane R tests with the
critic's amendments K7-K9, K16, K17 and K20. r4 recomputes the code manifest,
the step skeleton and the parameter schema from the export it read itself
with the gate's shared code, and refuses with a diff, signing nothing, on any
difference; a parameter that could carry code refuses by name; the summary
says exactly what UMRS still decides; the signature verifies with
``ssh-keygen -Y verify`` under ``umrs-grant-v1@<domain>`` only. Keys are made
with ``ssh-keygen`` in the scratch HOME at test time; nothing reads ``~/.ssh``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from hpc_intent_fixtures import (
    ACCOUNT,
    DOMAIN,
    IMAGE,
    LOGIN,
    PROJECT,
    SITE,
    SOURCES,
    TRUSTED,
    WORKFLOW,
    export_zip,
    manifest_files,
)
from r4_contracts import hpc_gate

from r4 import hpc_grants as hg
from r4 import hpc_signing as hs
from r4.knowledge import COMPANION_SHELL_ENV

KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
needs_keygen = pytest.mark.skipif(not os.access(KEYGEN, os.X_OK), reason="needs ssh-keygen")
NOW = 1_800_000_000

SCHEMA = {
    "type": "object",
    "properties": {
        "alpha": {"type": "number", "minimum": 0.5, "maximum": 9.7, "description": "rate"},
        "label": {"type": "string", "title": "Label"},
        "mode": {"type": "string", "enum": ["fast", "slow"], "maxLength": 8},
        "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
    },
    "required": ["n"],
}


def grant_workflow(name: str = "wf", schema: Optional[dict] = None, **step0: Any) -> dict:
    """The fixture workflow with a parameter schema; step ``prep`` declares
    the step image, ``fit`` runs on the platform image."""
    wf = copy.deepcopy(WORKFLOW)
    wf["name"] = name
    wf["parameter_schema"] = copy.deepcopy(SCHEMA if schema is None else schema)
    wf["steps"][0]["routing_hints"] = {"image": IMAGE["ref"], **step0}
    return wf


def _entrypoints(wf: dict) -> dict[str, Any]:
    return {s["name"]: s["entrypoint"] for s in wf["steps"]}


def honest_entry(wf: dict, version: str = "1.0") -> dict[str, Any]:
    """What a correct proposal holds for ``wf`` (computed with the gate's
    shared code, as the server does)."""
    manifest = {"files": manifest_files(), "entrypoints": _entrypoints(wf)}
    return {
        "name": wf["name"],
        "version": version,
        "src_manifest_sha256": hpc_gate.src_manifest_sha256(manifest),
        "steps_sha256": hpc_gate.steps_sha256(hpc_gate.workflow_steps_skeleton(wf)),
        "parameter_schema": wf["parameter_schema"],
        "images": [dict(IMAGE)],
        "trusted_image": dict(TRUSTED),
    }


class GrantSite:
    """UMRS as the C/D.0 contract answers; tests mutate it to lie."""

    def __init__(self, *workflows: dict) -> None:
        self.workflows_json = {wf["name"]: wf for wf in (workflows or (grant_workflow(),))}
        self.calls: list[tuple] = []
        self.budget: dict[str, Any] = {"max_gpu_hours": 0, "remaining_gpu_hours": None}
        self.account = {"venue": SITE, "status": "active", "verified_login": LOGIN}
        self.links = [{"project_id": PROJECT, "slurm_account": ACCOUNT, "status": "active"}]
        self.mutate = lambda answer: None
        self.cancel_answer: Optional[dict[str, Any]] = None
        self.gate_log = b""
        self.workflows = SimpleNamespace(export_bytes=self._export)

    def site_me(self):
        return {"user_id": "alice"}

    def hpc_account(self):
        return {"account": self.account, "links": self.links}

    def hpc_grant_proposal(self, body):
        self.calls.append(("proposal", body))
        answer = {
            "site": SITE,
            "domain": DOMAIN,
            "login": LOGIN,
            "accounts": [ACCOUNT],
            "workflows": [honest_entry(self.workflows_json[w["name"]]) for w in body["workflows"]],
            "site_profile_sha256": "0" * 64,
            "budget": dict(self.budget),
        }
        self.mutate(answer)
        return answer

    def _export(self, name, *, version, max_bytes):
        self.calls.append(("export", name, version))
        return export_zip(SOURCES, self.workflows_json[name])

    def hpc_grant_store(self, envelope):
        self.calls.append(("store", envelope))
        return {"grant": {"status": "active"}}

    def hpc_grant_cancel(self, grant_id, envelope):
        self.calls.append(("cancel", grant_id, envelope))
        if self.cancel_answer is not None:
            return self.cancel_answer
        with tempfile.TemporaryDirectory(prefix="r4-cancel-test-") as tmp:
            path = Path(tmp) / "gate.jsonl"
            log = hpc_gate.GateLog(str(path))
            log.open()
            log.append("grant-cancel", "ok", f"grant={grant_id} signer=SHA256:x", NOW)
            head = log.head()
            log.close()
            self.gate_log = path.read_bytes()
        result = {
            "ok": True,
            "op": "grant-cancel",
            "grant_id": grant_id,
            "already": False,
            "signer": "SHA256:x",
            "log_head": head,
        }
        return {
            "relayed": True,
            "result": result,
            "grant": {"status": "cancelled", "gate_cancel": "recorded"},
        }


@pytest.fixture
def recorded():
    hs.update_site_record(SITE, venue=SITE, domain=DOMAIN, login=LOGIN)


@pytest.fixture
def person(monkeypatch, recorded):
    """A person at a terminal with an ed25519 signing key the gate trusts."""
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(hs, "_ALLOW_UNENCRYPTED_KEYS", True)
    key = hs.key_path("ed25519")
    key.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(key)], check=True)
    hs.record_site_signer(SITE, "ed25519", key)
    return key


def request(**over: Any) -> hg.GrantRequest:
    fields: dict[str, Any] = {
        "site": SITE,
        "project": PROJECT,
        "workflows": (("wf", "1.0"),),
        "max_jobs": 10,
        "max_gpu_hours": 5,
        "days": 7,
        "yes": True,
    }
    fields.update(over)
    return hg.GrantRequest(**fields)


def _store(site: GrantSite) -> dict[str, str]:
    (envelope,) = [c[1] for c in site.calls if c[0] == "store"]
    return envelope


def _grant(site: GrantSite) -> dict[str, Any]:
    return hpc_gate.parse_strict(base64.b64decode(_store(site)["payload"]))


def _no_signing(*_a: Any, **_k: Any) -> None:
    raise AssertionError("nothing may be signed")


def _cancel_receipt(grant_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "op": "grant-cancel",
        "grant_id": grant_id,
        "already": False,
        "signer": "SHA256:x",
        "log_head": {"n": 2, "h": "b" * 64},
    }


def _cancel(site: GrantSite, grant_id: str, site_name: Optional[str] = SITE, **kw: Any):
    kw.setdefault("gate_log", lambda: site.gate_log)
    return hg.cancel_grant(site, grant_id, site_name, **kw)


def _verify(signature: str, payload: bytes, namespace: str, key: Path, tmp: Path) -> int:
    key_type, key_b64, _ = hs.public_key_info(key)
    signers = tmp / "allowed_signers"
    signers.write_text(hs.allowed_signers_line("alice", DOMAIN, key_type, key_b64) + "\n")
    sig = tmp / "grant.sig"
    sig.write_text(signature)
    argv = [KEYGEN, "-Y", "verify", "-f", str(signers), "-I", "alice", "-n", namespace]
    return subprocess.run([*argv, "-s", str(sig)], input=payload, capture_output=True).returncode


def _run(site: GrantSite, req: Optional[hg.GrantRequest] = None, **kw: Any) -> dict[str, Any]:
    kw.setdefault("confirm", lambda _p: True)
    kw.setdefault("out", lambda _t: None)
    kw.setdefault("clock", lambda: NOW)
    return hg.run_grant(site, req or request(), **kw)


# ── the happy path ───────────────────────────────────────────────────────────


@needs_keygen
def test_the_grant_verifies_under_the_grant_namespace_only(person, tmp_path):
    site = GrantSite()
    answer = _run(site)
    envelope = _store(site)
    payload = base64.b64decode(envelope["payload"])
    grant_ns = hpc_gate.namespace("grant", DOMAIN)
    assert _verify(envelope["signature"], payload, grant_ns, person, tmp_path) == 0
    for kind in ("intent", "control"):
        other = hpc_gate.namespace(kind, DOMAIN)
        assert _verify(envelope["signature"], payload, other, person, tmp_path) != 0
    grant = hpc_gate.parse_strict(payload)
    hpc_gate.validate_grant(grant)
    assert payload == hpc_gate.canonical_bytes(grant)
    assert answer["grant_id"] == grant["grant_id"]


@needs_keygen
def test_the_grant_carries_what_the_person_asked_for(person):
    site = GrantSite()
    _run(site)
    grant = _grant(site)
    honest = honest_entry(grant_workflow())
    want = {
        "site": SITE,
        "login": LOGIN,
        "principal": "alice",
        "accounts": [ACCOUNT],
        "projects": [PROJECT],
        "caps": {"max_jobs": 10, "max_gpu_hours": 5, "max_core_hours": 0, "max_concurrent": 4},
        "inputs": {"mode": "chained", "listed": []},
        "delegable": False,
        "issued": NOW,
        "not_before": NOW,
        "expires": NOW + 7 * 86400,
        "images": [IMAGE, TRUSTED],  # strictly increasing by image_id ("b…" < "d…")
        "trusted_images": [TRUSTED],  # helper/posture role, separately bound
        "workflows": [
            {
                "name": "wf",
                "version": "1.0",
                "src_manifest_sha256": honest["src_manifest_sha256"],
                "steps_sha256": honest["steps_sha256"],
                "params": ["alpha", "label", "mode", "n"],
            }
        ],
    }
    assert {key: grant[key] for key in want} == want
    assert ("export", "wf", "1.0") in site.calls


@needs_keygen
def test_the_local_record_keeps_the_caps_for_the_witness(person):
    site = GrantSite()
    _run(site)
    grant = _grant(site)
    record = hg.read_grant_record(grant["grant_id"])
    payload = base64.b64decode(_store(site)["payload"])
    assert record == {
        "site": SITE,
        "venue": SITE,
        "grant_sha256": hashlib.sha256(payload).hexdigest(),
        "expires": grant["expires"],
        "caps": grant["caps"],
    }
    assert grant["grant_id"] in hg.signed_grant_ids()
    assert oct(hg.grants_dir().stat().st_mode & 0o777) == "0o700"


@needs_keygen
def test_the_times_are_stamped_after_the_confirmation(person):
    """K8: a slow confirmation never leaves ``issued`` outside the store's
    window."""
    clock = {"now": NOW}

    def slow_confirm(_prompt: str) -> bool:
        clock["now"] += 3600
        return True

    site = GrantSite()
    _run(site, request(yes=False), confirm=slow_confirm, clock=lambda: clock["now"])
    grant = _grant(site)
    assert grant["issued"] == grant["not_before"] == NOW + 3600
    assert grant["expires"] == NOW + 3600 + 7 * 86400


@needs_keygen
def test_declining_signs_and_stores_nothing(person, monkeypatch):
    monkeypatch.setattr(hg, "sign", _no_signing)
    site = GrantSite()
    with pytest.raises(hs.HpcSigningError, match="declined"):
        _run(site, request(yes=False), confirm=lambda _p: False)
    assert not [c for c in site.calls if c[0] == "store"] and hg.signed_grant_ids() == set()


@needs_keygen
def test_listed_inputs_are_hashed_by_r4(person, tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_bytes(b"1,2\n")
    b.write_bytes(b"3,4,5\n")
    site = GrantSite()
    _run(site, request(inputs="listed", listed_paths=(a, b, a)))
    listed = _grant(site)["inputs"]["listed"]
    want = sorted(
        (
            {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size}
            for p in (a, b)
        ),
        key=lambda e: e["sha256"],
    )
    assert listed == want


# ── who may sign (I-6) and the fork refusal (K20) ───────────────────────────


@pytest.mark.parametrize(
    ("companion", "tty", "why"), [(True, True, "person's act"), (False, False, "needs a terminal")]
)
def test_grant_refuses_before_any_request(monkeypatch, recorded, companion, tty, why):
    if companion:
        monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    else:
        monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: tty)
    site = MagicMock()
    with pytest.raises(hs.HpcSigningError, match=why):
        _run(site)
    assert site.mock_calls == []


def test_a_recorded_fork_refuses_a_grant_before_any_request(monkeypatch, recorded):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    hs.update_site_record(SITE, witness_fork={"seen_at": "t", "reason": "r"})
    site = MagicMock()
    with pytest.raises(hs.HpcSigningError, match="FORK"):
        _run(site)
    assert site.mock_calls == []


def test_the_client_refuses_to_store_or_cancel_in_the_companion_shell(monkeypatch):
    from r4.client import Client
    from r4.config import Config
    from r4.errors import R4Error

    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client.http.post = MagicMock(return_value={})
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    with pytest.raises(R4Error, match="person's act"):
        client.hpc_grant_store({"payload": "p", "signature": "s"})
    with pytest.raises(R4Error, match="person's act"):
        client.hpc_grant_cancel("a" * 32, {"payload": "p", "signature": "s"})
    assert client.http.post.call_count == 0


@pytest.mark.parametrize(
    "command",
    [
        "r4 hpc grant --site bdrt --project p --workflow wf --max-jobs 1",
        "r4 hpc grant-cancel 0123456789abcdef0123456789abcdef",
        "r4 hpc 'grant-cancel' x --site bdrt",
        "r4 --profile x hpc grant-cancel x",
    ],
)
def test_signing_commands_are_fenced_never_auto(command):
    from r4.companion.permissions import _shell_command_fence

    assert _shell_command_fence("shell_exec", {"command": command}) == "never_auto"


# ── refusals before signing ─────────────────────────────────────────────────


def _refused(site: GrantSite, monkeypatch, match: str, req: Optional[hg.GrantRequest] = None):
    monkeypatch.setattr(hg, "sign", _no_signing)
    with pytest.raises(hg.IntentRefused, match=match):
        _run(site, req)
    assert not [c for c in site.calls if c[0] == "store"]


@needs_keygen
@pytest.mark.parametrize("key", ["src_manifest_sha256", "steps_sha256"])
def test_a_digest_the_export_does_not_give_is_refused_with_a_diff(person, monkeypatch, key):
    site = GrantSite()
    site.mutate = lambda answer: answer["workflows"][0].update({key: "0" * 64})
    _refused(site, monkeypatch, f"{key}: proposed 0000")


@needs_keygen
def test_a_changed_step_config_changes_the_steps_digest(person, monkeypatch):
    """The server's skeleton (its registered version) differs from the
    export: r4 refuses rather than sign a graph it did not read."""
    site = GrantSite()
    served = grant_workflow()
    served["steps"][1]["config"]["temperature"] = 2.0
    site.mutate = lambda answer: answer["workflows"][0].update(
        steps_sha256=hpc_gate.steps_sha256(hpc_gate.workflow_steps_skeleton(served))
    )
    _refused(site, monkeypatch, "steps_sha256")


@needs_keygen
def test_an_image_no_step_declares_is_refused(person, monkeypatch):
    site = GrantSite()
    rogue = {"ref": "ghcr.io/evil/x:1", "image_id": "9" * 64, "archive_sha256": "8" * 64}
    site.mutate = lambda answer: answer["workflows"][0]["images"].append(rogue)
    _refused(site, monkeypatch, "ghcr.io/evil/x:1")


@needs_keygen
@pytest.mark.parametrize(
    ("field", "value"),
    [("login", "mallory"), ("accounts", ["other"]), ("site", "rccs"), ("domain", "x.org")],
)
def test_a_proposal_naming_another_identity_is_refused(person, monkeypatch, field, value):
    site = GrantSite()
    site.mutate = lambda answer: answer.update({field: value})
    _refused(site, monkeypatch, f"proposal {field}")


@needs_keygen
def test_a_proposal_with_an_extra_workflow_is_refused(person, monkeypatch):
    site = GrantSite(grant_workflow(), grant_workflow("other"))
    site.mutate = lambda answer: answer["workflows"].append(
        honest_entry(site.workflows_json["other"])
    )
    _refused(site, monkeypatch, "lists 2 workflows")


@needs_keygen
@pytest.mark.parametrize(
    ("prop", "name"),
    [
        ({"type": "object"}, "cfg"),
        ({"type": "array"}, "cfg"),
        ({"type": "null"}, "cfg"),
        ({"type": ["string", "null"]}, "cfg"),
        ({"description": "no type"}, "cfg"),
        ({"type": "string", "format": "uri"}, "cfg"),
        ({"type": "string", "pattern": "^a"}, "cfg"),
        ({"type": "string", "items": {}}, "cfg"),
        ({"type": "string", "$ref": "#/x"}, "cfg"),
        ({"type": "string", "x-umrs-code": True}, "cfg"),
        ({"type": "string"}, "script"),
        ({"type": "string"}, "pre_cmd"),
        ({"type": "string"}, "source"),
        ({"type": "string"}, "input_path"),
        ({"type": "string"}, "model_url"),
        ({"type": "string"}, "system_prompt"),
        ({"type": "string"}, "Entrypoint"),
        ({"type": "string"}, "api_base"),
        ({"type": "string"}, "base_url"),
        ({"type": "string"}, "image_param"),
        ({"type": "string"}, "_hidden"),
    ],
)
def test_a_parameter_that_could_carry_code_is_refused_by_name(person, monkeypatch, prop, name):
    schema = {"type": "object", "properties": {name: prop}}
    _refused(GrantSite(grant_workflow(schema=schema)), monkeypatch, name)


@needs_keygen
def test_a_float_enum_is_refused(person, monkeypatch):
    """K9d: a grant holds no float."""
    schema = {"type": "object", "properties": {"rate": {"type": "number", "enum": [0.5, 1]}}}
    _refused(GrantSite(grant_workflow(schema=schema)), monkeypatch, "rate")


@needs_keygen
def test_a_float_range_that_holds_no_whole_number_is_refused(person, monkeypatch):
    schema = {
        "type": "object",
        "properties": {"rate": {"type": "number", "minimum": 0.2, "maximum": 0.8}},
    }
    _refused(GrantSite(grant_workflow(schema=schema)), monkeypatch, "rate")


@needs_keygen
def test_two_workflows_declaring_one_name_differently_are_refused(person, monkeypatch):
    other = grant_workflow(
        "other", schema={"type": "object", "properties": {"n": {"type": "string"}}}
    )
    site = GrantSite(grant_workflow(), other)
    _refused(
        site,
        monkeypatch,
        "grant them separately",
        request(workflows=(("wf", "1.0"), ("other", "1.0"))),
    )


@needs_keygen
def test_each_workflow_lists_only_its_own_parameters(person):
    """K9a: a run of one workflow never receives a name only another declares."""
    other = grant_workflow(
        "other",
        schema={
            "type": "object",
            "properties": {"n": SCHEMA["properties"]["n"], "depth": {"type": "integer"}},
            "required": [],
        },
    )
    site = GrantSite(grant_workflow(), other)
    _run(site, request(workflows=(("wf", "1.0"), ("other", "1.0"))))
    grant = _grant(site)
    params = {w["name"]: w["params"] for w in grant["workflows"]}
    assert params == {"other": ["depth", "n"], "wf": ["alpha", "label", "mode", "n"]}
    assert sorted(grant["param_schema"]["properties"]) == ["alpha", "depth", "label", "mode", "n"]
    assert grant["param_schema"]["required"] == []  # the intersection


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"inputs": "listed"}, "needs at least one --listed-input"),
        ({"inputs": "any", "listed_paths": (Path("x"),)}, "refused with --inputs any"),
        ({"days": 31}, "--days"),
        ({"days": 0}, "--days"),
        ({"max_concurrent": 11}, "at most --max-jobs"),
        ({"max_jobs": 0}, "--max-jobs"),
        ({"max_gpu_hours": -1}, "--max-gpu-hours"),
        ({"workflows": (("wf", None), ("wf", "1.0"))}, "named once"),
    ],
)
def test_the_command_lines_own_limits(over, match):
    assert any(match in p for p in hg.request_problems(request(**over)))


def test_max_concurrent_defaults_to_four_but_never_above_max_jobs():
    assert request(max_jobs=2).caps()["max_concurrent"] == 2
    assert request(max_jobs=50).caps()["max_concurrent"] == 4


# ── the summary (K9, K16, D-10) ─────────────────────────────────────────────


@needs_keygen
def test_the_summary_shows_narrowed_bounds_default_lengths_and_free_text(person):
    shown: list[str] = []
    site = GrantSite()
    _run(site, out=shown.append)
    props = _grant(site)["param_schema"]["properties"]
    assert (props["alpha"], props["label"], props["n"]) == (
        {"type": "number", "minimum": 1, "maximum": 9},
        {"type": "string", "maxLength": 1024},
        {"type": "integer", "minimum": 1, "maximum": 10},
    )
    words = [line.split() for line in "\n".join(shown).splitlines()]
    lines = {w[0]: " ".join(w) for w in words if w and w[0] in props}
    assert [lines[name] for name in ("alpha", "label", "mode", "n")] == [
        "alpha number from 1 to 9 (declared minimum 0.5, narrowed to a whole number; "
        "declared maximum 9.7, narrowed to a whole number)",
        "label string at most 1024 characters (no length declared; r4 set 1024; "
        "free text UMRS chooses)",
        'mode string one of "fast", "slow"',
        "n integer from 1 to 10",
    ]


def _summary(mode: str, listed: int = 0) -> str:
    body = {
        "site": SITE,
        "login": LOGIN,
        "accounts": [ACCOUNT],
        "projects": [PROJECT],
        "grant_id": "a" * 32,
        "expires": NOW,
        "workflows": [],
        "images": [],
        "param_schema": {"properties": {}},
        "caps": {"max_jobs": 1, "max_gpu_hours": 0, "max_core_hours": 0, "max_concurrent": 1},
        "inputs": {"mode": mode, "listed": [{"sha256": "0" * 64, "size": 1}] * listed},
    }
    return hg.render_summary(body, [], 1)


@pytest.mark.parametrize(
    ("mode", "listed", "rule"),
    [
        ("listed", 2, "only the 2 files you listed"),
        (
            "chained",
            3,
            "only the 3 files you listed and outputs of runs under this permission, "
            "which UMRS picks",
        ),
        ("chained", 0, "only outputs of runs under this permission, which UMRS picks"),
        ("any", 0, hg.D10_DATA),
    ],
)
def test_every_summary_says_what_umrs_still_decides(mode, listed, rule):
    """K16: the first sentence, both residuals and both lists in every mode;
    the "chooses the data" sentence verbatim only for ``any``."""
    text = _summary(mode, listed)
    needed = [
        rule,
        hg.D10_FIRST,
        hg.EXPIRY_TEXT,
        "UMRS also sees every output.",
        "no limit (0 means no core-hour cap)",
        *hg.RESIDUALS,
        *hg.UMRS_DECIDES,
        *hg.UMRS_CANNOT,
    ]
    assert [item for item in needed if item not in text] == []
    assert text.count(hg.D10_DATA) == (1 if mode == "any" else 0)


def test_the_budget_warning():
    assert hg.budget_warning({"budget": {"remaining_gpu_hours": 4.5}}, 5) == (
        "warning: the project has 4.5 GPU-hours left and this grant asks for 5; "
        "the server will refuse to store it."
    )
    assert hg.budget_warning({"budget": {"remaining_gpu_hours": 5}}, 5) is None
    assert hg.budget_warning({"budget": {"remaining_gpu_hours": None}}, 5) is None


@needs_keygen
def test_the_budget_warning_is_shown_before_the_summary(person):
    shown: list[str] = []
    site = GrantSite()
    site.budget = {"max_gpu_hours": 10, "remaining_gpu_hours": 1.0}
    _run(site, out=shown.append)
    assert shown[0].startswith("warning: the project has 1 GPU-hours left")


# ── legacy shared keys and grant-cancel ──────────────────────────────────────


@needs_keygen
def test_an_ambiguous_legacy_key_refuses_a_new_grant_but_not_its_cancel(person):
    hs.record_site_signer("other-site", "ed25519", person)
    site = GrantSite()
    with pytest.raises(hs.HpcSigningError, match="also recorded for other-site"):
        _run(site)
    assert not [call for call in site.calls if call[0] == "store"]
    result = _cancel(site, "a" * 32, SITE, clock=lambda: NOW)
    assert result.relayed and result.recorded and not result.direct


@needs_keygen
def test_grant_cancel_signs_a_control_with_the_grant_id_and_no_key_id(person, tmp_path):
    site = GrantSite()
    _run(site)
    grant_id = _grant(site)["grant_id"]
    result = _cancel(site, grant_id, None, clock=lambda: NOW)
    (gid, envelope) = [c[1:] for c in site.calls if c[0] == "cancel"][0]
    payload = base64.b64decode(envelope["payload"])
    body = hpc_gate.parse_strict(payload)
    assert gid == grant_id and result.relayed and result.fork_note is None
    assert body == {
        "v": 1,
        "type": "control",
        "op": "grant-cancel",
        "site": SITE,
        "principal": "alice",
        "grant_id": grant_id,
        "nonce": body["nonce"],
        "issued": NOW,
        "expires": NOW + 300,
    }
    hpc_gate.validate_control(body, "grant-cancel")
    control_ns = hpc_gate.namespace("control", DOMAIN)
    assert _verify(envelope["signature"], payload, control_ns, person, tmp_path) == 0
    grant_ns = hpc_gate.namespace("grant", DOMAIN)
    assert _verify(envelope["signature"], payload, grant_ns, person, tmp_path) != 0


@needs_keygen
def test_grant_cancel_still_signs_while_a_fork_is_recorded(person):
    """K20: a fork must not block the remedy; the fork is shown beside it."""
    site = GrantSite()
    hs.update_site_record(SITE, witness_fork={"seen_at": "t", "reason": "r"})
    result = _cancel(site, "a" * 32, SITE, clock=lambda: NOW)
    assert [c[0] for c in site.calls] == ["cancel"]
    assert "grant-cancel is allowed" in (result.fork_note or "")


def test_grant_cancel_needs_a_site_for_a_grant_r4_did_not_sign(person):
    with pytest.raises(hs.HpcSigningError, match="pass --site"):
        _cancel(GrantSite(), "b" * 32, None)


@pytest.mark.parametrize("bad", ["", "A" * 32, "a" * 31, "../" + "a" * 29])
def test_grant_cancel_refuses_a_malformed_id(person, bad):
    with pytest.raises(hs.HpcSigningError, match="grant id"):
        _cancel(GrantSite(), bad, SITE)


def test_grant_cancel_refuses_in_the_companion_shell(monkeypatch, recorded):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    site = MagicMock()
    with pytest.raises(hs.HpcSigningError, match="person's act"):
        _cancel(site, "a" * 32, SITE)
    assert site.mock_calls == []


def test_grant_cancel_requires_a_matching_gate_receipt(person):
    site = GrantSite()
    site.cancel_answer = {
        "relayed": True,
        "grant": {"status": "cancelled", "gate_cancel": "recorded"},
    }
    result = _cancel(site, "a" * 32, SITE, clock=lambda: NOW)
    assert not result.relayed and not result.recorded


def test_grant_cancel_uses_and_verifies_the_direct_route_when_relay_fails(person):
    grant_id = "a" * 32
    site = GrantSite()
    site.cancel_answer = {
        "relayed": False,
        "grant": {"status": "cancelled", "gate_cancel": "pending"},
    }
    envelopes = []

    def direct(envelope):
        envelopes.append(envelope)
        return _cancel_receipt(grant_id)

    result = _cancel(site, grant_id, SITE, clock=lambda: NOW, direct=direct)
    assert len(envelopes) == 1
    assert not result.relayed and result.recorded and result.direct
    assert not result.mirror_recorded and result.mirror_pending


def test_grant_cancel_rejects_a_bad_direct_receipt(person):
    grant_id = "a" * 32
    site = GrantSite()
    site.cancel_answer = {
        "relayed": True,
        "grant": {"status": "cancelled", "gate_cancel": "pending"},
    }
    result = _cancel(site, grant_id, SITE, clock=lambda: NOW, direct=lambda _answer: {"ok": True})
    assert not result.relayed and not result.recorded and not result.direct


def test_direct_cancel_also_covers_a_gateway_exception(person):
    grant_id = "a" * 32
    site = GrantSite()

    def unreachable(_grant_id, _envelope):
        raise hs.HpcSigningError("gateway unreachable")

    site.hpc_grant_cancel = unreachable
    result = _cancel(
        site, grant_id, SITE, clock=lambda: NOW, direct=lambda _env: _cancel_receipt(grant_id)
    )
    assert result.recorded and result.direct and not result.mirror_recorded
    assert not result.mirror_pending


@needs_keygen
def test_cancel_records_the_request_time_without_marking_an_unknown_grant_signed(person):
    site = GrantSite()
    grant_id = "a" * 32
    site.cancel_answer = {"relayed": False, "grant": {"gate_cancel": "pending"}}
    result = _cancel(
        site, grant_id, SITE, clock=lambda: NOW, direct=lambda _env: _cancel_receipt(grant_id)
    )
    assert result.recorded and result.direct
    record = hg.read_grant_record(grant_id)
    assert record["cancel_requested_at"] == NOW
    assert grant_id not in hg.signed_grant_ids()


# ── the CLI ─────────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    from r4._cli import hpc as hpc_cli
    from r4.cli import _build_parser

    return hpc_cli.HpcCommand().handle(_build_parser().parse_args(["hpc", *argv]))


GRANT_ARGV = [
    "grant",
    "--site",
    SITE,
    "--project",
    PROJECT,
    "--workflow",
    "wf@1.0",
    "--max-jobs",
    "10",
    "--max-gpu-hours",
    "5",
    "--days",
    "7",
    "--yes",
]


@needs_keygen
def test_cli_grant_signs_and_stores(person, monkeypatch, capsys):
    from r4._cli import hpc as hpc_cli

    site = GrantSite()
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    assert _cli(GRANT_ARGV) == 0
    out = capsys.readouterr().out
    grant_id = _grant(site)["grant_id"]
    assert hg.D10_FIRST in out and f"✓ grant {grant_id} stored at {SITE}" in out


@needs_keygen
def test_cli_grant_refusal_exits_1_with_the_diff(person, monkeypatch, capsys):
    from r4._cli import hpc as hpc_cli

    site = GrantSite()
    site.mutate = lambda answer: answer["workflows"][0].update(steps_sha256="0" * 64)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    assert _cli(GRANT_ARGV) == 1
    err = capsys.readouterr().err
    assert "nothing was signed" in err and "steps_sha256" in err


@needs_keygen
def test_cli_grant_cancel_takes_the_site_from_the_local_record(person, monkeypatch, capsys):
    from r4 import hpc_remote
    from r4._cli import hpc as hpc_cli

    site = GrantSite()
    _run(site)
    grant_id = _grant(site)["grant_id"]
    names: list[Any] = []
    monkeypatch.setattr(hpc_cli, "_site_client", lambda n: names.append(n) or site)
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda _host: site.gate_log)
    checked = []
    verify = hg._independent_cancel_receipt

    def checked_receipt(answer, data, grant):
        valid = verify(answer, data, grant)
        checked.append(valid)
        return valid

    monkeypatch.setattr(hg, "_independent_cancel_receipt", checked_receipt)
    code = _cli(["grant-cancel", grant_id])
    assert checked == [True]
    assert code == 0
    assert names == [SITE] and "your gate refuses it" in capsys.readouterr().out


@needs_keygen
def test_cli_grant_cancel_says_when_the_gate_has_not_recorded_it(person, monkeypatch, capsys):
    """K8: UMRS stopped using it; running the command again retries."""
    from r4._cli import hpc as hpc_cli

    site = GrantSite()
    site.cancel_answer = {"relayed": False, "grant": {"gate_cancel": "pending"}}
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    assert _cli(["grant-cancel", "c" * 32, "--site", SITE]) == 1
    err = capsys.readouterr().err
    assert "has no verified gate receipt" in err and f"r4 hpc grant-cancel {'c' * 32}" in err


@needs_keygen
def test_cli_direct_grant_cancel_reports_the_gate_and_pending_mirror(person, monkeypatch, capsys):
    from r4 import hpc_remote
    from r4._cli import hpc as hpc_cli

    grant_id = "c" * 32
    site = GrantSite()
    site.cancel_answer = {
        "relayed": False,
        "grant": {"status": "cancelled", "gate_cancel": "pending"},
    }
    seen = []

    def direct(host, venue, envelope):
        seen.append((host, venue, envelope))
        return _cancel_receipt(grant_id)

    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    monkeypatch.setattr(hpc_cli, "_host", lambda _args: "login.example")
    monkeypatch.setattr(hpc_remote, "direct_grant_cancel", direct)
    assert _cli(["grant-cancel", grant_id, "--site", SITE, "--direct", "--ssh", "gate"]) == 0
    assert seen[0][:2] == ("login.example", SITE)
    captured = capsys.readouterr()
    assert "cancelled at your gate" in captured.out
    assert "UMRS mirror is pending" in captured.err


def _forge_relay_receipt(site: GrantSite, grant_id: str, tmp_path: Path) -> None:
    site.cancel_answer = {
        "relayed": True,
        "result": {
            "ok": True,
            "op": "grant-cancel",
            "grant_id": grant_id,
            "log_head": {"n": 1, "h": "0" * 64},
        },
        "grant": {"status": "cancelled", "gate_cancel": "recorded"},
    }
    path = tmp_path / "gate.jsonl"
    log = hpc_gate.GateLog(str(path))
    log.open()
    log.append("hello", "ok", "not a cancel", NOW)
    log.close()
    site.gate_log = path.read_bytes()


@needs_keygen
def test_cli_forged_relay_cancel_receipt_is_not_accepted(person, monkeypatch, tmp_path, capsys):
    from r4 import hpc_remote
    from r4._cli import hpc as hpc_cli

    grant_id = "a" * 32
    site = GrantSite()
    _forge_relay_receipt(site, grant_id, tmp_path)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda _host: site.gate_log)
    assert _cli(["grant-cancel", grant_id, "--site", SITE]) == 1
    assert "no verified gate receipt" in capsys.readouterr().err


@needs_keygen
def test_cli_forged_relay_receipt_still_falls_back_to_direct(person, monkeypatch, tmp_path, capsys):
    from r4 import hpc_remote
    from r4._cli import hpc as hpc_cli

    grant_id = "a" * 32
    site = GrantSite()
    _forge_relay_receipt(site, grant_id, tmp_path)
    seen = []
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: site)
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda _host: site.gate_log)
    monkeypatch.setattr(
        hpc_remote,
        "direct_grant_cancel",
        lambda host, venue, envelope: seen.append((host, venue)) or _cancel_receipt(grant_id),
    )
    assert _cli(["grant-cancel", grant_id, "--site", SITE, "--direct"]) == 0
    assert len(seen) == 1
    assert "direct receipt verified" in capsys.readouterr().out


def test_the_direct_gate_cancel_is_bounded_and_parses_one_answer(monkeypatch):
    from r4 import hpc_remote

    monkeypatch.setattr(
        hpc_remote, "probe", lambda _host: hpc_remote.RemoteHome("alice", "/home/alice")
    )
    grant_id = "d" * 32
    answer = json.dumps(_cancel_receipt(grant_id)).encode()
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=answer)

    monkeypatch.setattr(hpc_remote, "_run", run)
    result = hpc_remote.direct_grant_cancel("gate", SITE, {"payload": "p", "signature": "s"})
    assert result["grant_id"] == grant_id
    assert seen["kwargs"]["timeout"] == 90
    assert seen["kwargs"]["input"] == b'{"payload":"p","signature":"s"}'
    command = seen["argv"][-1]
    assert "SSH_ORIGINAL_COMMAND=grant-cancel" in command


@pytest.mark.parametrize(("direct", "prefix"), [(False, "/gw"), (True, "/umrs")])
def test_the_grant_routes_match_the_contract(direct, prefix, monkeypatch):
    from r4.client import Client
    from r4.config import Config

    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client._is_umrs_direct = direct
    client.http.post = MagicMock(return_value={})
    client.hpc_grant_proposal({"project_id": "p", "workflows": []})
    client.hpc_grant_store({"payload": "p", "signature": "s"})
    client.hpc_grant_cancel("a" * 32, {"payload": "p", "signature": "s"})
    base = f"http://gw:8080{prefix}"
    assert [c.args[0] for c in client.http.post.call_args_list] == [
        f"{base}/sites/me/hpc/grant-proposal",
        f"{base}/sites/me/hpc/grants",
        f"{base}/sites/me/hpc/grants/{'a' * 32}/cancel",
    ]
