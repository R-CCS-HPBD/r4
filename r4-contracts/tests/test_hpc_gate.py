"""ADR-112 wave B1: the UMRS gate (r4_contracts.hpc_gate).

Contract: gated-delegation plan, "B1.5a Wire contract". The subprocess tests
run the gate file exactly as sshd would (``python -I <gate> --site s1`` with
``SSH_ORIGINAL_COMMAND``), with a scratch HOME, and with real ``ssh-keygen``
signatures from ed25519 keys generated into tmp at test time. They also run
under python3.9 when one is installed (the gate's interpreter floor).
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import pwd
import secrets
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from r4_contracts import hpc_gate as g

KEYGEN = "/usr/bin/ssh-keygen"
GATE = Path(g.__file__).resolve()
SITE = "s1"
DOMAIN = "umrs.example.org"
PRINCIPAL = "alice"
KEY_A = "tk-aaaaaaaaaaaa"
KEY_B = "tk-bbbbbbbbbbbb"
KEY_N = "tk-cccccccccccc"
CONTROL_NS = f"umrs-control-v1@{DOMAIN}"

pytestmark = pytest.mark.skipif(not os.path.exists(KEYGEN), reason="needs /usr/bin/ssh-keygen")

_PY39 = shutil.which("python3.9")
INTERPRETERS = [sys.executable] + ([_PY39] if _PY39 else [])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tool_env(home: Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C"}


def _keygen(path: Path, comment: str) -> str:
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
        check=True,
        capture_output=True,
        env=_tool_env(path.parent),
    )
    return (path.parent / (path.name + ".pub")).read_text().split()[1]


def _sign(key: Path, ns: str, payload: bytes) -> str:
    proc = subprocess.run(
        [KEYGEN, "-Y", "sign", "-f", str(key), "-n", ns],
        input=payload,
        check=True,
        capture_output=True,
        env=_tool_env(key.parent),
    )
    return proc.stdout.decode("ascii")


@dataclass(frozen=True)
class Keys:
    signer: Path
    signer_b64: str
    other: Path
    transport_a: str
    transport_b: str
    transport_new: str


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    base = tmp_path_factory.mktemp("keys")
    _keygen(base / "other", "other")
    return Keys(
        signer=base / "signer",
        signer_b64=_keygen(base / "signer", "signer"),
        other=base / "other",
        transport_a=_keygen(base / "ta", "ta"),
        transport_b=_keygen(base / "tb", "tb"),
        transport_new=_keygen(base / "tn", "tn"),
    )


@dataclass
class Env:
    home: Path
    bin: Path
    keys: Keys
    interp: str

    @property
    def root(self) -> Path:
        return self.home / ".umrs" / "gate"

    @property
    def ak(self) -> Path:
        return self.home / ".ssh" / "authorized_keys"

    @property
    def log(self) -> Path:
        return self.root / "log" / "gate.jsonl"

    def config(self, **over: Any) -> None:
        cfg = {
            "site": SITE,
            "domain": DOMAIN,
            "principal": PRINCIPAL,
            "uv_required": False,
            "allow_non_sk": True,
            "gate_version": "1",
        }
        cfg.update(over)
        (self.root / "gate.json").write_text(json.dumps(cfg))

    def run(
        self, command: str | None, stdin: bytes = b"", extra: dict[str, str] | None = None
    ) -> tuple[int, Any, Any]:
        env = {"HOME": str(self.home), "PATH": str(self.bin), **(extra or {})}
        if command is not None:
            env["SSH_ORIGINAL_COMMAND"] = command
        proc = subprocess.run(
            [self.interp, "-I", str(GATE), "--site", SITE],
            input=stdin,
            capture_output=True,
            env=env,
            timeout=60,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else None
        err = json.loads(proc.stderr) if proc.stderr.strip() else None
        return proc.returncode, out, err

    def control(self, op: str, /, **over: Any) -> dict[str, Any]:
        now = int(time.time())
        body: dict[str, Any] = {
            "v": 1,
            "type": "control",
            "op": op,
            "site": SITE,
            "principal": PRINCIPAL,
            "key_id": KEY_A,
            "nonce": secrets.token_hex(16),
            "issued": now,
            "expires": now + 300,
        }
        if op == "rotate":
            body["new_public_key"] = f"ssh-ed25519 {self.keys.transport_new}"
        body.update(over)
        return {k: v for k, v in body.items() if v is not None}

    def envelope(self, payload: bytes, key: Path | None = None, ns: str = CONTROL_NS) -> bytes:
        sig = _sign(key or self.keys.signer, ns, payload)
        return json.dumps(
            {"payload": base64.b64encode(payload).decode(), "signature": sig}
        ).encode()

    def signed(self, body: dict[str, Any], **kw: Any) -> bytes:
        return self.envelope(g.canonical_bytes(body), **kw)


def _ak_lines(env: Env) -> list[str]:
    home = str(env.home)
    return [
        "# my own keys",
        "ssh-ed25519 " + env.keys.transport_b + " laptop",
        g.authorized_keys_line(home, SITE, KEY_A, env.keys.transport_a, "192.0.2.0/24"),
        g.authorized_keys_line(home, SITE, KEY_B, env.keys.transport_b, ""),
        "",
    ]


def _make_env(tmp_path: Path, keys: Keys, interp: str) -> Env:
    home = tmp_path / "home"
    (home / ".umrs" / "gate").mkdir(parents=True, mode=0o700)
    (home / ".ssh").mkdir(mode=0o700)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    e = Env(home=home, bin=bindir, keys=keys, interp=interp)
    e.config()
    line = g.allowed_signers_line(PRINCIPAL, DOMAIN, "ssh-ed25519", keys.signer_b64)
    (e.root / "allowed_signers").write_text(line + "\n")
    e.ak.write_text("\n".join(_ak_lines(e)))
    e.ak.chmod(0o600)
    return e


@pytest.fixture(params=INTERPRETERS, ids=lambda p: Path(p).name)
def env(request: pytest.FixtureRequest, tmp_path: Path, keys: Keys) -> Env:
    return _make_env(tmp_path, keys, request.param)


@pytest.fixture
def local(tmp_path: Path, keys: Keys) -> Env:
    """The same layout for in-process calls (this interpreter only)."""
    return _make_env(tmp_path, keys, sys.executable)


def _refused(result: tuple[int, Any, Any], name: str) -> None:
    code, out, err = result
    assert (code, out) == (4, None), result
    assert err["error"] == name, result
    assert set(err) == {"error", "detail"}


# ---------------------------------------------------------------------------
# module shape
# ---------------------------------------------------------------------------


def test_gate_imports_only_the_stdlib() -> None:
    tree = ast.parse(GATE.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names - {"__future__"} <= set(sys.stdlib_module_names), names


def test_constants_and_namespaces() -> None:
    # waves C and D (2026-11-09): gate.json "1" (B1) and "2" (B2) still load
    assert g.GATE_VERSION == "3"
    assert g.GATE_CONFIG_VERSIONS == ("1", "2", "3")
    assert g.namespace("intent", DOMAIN) == f"umrs-intent-v1@{DOMAIN}"
    assert g.namespace("grant", DOMAIN) == f"umrs-grant-v1@{DOMAIN}"
    assert g.namespace("control", DOMAIN) == CONTROL_NS
    for kind, domain in (("other", DOMAIN), ("intent", "a b"), ("intent", ""), ("intent", "x,y")):
        with pytest.raises(ValueError):
            g.namespace(kind, domain)


def test_gate_sha256_and_intent_id() -> None:
    assert g.gate_sha256() == hashlib.sha256(GATE.read_bytes()).hexdigest()
    assert g.intent_id(b'{"a":1}') == hashlib.sha256(b'{"a":1}').hexdigest()


# ---------------------------------------------------------------------------
# canonical JSON vectors
# ---------------------------------------------------------------------------

CANONICAL_VECTORS = [
    ({"b": 1, "a": [True, False, "x"]}, b'{"a":[true,false,"x"],"b":1}'),
    ({"k": "é\U0001f600"}, '{"k":"é\U0001f600"}'.encode()),
    ({"k": 'a\nb\u001f"\\'}, b'{"k":"a\\nb\\u001f\\"\\\\"}'),
    ({"n": 2**53 - 1, "m": -(2**53 - 1)}, b'{"m":-9007199254740991,"n":9007199254740991}'),
    ({"z": {}, "y": []}, b'{"y":[],"z":{}}'),
]


@pytest.mark.parametrize(("obj", "expected"), CANONICAL_VECTORS)
def test_canonical_bytes_vectors(obj: Any, expected: bytes) -> None:
    assert g.canonical_bytes(obj) == expected
    assert g.parse_strict(expected) == obj


@pytest.mark.parametrize(
    "bad",
    [1.5, None, {"a": None}, {1: "a"}, 2**53, -(2**53), (1, 2), float("nan"), "\ud800"],
)
def test_canonical_bytes_refuses(bad: Any) -> None:
    with pytest.raises(ValueError):
        g.canonical_bytes(bad)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"a":1.0}',
        b'{"a":1e2}',
        b'{"a": 1}',
        b'{"b":1,"a":2}',
        b"[1]",
        b'"x"',
        b'{"a":9007199254740993}',
        b'{"a":"\\u00e9"}',
        b'{"a":-0}',
        b'{"a":1}\n',
        b"\xef\xbb\xbf{}",
        b'{"a":"\xff"}',
        b"[" * 100000 + b"]" * 100000,
        b"",
    ],
)
def test_parse_strict_refuses(raw: bytes) -> None:
    with pytest.raises(ValueError):
        g.parse_strict(raw)


JCS_EXTRA = [
    {"z": {"b": [1, {"y": "\u00fc", "x": -5}], "a": "\u2028"}, "A": False},
    {"k": "\t\b\f\r\u007f\u0000", "e": "", "n": 2**53 - 1},
    {"emoji": "\U0001f600", "list": [[], {}, [True]]},
]


def test_canonical_matches_rfc8785() -> None:
    """Cross-check with the JCS reference (dev only; skipped when not installed).

    Both stop at |v| <= 2**53 - 1 (the contract was tightened to I-JSON on
    2026-11-08), so every vector is compared.
    """
    rfc8785 = pytest.importorskip("rfc8785")
    objs = [obj for obj, _ in CANONICAL_VECTORS] + JCS_EXTRA
    for obj in objs:
        assert rfc8785.dumps(obj) == g.canonical_bytes(obj)


# ---------------------------------------------------------------------------
# line builders
# ---------------------------------------------------------------------------


def test_authorized_keys_line_exact(keys: Keys) -> None:
    b64 = keys.transport_a
    line = g.authorized_keys_line("/home/u", SITE, KEY_A, b64, "192.0.2.1,2001:db8::/32")
    assert line == (
        'restrict,from="192.0.2.1,2001:db8::/32",'
        'command="/usr/bin/python3 -I /home/u/.umrs/gate/umrs_gate.py --site s1" '
        f"ssh-ed25519 {b64} umrs-transport-s1-{KEY_A}"
    )
    bare = g.authorized_keys_line("/home/u/", SITE, KEY_A, b64)
    assert bare.startswith('restrict,command="/usr/bin/python3 -I /home/u/.umrs/gate/')


@pytest.mark.parametrize(
    ("home", "site", "key_id", "from_address"),
    [
        ("/home/u", "S1", KEY_A, ""),
        ("/home/u", "s" * 41, KEY_A, ""),
        ("/home/u", SITE, "tk-123", ""),
        ("/home/u", SITE, "TK-aaaaaaaaaaaa", ""),
        ("home/u", SITE, KEY_A, ""),
        ('/home/u" x', SITE, KEY_A, ""),
        ("/home/my u", SITE, KEY_A, ""),
        ("/home/../root", SITE, KEY_A, ""),
        ("/home/u", SITE, KEY_A, '1.2.3.4",command="sh'),
        ("/home/u", SITE, KEY_A, "1.2.3.4\nssh-ed25519"),
        ("/home/u", SITE, KEY_A, "1.2.3.4,,5.6.7.8"),
        ("/home/u", SITE, KEY_A, "1.2.3.4 5.6.7.8"),
    ],
)
def test_authorized_keys_line_refuses(
    keys: Keys, home: str, site: str, key_id: str, from_address: str
) -> None:
    with pytest.raises(ValueError):
        g.authorized_keys_line(home, site, key_id, keys.transport_a, from_address)


def test_public_site_and_from_checks_are_the_line_builders_rules() -> None:
    """UMRS (site config, registration) and r4 call these instead of copying
    the patterns (2026-11-08 integration)."""
    assert g.is_site_name("bdrt-front") and g.is_site_name("s" * 40)
    for bad in ("Bdrt", "bdrt_front", "s" * 41, "", None, 3):
        assert not g.is_site_name(bad)
    assert g.check_from_address("192.0.2.1,!192.0.2.9,*.example.org") == (
        "192.0.2.1,!192.0.2.9,*.example.org"
    )
    for bad_from in ("", "1.2.3.4,", "1.2.3.4,,5.6.7.8", "[::1]", '1.2.3.4"', None):
        with pytest.raises(ValueError):
            g.check_from_address(bad_from)  # type: ignore[arg-type]


def test_authorized_keys_line_refuses_bad_keys(keys: Keys) -> None:
    blob = base64.b64decode(keys.transport_a)
    rsa_like = base64.b64encode(b"\x00\x00\x00\x0bssh-rsa\x00\x00" + blob[15:]).decode()
    for bad in (keys.transport_a[:-4], keys.transport_a + "AAAA", rsa_like, "A" * 68, ""):
        with pytest.raises(ValueError):
            g.authorized_keys_line("/home/u", SITE, KEY_A, bad)


def test_allowed_signers_line(keys: Keys) -> None:
    line = g.allowed_signers_line(PRINCIPAL, DOMAIN, "ssh-ed25519", keys.signer_b64)
    names = f"umrs-intent-v1@{DOMAIN},umrs-grant-v1@{DOMAIN},umrs-control-v1@{DOMAIN}"
    assert line == f'alice namespaces="{names}" ssh-ed25519 {keys.signer_b64}'
    dated = g.allowed_signers_line(PRINCIPAL, DOMAIN, "ssh-ed25519", keys.signer_b64, "20270101")
    assert ',valid-before="20270101" ssh-ed25519' in dated
    for args in (
        ("a b", DOMAIN, "ssh-ed25519", keys.signer_b64),
        ("-x", DOMAIN, "ssh-ed25519", keys.signer_b64),
        (PRINCIPAL, DOMAIN, "ssh-rsa", keys.signer_b64),
        (PRINCIPAL, DOMAIN, "sk-ssh-ed25519@openssh.com", keys.signer_b64),
        (PRINCIPAL, "bad domain", "ssh-ed25519", keys.signer_b64),
    ):
        with pytest.raises(ValueError):
            g.allowed_signers_line(*args)


# ---------------------------------------------------------------------------
# SSHSIG parsing and the sk- flag policy
# ---------------------------------------------------------------------------


def _s(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _armor(blob: bytes) -> str:
    text = base64.b64encode(blob).decode()
    body = "\n".join(text[i : i + 70] for i in range(0, len(text), 70))
    return f"-----BEGIN SSH SIGNATURE-----\n{body}\n-----END SSH SIGNATURE-----\n"


def _sk_sig(flags: int, counter: int = 7, ns: str = CONTROL_NS) -> str:
    kt = b"sk-ssh-ed25519@openssh.com"
    pub = _s(kt) + _s(b"\x11" * 32) + _s(b"ssh:")
    sig = _s(kt) + _s(b"\x22" * 64) + bytes([flags]) + struct.pack(">I", counter)
    blob = b"SSHSIG" + struct.pack(">I", 1) + _s(pub) + _s(ns.encode()) + _s(b"")
    return _armor(blob + _s(b"sha512") + _s(sig))


@pytest.mark.parametrize("flags", [0x00, 0x01, 0x05])
def test_parse_sshsig_sk_flags(flags: int) -> None:
    sig = g.parse_sshsig(_sk_sig(flags, counter=42))
    assert (sig.pubkey_type, sig.sig_type) == ("sk-ssh-ed25519@openssh.com",) * 2
    assert (sig.namespace, sig.hash_alg, sig.flags, sig.counter) == (
        CONTROL_NS,
        "sha512",
        flags,
        42,
    )


@pytest.mark.parametrize(
    ("flags", "uv_required", "refusal"),
    [
        (0x00, False, "presence_required"),
        (0x04, False, "presence_required"),
        (0x01, False, None),
        (0x01, True, "verification_required"),
        (0x05, True, None),
        (0x05, False, None),
    ],
)
def test_sk_flag_policy(flags: int, uv_required: bool, refusal: str | None) -> None:
    sig = g.parse_sshsig(_sk_sig(flags))
    if refusal is None:
        g.check_signature_policy(sig, uv_required=uv_required, allow_non_sk=False)
        return
    with pytest.raises(g.GateError) as info:
        g.check_signature_policy(sig, uv_required=uv_required, allow_non_sk=False)
    assert (info.value.error, info.value.code) == (refusal, 4)


def test_non_sk_policy_and_real_signature(keys: Keys) -> None:
    sig = g.parse_sshsig(_sign(keys.signer, CONTROL_NS, b"x"))
    assert (sig.pubkey_type, sig.sig_type, sig.flags, sig.counter) == (
        "ssh-ed25519",
        "ssh-ed25519",
        None,
        None,
    )
    assert sig.namespace == CONTROL_NS
    assert sig.signer_fingerprint == g.fingerprint(base64.b64decode(keys.signer_b64))
    g.check_signature_policy(sig, uv_required=True, allow_non_sk=True)
    with pytest.raises(g.GateError, match="key_type_not_allowed"):
        g.check_signature_policy(sig, uv_required=False, allow_non_sk=False)
    rsa = g.SshSig("ssh-rsa", CONTROL_NS, "sha512", "rsa-sha2-512", None, None)
    with pytest.raises(g.GateError, match="key_type_not_allowed"):
        g.check_signature_policy(rsa, uv_required=False, allow_non_sk=True)


def test_parse_sshsig_refuses_malformed() -> None:
    good = base64.b64decode("".join(_sk_sig(1).splitlines()[1:-1]))
    for blob in (
        good[:-1],
        good + b"\x00",
        b"SSHSIH" + good[6:],
        good[:6] + b"\x00\x00\x00\x02" + good[10:],
    ):
        with pytest.raises(ValueError):
            g.parse_sshsig(_armor(blob))
    for text in ("", "garbage", _sk_sig(1).replace("BEGIN", "START")):
        with pytest.raises(ValueError):
            g.parse_sshsig(text)


def test_fingerprint_matches_ssh_keygen(keys: Keys, tmp_path: Path) -> None:
    proc = subprocess.run(
        [KEYGEN, "-lf", str(keys.signer) + ".pub"],
        capture_output=True,
        check=True,
        env=_tool_env(tmp_path),
    )
    assert proc.stdout.split()[1].decode() == g.fingerprint(base64.b64decode(keys.signer_b64))


# ---------------------------------------------------------------------------
# the gate as sshd runs it: unsigned verbs
# ---------------------------------------------------------------------------


def test_no_command(env: Env) -> None:
    _refused(env.run(None), "no_command")
    _refused(env.run("   "), "no_command")


@pytest.mark.parametrize("command", ["bash", "rm -rf ~", "sh -c id", "'unbalanced", "scp -t ."])
def test_unknown_verb(env: Env, command: str) -> None:
    _refused(env.run(command), "unknown_verb")


def test_probe_refuses_even_without_config(env: Env) -> None:
    _refused(env.run("probe"), "gate_probe_refusal")
    (env.root / "gate.json").unlink()
    _refused(env.run("probe"), "gate_probe_refusal")


def test_usage_and_absent(env: Env) -> None:
    proc = subprocess.run(
        [env.interp, "-I", str(GATE)],
        capture_output=True,
        env={"HOME": str(env.home), "PATH": str(env.bin), "SSH_ORIGINAL_COMMAND": "hello"},
    )
    assert proc.returncode == 2 and json.loads(proc.stderr)["error"] == "usage"
    assert env.run("hello extra")[0] == 2
    (env.root / "gate.json").unlink()
    code, _, err = env.run("hello")
    assert (code, err["error"]) == (3, "absent")


def test_config_for_another_site_or_invalid(env: Env) -> None:
    env.config(site="s2")
    _refused(env.run("hello"), "wrong_site")
    env.config(extra=True)
    code, _, err = env.run("hello")
    assert (code, err["error"]) == (1, "failed")


def test_hello_shape(env: Env) -> None:
    code, out, err = env.run("hello")
    assert (code, err) == (0, None)
    assert set(out) == {
        "gate_version", "gate_sha256", "site", "login", "uid", "python",
        "openssh", "signers", "uv_required", "allow_non_sk", "assocs", "log_head",
        "site_profile_sha256", "verbs", "checkpoint",
    }  # fmt: skip
    assert (out["uv_required"], out["allow_non_sk"]) == (False, True)
    sha = hashlib.sha256(GATE.read_bytes()).hexdigest()
    assert (out["gate_version"], out["gate_sha256"]) == ("3", sha)
    assert (out["site"], out["uid"], out["site_profile_sha256"], out["verbs"]) == (
        SITE,
        os.geteuid(),
        "",
        list(g.VERBS),
    )
    assert out["login"] == pwd.getpwuid(os.geteuid()).pw_name
    assert out["python"].count(".") == 2


def test_hello_reports_signers_assocs_and_log_head(env: Env) -> None:
    out = env.run("hello")[1]
    assert out["openssh"].startswith("OpenSSH") or not os.path.exists("/usr/bin/ssh")
    fp = g.fingerprint(base64.b64decode(env.keys.signer_b64))
    assert out["signers"] == [{"fingerprint": fp, "type": "ssh-ed25519"}]
    assert out["assocs"] == []
    assert out["log_head"] == {"n": 0, "h": "0" * 64}


def _stub_sacctmgr(env: Env, script: str) -> None:
    stub = env.bin / "sacctmgr"
    stub.write_text("#!/bin/sh\n" + script)
    stub.chmod(0o755)


def test_hello_reads_slurm_associations(env: Env) -> None:
    _stub_sacctmgr(
        env, 'echo "$@" > "$HOME/args"\nprintf "c1|acct1|gpu|normal\\nbad line\\nc1|acct2||\\n"\n'
    )
    code, out, _ = env.run("hello")
    assert code == 0
    assert out["assocs"] == [
        {"cluster": "c1", "account": "acct1", "partition": "gpu", "qos": "normal"},
        {"cluster": "c1", "account": "acct2", "partition": "", "qos": ""},
    ]
    args = (env.home / "args").read_text().split()
    assert (
        args[:3] == ["-nP", "show", "assoc"] and args[-1] == "format=cluster,account,partition,qos"
    )


def test_hello_caps_associations_and_reports_scheduler_failure(env: Env) -> None:
    _stub_sacctmgr(env, 'i=0; while [ $i -lt 300 ]; do echo "c|a$i|p|q"; i=$((i+1)); done\n')
    assert len(env.run("hello")[1]["assocs"]) == g.ASSOC_CAP
    _stub_sacctmgr(env, "echo boom >&2; exit 1\n")
    code, _, err = env.run("hello")
    assert (code, err["error"]) == (5, "scheduler")


# ---------------------------------------------------------------------------
# signed controls with real ssh-keygen signatures
# ---------------------------------------------------------------------------


def _ok(result: tuple[int, Any, Any]) -> dict[str, Any]:
    code, out, err = result
    assert (code, err) == (0, None), result
    return out


def test_revoke_removes_only_the_matching_line(env: Env) -> None:
    before = env.ak.read_text().splitlines()
    out = _ok(env.run("revoke", env.signed(env.control("revoke"))))
    assert (out["ok"], out["op"], out["key_id"], out["lines"]) == (True, "revoke", KEY_A, 1)
    after = env.ak.read_text().splitlines()
    assert after == [line for line in before if not line.endswith(f"umrs-transport-{SITE}-{KEY_A}")]
    assert len(after) == len(before) - 1
    assert oct(env.ak.stat().st_mode & 0o777) == "0o600"
    assert out["log_head"]["n"] == 1 and g.verify_log(str(env.log))


def test_rotate_replaces_only_the_key_blob(env: Env) -> None:
    before = env.ak.read_text().splitlines()
    _ok(env.run("rotate", env.signed(env.control("rotate"))))
    after = env.ak.read_text().splitlines()
    old = next(line for line in before if line.endswith(KEY_A))
    assert after[before.index(old)] == old.replace(env.keys.transport_a, env.keys.transport_new)
    assert [x for x in after if x != after[before.index(old)]] == [x for x in before if x != old]


def test_rotate_takes_the_new_comment(env: Env) -> None:
    new = f"ssh-ed25519 {env.keys.transport_new} umrs-transport-{SITE}-{KEY_N}"
    _ok(env.run("rotate", env.signed(env.control("rotate", new_public_key=new))))
    rotated = [line for line in env.ak.read_text().splitlines() if KEY_N in line]
    assert len(rotated) == 1 and rotated[0].startswith('restrict,from="192.0.2.0/24",command=')
    assert rotated[0].endswith(
        f"ssh-ed25519 {env.keys.transport_new} umrs-transport-{SITE}-{KEY_N}"
    )
    _ok(env.run("revoke", env.signed(env.control("revoke", key_id=KEY_N))))
    assert KEY_N not in env.ak.read_text()


def test_rotate_comment_for_another_site_is_refused(env: Env) -> None:
    new = f"ssh-ed25519 {env.keys.transport_new} umrs-transport-s2-{KEY_N}"
    _refused(env.run("rotate", env.signed(env.control("rotate", new_public_key=new))), "wrong_site")


def _unchanged(env: Env, before: str) -> None:
    assert env.ak.read_text() == before


@pytest.mark.parametrize(
    ("over", "refusal"),
    [
        ({"site": "s2"}, "wrong_site"),
        ({"principal": "bob"}, "wrong_principal"),
        ({"issued": 1000, "expires": 1300}, "expired"),
        ({"issued": int(time.time()) + 3600, "expires": int(time.time()) + 3700}, "expired"),
        ({"issued": int(time.time()), "expires": int(time.time()) + 601}, "expired"),
        ({"issued": int(time.time()), "expires": int(time.time()) - 1}, "expired"),
        ({"key_id": "tk-dddddddddddd"}, "no_matching_key"),
        ({"extra": "x"}, "bad_schema"),
        ({"op": "rotate"}, "bad_schema"),
        ({"v": 2}, "bad_schema"),
        ({"v": True}, "bad_schema"),
        ({"nonce": "short"}, "bad_schema"),
        ({"new_public_key": "ssh-ed25519 AAAA"}, "bad_schema"),
        ({"issued": None}, "bad_schema"),
    ],
)
def test_control_refusals(env: Env, over: dict[str, Any], refusal: str) -> None:
    before = env.ak.read_text()
    _refused(env.run("revoke", env.signed(env.control("revoke", **over))), refusal)
    _unchanged(env, before)


def test_rotate_requires_new_public_key(env: Env) -> None:
    _refused(
        env.run("rotate", env.signed(env.control("rotate", new_public_key=None))), "bad_schema"
    )


def test_wrong_namespace_tamper_and_foreign_key(env: Env) -> None:
    before = env.ak.read_text()
    body = env.control("revoke")
    intent_ns = f"umrs-intent-v1@{DOMAIN}"
    _refused(env.run("revoke", env.signed(body, ns=intent_ns)), "bad_signature")
    _refused(env.run("revoke", env.signed(body, key=env.keys.other)), "bad_signature")
    good = json.loads(env.signed(body))
    tampered = dict(body, key_id=KEY_B)
    good["payload"] = base64.b64encode(g.canonical_bytes(tampered)).decode()
    _refused(env.run("revoke", json.dumps(good).encode()), "bad_signature")
    _unchanged(env, before)


def test_replay_is_refused(env: Env) -> None:
    request = env.signed(env.control("revoke", key_id=KEY_B))
    _ok(env.run("revoke", request))
    _refused(env.run("revoke", request), "replayed")


def test_non_canonical_body_is_refused(env: Env) -> None:
    body = env.control("revoke")
    spaced = json.dumps(body, sort_keys=True).encode()
    _refused(env.run("revoke", env.envelope(spaced)), "not_canonical")
    unsorted = json.dumps(dict(reversed(list(body.items()))), separators=(",", ":")).encode()
    _refused(env.run("revoke", env.envelope(unsorted)), "not_canonical")
    dup = g.canonical_bytes(body)[:-1] + b',"v":1}'
    _refused(env.run("revoke", env.envelope(dup)), "not_canonical")


def test_envelope_refusals(env: Env) -> None:
    _refused(env.run("revoke", b"x" * (g.MAX_STDIN + 1)), "too_large")
    _refused(env.run("revoke", b"not json"), "bad_envelope")
    _refused(env.run("revoke", b'{"payload":"%%%","signature":"s"}'), "bad_envelope")
    _refused(env.run("revoke", b'{"payload":"e30=","signature":"s","x":1}'), "bad_envelope")
    _refused(env.run("revoke", b'{"payload":"e30="}'), "not_signed")
    _refused(env.run("revoke", b'{"payload":"e30=","signature":""}'), "not_signed")
    _refused(env.run("revoke", b'{"payload":"e30=","signature":"junk"}'), "bad_signature")


def test_non_sk_key_needs_allow_non_sk(env: Env) -> None:
    env.config(allow_non_sk=False)
    before = env.ak.read_text()
    _refused(env.run("revoke", env.signed(env.control("revoke"))), "key_type_not_allowed")
    _unchanged(env, before)


def test_authorized_keys_symlink_is_refused(env: Env, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text(env.ak.read_text())
    env.ak.unlink()
    env.ak.symlink_to(target)
    code, _, err = env.run("revoke", env.signed(env.control("revoke")))
    assert (code, err["error"]) == (1, "failed") and "symlink" in err["detail"]
    assert env.ak.is_symlink() and KEY_A in target.read_text()


def test_log_unwritable_fails_closed(env: Env, tmp_path: Path) -> None:
    (env.root / "log").mkdir(mode=0o700)
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_text("")
    env.log.symlink_to(decoy)
    before = env.ak.read_text()
    _refused(env.run("revoke", env.signed(env.control("revoke"))), "log_unwritable")
    _unchanged(env, before)
    assert decoy.read_text() == ""
    assert list((env.root / "state" / "nonces").iterdir()) == []


def test_corrupt_log_tail_fails_closed(env: Env) -> None:
    (env.root / "log").mkdir(mode=0o700)
    env.log.write_text('{"n":1')
    before = env.ak.read_text()
    _refused(env.run("revoke", env.signed(env.control("revoke"))), "log_unwritable")
    _unchanged(env, before)


def test_log_chain_records_refusals_and_verifies(env: Env) -> None:
    _refused(env.run("revoke", env.signed(env.control("revoke", site="s2"))), "wrong_site")
    _ok(env.run("revoke", env.signed(env.control("revoke"))))
    entries = [json.loads(line) for line in env.log.read_text().splitlines()]
    assert [(e["n"], e["verb"], e["result"]) for e in entries] == [
        (1, "revoke", "refused"),
        (2, "revoke", "ok"),
    ]
    assert entries[0]["prev"] == "0" * 64 and entries[1]["prev"] == entries[0]["h"]
    assert entries[0]["detail"].startswith("wrong_site")
    assert g.verify_log(str(env.log))
    assert env.run("hello")[1]["log_head"] == {"n": 2, "h": entries[1]["h"]}


# ---------------------------------------------------------------------------
# log, nonce and authorized_keys helpers in-process
# ---------------------------------------------------------------------------


def _write_log(path: Path, count: int) -> list[bytes]:
    log = g.GateLog(str(path))
    log.open()
    for i in range(count):
        log.append("revoke", "ok", f"entry {i}", 1000 + i)
    log.close()
    return path.read_bytes().splitlines(keepends=True)


def test_verify_log_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "gate.jsonl"
    assert g.verify_log(str(path))
    lines = _write_log(path, 3)
    assert g.verify_log(str(path))
    edited = lines[1].replace(b"entry 1", b"entry X")
    for variant in (
        [lines[0], edited, lines[2]],
        [lines[0], lines[2]],
        [lines[1], lines[2]],
        lines + [lines[2][:-1]],
        [lines[0], lines[1], lines[2].replace(b'"n":3', b'"n":true')],
    ):
        path.write_bytes(b"".join(variant))
        assert not g.verify_log(str(path))


def test_log_hash_definition(tmp_path: Path) -> None:
    path = tmp_path / "gate.jsonl"
    first = json.loads(_write_log(path, 1)[0])
    body = {k: v for k, v in first.items() if k != "h"}
    expected = hashlib.sha256(bytes(32) + g.canonical_bytes(body)).hexdigest()
    assert first["h"] == expected == g.log_entry_hash("0" * 64, first)
    assert g.read_log_head(str(path)) == {"n": 1, "h": expected}


def test_prune_nonces_keeps_recent(tmp_path: Path) -> None:
    old, new = tmp_path / ("a" * 32), tmp_path / ("b" * 32)
    old.write_text("1")
    new.write_text("1")
    now = time.time()
    os.utime(old, (now - 3 * 86400, now - 3 * 86400))
    g.prune_nonces(str(tmp_path), now)
    assert not old.exists() and new.exists()
    g.consume_nonce(str(tmp_path), "c" * 32, int(now))
    with pytest.raises(g.GateError, match="replayed"):
        g.consume_nonce(str(tmp_path), "c" * 32, int(now))


def test_comment_lines_never_match(tmp_path: Path, keys: Keys) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir(mode=0o700)
    comment = f"umrs-transport-{SITE}-{KEY_A}"
    (ssh / "authorized_keys").write_text(
        f"# {comment}\nssh-ed25519 {keys.transport_a} {comment}x\n"
    )
    with pytest.raises(g.GateError, match="no_matching_key"):
        g.edit_authorized_keys(str(tmp_path), comment, None)
    with pytest.raises(g.GateError, match="no_matching_key"):
        g.edit_authorized_keys(str(tmp_path / "nohome"), comment, None)


# ---------------------------------------------------------------------------
# B1 review fixes (2026-11-08): time, deadline, lock, log bounds, parsing
# ---------------------------------------------------------------------------


def _cfg(**over: Any) -> g.GateConfig:
    fields: dict[str, Any] = {
        "site": SITE,
        "domain": DOMAIN,
        "principal": PRINCIPAL,
        "uv_required": False,
        "allow_non_sk": True,
        "gate_version": "1",
    }
    fields.update(over)
    return g.GateConfig(**fields)


class _Stdin:
    """A request body whose arrival takes ``delay`` seconds of the patched clock."""

    def __init__(self, data: bytes, clock: list[float], delay: float) -> None:
        self.data, self.clock, self.delay = data, clock, delay

    def read(self, _size: int = -1) -> bytes:
        self.clock[0] += self.delay
        return self.data


def _control(env: Env, verb: str, data: bytes, stdin: Any = None) -> dict[str, Any]:
    import io

    return g.cmd_control(verb, _cfg(), str(env.home), stdin or io.BytesIO(data))


def _log_lines(env: Env) -> list[dict[str, Any]]:
    if not env.log.exists():
        return []
    return [json.loads(line) for line in env.log.read_text().splitlines()]


def test_expiry_is_judged_when_the_body_arrives(
    local: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    t0 = int(time.time())
    clock = [float(t0)]
    monkeypatch.setattr(g.time, "time", lambda: clock[0])
    body = local.control("revoke", issued=t0, expires=t0 + 60)
    stdin = _Stdin(local.signed(body), clock, delay=7 * 86400)
    before = local.ak.read_text()
    with pytest.raises(g.GateError) as caught:
        _control(local, "revoke", b"", stdin=stdin)
    assert caught.value.error == "expired"
    assert local.ak.read_text() == before
    assert _log_lines(local)[-1]["t"] >= t0 + 7 * 86400


def test_deadline_ends_a_session_that_never_sends_its_body(
    local: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    read_end, write_end = os.pipe()
    monkeypatch.setattr(g, "GATE_DEADLINE", 1)
    monkeypatch.setenv("HOME", str(local.home))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "revoke")

    class _Sys:
        buffer = os.fdopen(read_end, "rb")

    monkeypatch.setattr(g.sys, "stdin", _Sys)
    started = time.monotonic()
    try:
        code = g.main(["--site", SITE])
    finally:
        _Sys.buffer.close()
        os.close(write_end)
    assert code == 1 and time.monotonic() - started < 10
    assert json.loads(capsys.readouterr().err) == {"error": "failed", "detail": "deadline"}
    assert [e["detail"] for e in _log_lines(local)] == ["failed: before a valid signature"]


def test_a_held_log_lock_refuses_busy_instead_of_waiting(
    local: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    monkeypatch.setattr(g, "LOCK_WAIT", 0.2)
    (local.root / "log").mkdir(mode=0o700)
    with open(local.log, "a") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(g.GateError) as caught:
            _control(local, "revoke", local.signed(local.control("revoke")))
    assert (caught.value.error, time.monotonic() - started < 5) == ("log_unwritable", True)
    assert "busy" in caught.value.detail


def test_a_failed_append_leaves_no_partial_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    path = tmp_path / "gate.jsonl"
    _write_log(path, 2)
    real = g._write_all

    def short_then_full(fd: int, data: bytes) -> None:
        real(fd, data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    log = g.GateLog(str(path))
    log.open()
    monkeypatch.setattr(g, "_write_all", short_then_full)
    with pytest.raises(OSError):
        log.append("revoke", "refused", "x", 1)
    monkeypatch.setattr(g, "_write_all", real)
    assert path.read_bytes().endswith(b"\n") and g.verify_log(str(path))
    log.append("revoke", "ok", "after", 2)
    log.close()
    assert g.verify_log(str(path)) and g.read_log_head(str(path))["n"] == 3


def test_unsigned_attempts_log_a_fixed_short_entry_and_stop_at_the_cap(
    local: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    junk = b'{"payload":"e30=","signature":"junk"}'
    for _ in range(3):
        with pytest.raises(g.GateError, match="bad_signature"):
            _control(local, "revoke", junk)
    details = [e["detail"] for e in _log_lines(local)]
    assert details == ["bad_signature: before a valid signature"] * 3
    monkeypatch.setattr(g, "LOG_UNSIGNED_CAP", local.log.stat().st_size)
    with pytest.raises(g.GateError, match="bad_signature"):
        _control(local, "revoke", junk)
    assert len(_log_lines(local)) == 3
    _control(local, "revoke", local.signed(local.control("revoke")))
    assert [e["result"] for e in _log_lines(local)] == ["refused"] * 3 + ["ok"]
    assert g.verify_log(str(local.log))


def test_a_non_ascii_signature_is_a_logged_bad_envelope(env: Env) -> None:
    _refused(env.run("revoke", b'{"payload":"e30=","signature":"\\ud800"}'), "bad_envelope")
    assert len(_log_lines(env)) == 1


def test_every_failure_is_logged_even_an_unexpected_one(
    local: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(g, "consume_nonce", boom)
    with pytest.raises(g.GateError) as caught:
        _control(local, "revoke", local.signed(local.control("revoke")))
    assert (caught.value.error, caught.value.code) == ("failed", g.EXIT_FAILED)
    assert _log_lines(local)[-1]["detail"] == "failed: RuntimeError"


def test_rotate_never_installs_a_key_that_is_already_authorized(env: Env) -> None:
    before = env.ak.read_text()
    for blob in (env.keys.transport_b, env.keys.signer_b64):
        request = env.signed(env.control("rotate", new_public_key=f"ssh-ed25519 {blob}"))
        _refused(env.run("rotate", request), "bad_schema")
    _unchanged(env, before)


def test_sacctmgr_never_sees_the_session_environment(env: Env) -> None:
    _stub_sacctmgr(env, 'export -p > "$HOME/envdump"\n')
    extra = {"SLURM_CONF": "/tmp/attacker.conf", "LC_EVIL": "x"}
    assert env.run("hello", extra=extra)[0] == 0
    dump = (env.home / "envdump").read_text()
    assert "SLURM_CONF" not in dump and "LC_EVIL" not in dump and "LC_ALL" in dump


def test_lines_split_at_newline_only_like_sshd(tmp_path: Path, keys: Keys) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir(mode=0o700)
    comment = f"umrs-transport-{SITE}-{KEY_A}"
    text = f"ssh-ed25519 {keys.transport_a} note\x1c{comment}\nssh-ed25519 {keys.transport_b} x\n"
    (ssh / "authorized_keys").write_text(text)
    with pytest.raises(g.GateError, match="no_matching_key"):
        g.edit_authorized_keys(str(tmp_path), comment, None)
    assert (ssh / "authorized_keys").read_text() == text
    assert g.split_lines("a\x0bb c\nd") == ["a\x0bb c\n", "d"]
    assert g.split_lines("a\n") == ["a\n"] and g.split_lines("") == []


def _short_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    real_read, real_pread = os.read, os.pread
    monkeypatch.setattr(g.os, "read", lambda fd, n: real_read(fd, min(n, 7)))
    monkeypatch.setattr(g.os, "pread", lambda fd, n, off: real_pread(fd, min(n, 7), off))


def test_short_reads_never_truncate_authorized_keys(
    local: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gate.jsonl"
    _write_log(path, 2)
    expected = g.read_log_head(str(path))
    lines = local.ak.read_text().splitlines()
    _short_reads(monkeypatch)
    assert g.edit_authorized_keys(str(local.home), f"umrs-transport-{SITE}-{KEY_A}", None) == 1
    assert g.read_log_head(str(path)) == expected
    monkeypatch.undo()
    assert local.ak.read_text().splitlines() == [line for line in lines if KEY_A not in line]
