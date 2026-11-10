"""``r4 hpc add|verify|status|link|unlink|revoke|rotate`` (ADR-112 wave B1).

The six client routes follow the B1.5a wire contract. ``ssh`` is emulated
locally the way sshd runs a command (``sh -c "<joined argv>"`` under the
person's HOME, here a temp dir), so the install script, the line editor and
the installed gate itself really run; nothing reaches a network or the
developer's ``~/.ssh``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hpc_intent_fixtures import PROFILE, canonical, profile_answer
from r4_contracts import hpc_gate

from r4 import hpc_remote
from r4 import hpc_signing as hs
from r4 import hpc_site_profile as sp
from r4._cli import hpc as hpc_cli
from r4.knowledge import COMPANION_SHELL_ENV

KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
pytestmark = pytest.mark.skipif(
    not (os.access(KEYGEN, os.X_OK) and os.access(hs.GATE_PYTHON, os.X_OK)),
    reason="needs ssh-keygen and /usr/bin/python3",
)

SITE = "bdrt"
DOMAIN = "r4.example.org"
HOST = "bdrt-login"
OLD_ID = "tk-0123456789ab"
NEW_ID = "tk-fedcba987654"


def _transport(tmp_path: Path, key_id: str, venue: str = SITE) -> str:
    path = tmp_path / f"transport-{key_id}"
    comment = f"umrs-transport-{venue}-{key_id}"
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)], check=True
    )
    return Path(f"{path}.pub").read_text().strip()


class FakeSite:
    """The UMRS routes, as the contract answers them."""

    def __init__(self, tmp_path: Path, venue: str = SITE) -> None:
        self.venue = venue
        self.keys = {
            OLD_ID: _transport(tmp_path, OLD_ID, venue),
            NEW_ID: _transport(tmp_path, NEW_ID, venue),
        }
        self.calls: list[tuple] = []
        self.account: dict | None = None
        self.links: list[dict] = []
        self.gate_sha = hs.gate_sha256()
        self.profile = {**PROFILE, "site": venue}
        self.profile_current = True

    def hpc_transport_key(self, *, rotate=False):
        self.calls.append(("transport-key", rotate))
        key_id = NEW_ID if rotate else OLD_ID
        return {
            "key_id": key_id,
            "public_key": self.keys[key_id],
            "site": self.venue,
            "domain": DOMAIN,
            "from": "",
            "gate": {"version": hpc_gate.GATE_VERSION, "sha256": self.gate_sha},
        }

    def site_me(self):
        return {"user_id": "alice", "site": self.venue}

    def hpc_site_profile(self):
        self.calls.append(("site-profile",))
        return profile_answer(self.profile)

    def hpc_verify(self, login, *, signers, uv_required, allow_non_sk):
        self.calls.append(("verify", login, tuple(signers), uv_required, allow_non_sk))
        return {
            "account": {
                "status": "active",
                "venue": self.venue,
                "verified_login": login,
                "verified_assocs": [{"account": "alloc-1"}],
            },
            "site_profile_current": self.profile_current,
        }

    def hpc_account(self):
        return {"account": self.account, "links": self.links}

    def hpc_link(self, project_id, slurm_account):
        self.calls.append(("link", project_id, slurm_account))
        return {"link": {"project_id": project_id, "slurm_account": slurm_account}}

    def hpc_unlink(self, project_id):
        self.calls.append(("unlink", project_id))
        return {"status": "deleted"}

    def hpc_revoke(self):
        self.calls.append(("revoke",))
        return {"status": "revoked"}

    def hpc_gate(self):
        return {"log_head": None}


@pytest.fixture
def remote_home(tmp_path):
    home = tmp_path / "remote"
    home.mkdir()
    return home


@pytest.fixture
def ssh_calls(monkeypatch, remote_home):
    """Every ``ssh`` argv; the command runs as sshd would, under ``remote_home``."""
    calls: list[list[str]] = []

    def fake_ssh(argv, **kwargs):
        calls.append(list(argv))
        assert argv[0] == "ssh" and argv[1] == HOST
        env = {"HOME": str(remote_home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        return subprocess.run(
            ["sh", "-c", " ".join(argv[2:])],
            input=kwargs.get("input"),
            stdout=subprocess.PIPE,
            env=env,
            check=False,
        )

    monkeypatch.setattr(hpc_remote, "_run", fake_ssh)
    return calls


@pytest.fixture
def site(monkeypatch, tmp_path):
    fake = FakeSite(tmp_path)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda name: fake)
    return fake


@pytest.fixture
def person(monkeypatch):
    """The person at a terminal who answers yes, with an ed25519 signing key."""
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(hs, "openssh_version", lambda: (9, 6))
    monkeypatch.setattr(hs, "_ALLOW_UNENCRYPTED_KEYS", True)  # the private test hook
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    key = hs.key_path("ed25519")
    key.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)], check=True
    )
    return key


def run(argv: list[str]) -> int:
    from r4.cli import _build_parser

    args = _build_parser().parse_args(["hpc", *argv])
    return hpc_cli.HpcCommand().handle(args)


def _mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


def _gate(remote_home: Path, verb: str) -> subprocess.CompletedProcess:
    gate = remote_home / ".umrs" / "gate" / "umrs_gate.py"
    env = {"HOME": str(remote_home), "SSH_ORIGINAL_COMMAND": verb, "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [hs.GATE_PYTHON, "-I", str(gate), "--site", SITE],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


# ── routes (B1.5a HTTP API) ──────────────────────────────────────────────────


@pytest.mark.parametrize(("direct", "prefix"), [(False, "/gw"), (True, "/umrs")])
def test_the_six_routes_match_the_contract(direct, prefix):
    from r4.client import Client
    from r4.config import Config

    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client._is_umrs_direct = direct
    for verb in ("get", "post", "delete"):
        setattr(client.http, verb, MagicMock(return_value={}))
    client.hpc_transport_key(rotate=True)
    client.hpc_verify("alice", signers=["SHA256:x"], uv_required=True, allow_non_sk=False)
    client.hpc_account()
    client.hpc_link("proj/1", "alloc-1")
    client.hpc_unlink("proj/1")
    client.hpc_revoke()
    base = f"http://gw:8080{prefix}/sites/me/hpc"
    posts = [(c.args[0], c.kwargs["json_body"]) for c in client.http.post.call_args_list]
    assert posts == [
        (f"{base}/transport-key", {"rotate": True}),
        (
            f"{base}/verify",
            {"login": "alice", "signers": ["SHA256:x"], "uv_required": True, "allow_non_sk": False},
        ),
        (f"{base}/links", {"project_id": "proj/1", "slurm_account": "alloc-1"}),
    ]
    assert [c.args[0] for c in client.http.get.call_args_list] == [base]
    assert [c.args[0] for c in client.http.delete.call_args_list] == [
        f"{base}/links/proj%2F1",
        base,
    ]


# ── registration ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["add", SITE, "--ssh", HOST, "--key-type", "ed25519"],
        ["add", SITE, "--manual", "--home", "/home/alice"],
        ["verify", SITE, "--login", "alice"],
        ["status"],
        ["status", SITE, "--json"],
        ["link", "--project", "p", "--account", "a", "--site", SITE],
        ["unlink", "--project", "p"],
        ["revoke", SITE, "--remove-line", "--ssh", HOST],
        ["rotate", SITE, "--manual", "--home", "/home/alice"],
        ["profile", SITE, "--ssh", HOST, "--login", "alice"],
        ["profile", SITE, "--manual"],
        ["status", SITE, "--witness", "--ssh", HOST],
    ],
)
def test_account_subcommands_parse(argv):
    from r4.cli import _build_parser

    args = _build_parser().parse_args(["hpc", *argv])
    assert args.hpc_command == argv[0]


def test_usage_names_every_subcommand(capsys):
    from argparse import Namespace

    assert hpc_cli.HpcCommand().handle(Namespace(hpc_command=None)) == 2
    usage = capsys.readouterr().err
    for name in ("sites", "add", "verify", "status", "link", "unlink", "revoke", "rotate"):
        assert name in usage


def test_no_agent_tool_reaches_signing_or_install():
    """The account commands are the person's: no Companion tool wraps them."""
    import r4.companion.tools as tools

    text = Path(tools.__file__).read_text()
    for name in (
        "hpc_signing",
        "hpc_remote",
        "hpc_transport_key",
        "hpc_verify",
        "hpc_revoke",
        "hpc_link",
        "ensure_signing_key",
    ):
        assert name not in text


# ── who may run them ─────────────────────────────────────────────────────────


PERSON_ONLY = [
    ["add", SITE],
    ["verify", SITE, "--login", "alice"],
    ["link", "--project", "p", "--account", "a"],
    ["unlink", "--project", "p"],
    ["revoke", SITE],
    ["rotate", SITE],
    ["profile", SITE],
]


@pytest.mark.parametrize("argv", PERSON_ONLY)
def test_account_commands_refuse_in_a_companion_shell(monkeypatch, capsys, argv):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda n: pytest.fail("no platform call"))
    monkeypatch.setattr(hpc_remote, "_run", lambda *a, **k: pytest.fail("no ssh"))
    assert run(argv) == 2
    assert "person's act" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv", [["add", SITE], ["revoke", SITE], ["rotate", SITE], ["profile", SITE]]
)
def test_key_commands_refuse_without_a_terminal(monkeypatch, capsys, argv):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda n: pytest.fail("no platform call"))
    assert run(argv) == 2
    assert "needs a terminal" in capsys.readouterr().err


def test_status_runs_in_a_companion_shell(monkeypatch, site, capsys):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    assert run(["status", SITE]) == 0
    assert "No HPC account" in capsys.readouterr().out


# ── add ──────────────────────────────────────────────────────────────────────


def test_add_checks_the_signed_release_when_configured(person, site, monkeypatch):
    from r4 import hpc_release

    seen = {}

    def checked(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(hpc_release, "verify_environment", checked)
    assert run(["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    assert seen["domain"] == DOMAIN


def test_add_manual_prints_the_files_and_the_line_exactly(person, site, monkeypatch, capsys):
    """A new account (the site reports none): the grant epoch is part of
    the install, so no grant signed before it can act (plan §6 K7)."""
    monkeypatch.setattr(hpc_remote, "_run", lambda *a, **k: pytest.fail("--manual never SSHes"))
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.5)
    assert run(["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    key_type, key_b64, _ = hs.public_key_info(person)
    transport_b64 = site.keys[OLD_ID].split()[1]
    gate = hs.gate_source().decode().rstrip("\n")
    data = canonical(PROFILE)
    shown = sp.render(sp.SiteProfile(PROFILE, data, hashlib.sha256(data).hexdigest()), None)
    site_json = "~/.umrs/gate/site.json"
    expected = (
        "\n".join(
            [
                "This is a new or re-enrolled account: grants signed before now end at your gate.",
                shown,
                "Install these in your HPC home at the site, then verify.",
                "",
                "mkdir -p ~/.umrs/gate/state/nonces ~/.umrs/gate/log && chmod 700 ~/.umrs "
                "~/.umrs/gate ~/.umrs/gate/state ~/.umrs/gate/state/nonces ~/.umrs/gate/log",
                "",
                "==> ~/.umrs/gate/gate.json (mode 0600) <==",
                '{"allow_non_sk":true,"domain":"r4.example.org","gate_version":'
                f'"{hpc_gate.GATE_VERSION}","principal":"alice","site":"bdrt","uv_required":false}}',
                "==> end of gate.json <==",
                "",
                "==> ~/.umrs/gate/allowed_signers (mode 0600) <==",
                'alice namespaces="umrs-intent-v1@r4.example.org,umrs-grant-v1@r4.example.org,'
                f'umrs-control-v1@r4.example.org" {key_type} {key_b64}',
                "==> end of allowed_signers <==",
                "",
                f"==> write {site_json} with exactly these bytes (mode 0600): run <==",
                f"printf '%s' {shlex.quote(data.decode())} > {site_json} && chmod 600 {site_json}",
                "",
                *hpc_remote.manual_epoch_command(hs.epoch_bytes(1_800_000_000)).splitlines(),
                "",
                f"==> ~/.umrs/gate/umrs_gate.py (mode 0600; sha256 {hs.gate_sha256()}) <==",
                gate,
                "==> end of umrs_gate.py <==",
                "",
                "==> append this one line to ~/.ssh/authorized_keys (file mode 0600, ~/.ssh mode "
                "0700) <==",
                'restrict,command="/usr/bin/python3 -I /home/alice/.umrs/gate/umrs_gate.py --site '
                f'bdrt" ssh-ed25519 {transport_b64} umrs-transport-bdrt-{OLD_ID}',
                "",
                "Then run: r4 hpc verify bdrt --login <your login at the site>",
            ]
        )
        + "\n"
    )
    assert capsys.readouterr().out == expected
    assert site.calls == [("transport-key", False), ("site-profile",)]


def _epoch(remote_home: Path) -> Path:
    return remote_home / ".umrs" / "gate" / "state" / "grants" / "epoch"


@pytest.mark.parametrize("status", [None, "revoked", "pending"])
def test_add_for_an_absent_or_revoked_account_writes_the_epoch_over_own_ssh(
    person, site, ssh_calls, remote_home, monkeypatch, status
):
    """K7: after a revoke (or for a new account) the gate learns the install
    time, so grants signed before it refuse ``grant_cancelled``."""
    site.account = None if status is None else {"status": status, "venue": SITE}
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.9)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    epoch = _epoch(remote_home)
    assert epoch.read_bytes() == b'{"epoch":1800000000}'
    assert (_mode(epoch.parent), _mode(epoch)) == ("0o700", "0o600")
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_000
    assert ssh_calls[1] == ["ssh", HOST, "sh", "-c", shlex.quote(hpc_remote.INSTALL_SCRIPT)]


@pytest.mark.parametrize("status", ["active", "suspended"])
def test_add_for_an_account_still_there_bootstraps_zero(
    person, site, ssh_calls, remote_home, status
):
    """K7: an active upgrade with existing grant state ends no grant."""
    site.account = {"status": status, "venue": SITE, "verified_login": "alice"}
    _epoch(remote_home).parent.mkdir(parents=True)
    (_epoch(remote_home).parent / f"{'ab' * 16}.json").write_text("{}")
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert (remote_home / ".umrs" / "gate" / "umrs_gate.py").is_file()
    assert _epoch(remote_home).read_bytes() == b'{"epoch":0}'
    root = str(remote_home / ".umrs" / "gate")
    grant = {"grant_id": "ab" * 16, "issued": 1}
    assert hpc_gate._grant_ended(root, grant) == ""


def test_add_where_the_gate_has_no_grant_state_writes_the_epoch(
    person, site, ssh_calls, remote_home, monkeypatch
):
    """Review 2026-11-09: a gate whose state was lost (or a first install)
    gets the install time as its epoch, so a grant signed before, even a
    cancelled one, cannot run again with fresh counters."""
    site.account = {"status": "active", "venue": SITE, "verified_login": "alice"}
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_000


def test_add_with_an_empty_grant_directory_treats_it_as_fresh_state(
    person, site, ssh_calls, remote_home, monkeypatch
):
    """A surviving directory with no counters or cancel records is state loss."""
    site.account = {"status": "active", "venue": SITE, "verified_login": "alice"}
    _epoch(remote_home).parent.mkdir(parents=True)
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_000


def test_first_grant_at_the_initial_epoch_remains_valid(
    person, site, ssh_calls, remote_home, monkeypatch
):
    """A newly signed first grant may have the same whole-second timestamp."""
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    root = str(remote_home / ".umrs" / "gate")
    grant = {"grant_id": "ab" * 16, "issued": 1_800_000_000}
    assert hpc_gate._grant_ended(root, grant) == ""


def test_reenrolment_advances_an_existing_epoch(person, site, ssh_calls, remote_home, monkeypatch):
    """An install neither lowers nor reuses the preceding revocation boundary."""
    _epoch(remote_home).parent.mkdir(parents=True)
    _epoch(remote_home).write_bytes(hs.epoch_bytes(1_800_000_100))
    hs.update_site_record(SITE, epoch_due=True)
    site.account = {"status": "active", "venue": SITE, "verified_login": "alice"}
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_101


def test_manual_active_install_includes_guarded_fresh_and_legacy_epochs(
    person, site, monkeypatch, capsys
):
    """Manual installation bootstraps fresh state without ending an upgrade's grants."""
    site.account = {"status": "active", "venue": SITE, "verified_login": "alice"}
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    monkeypatch.setattr(hpc_remote, "_run", lambda *a, **k: pytest.fail("--manual never SSHes"))
    assert run(["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    out = capsys.readouterr().out
    assert "[ ! -f ~/.umrs/gate/state/grants/epoch ]" in out
    assert "[ -d ~/.umrs/gate/state/grants ]" in out
    assert "'{\"epoch\":1800000000}'" in out
    assert "'{\"epoch\":0}'" in out
    assert shlex.quote(hpc_remote.LEGACY_GRANT_STATE_PY) in out


def test_revoke_then_a_failed_add_then_add_still_writes_the_epoch(
    person, site, ssh_calls, remote_home, monkeypatch
):
    """Review 2026-11-09: a revoke followed by an add the person declined
    leaves the account ``pending`` (or the site may answer ``active``); r4's
    own record of the revoke still makes the next add write the epoch."""
    _epoch(remote_home).parent.mkdir(parents=True)
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["revoke", SITE]) == 0
    assert hs.read_site_record(SITE)["epoch_due"] is True
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 1
    assert not _epoch(remote_home).exists()
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_100.0)
    site.account = {"status": "active", "venue": SITE, "verified_login": "alice"}
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_100
    assert hs.read_site_record(SITE)["epoch_due"] is False


def test_add_refuses_a_venue_another_site_record_holds(
    person, site, ssh_calls, remote_home, capsys
):
    """Review 2026-11-09: one venue per gate; a grant for it would pass at both."""
    hs.update_site_record("other-cluster", venue=SITE, domain=DOMAIN)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) != 0
    err = capsys.readouterr().err
    assert "other-cluster" in err and "venue" in err
    assert not (remote_home / ".umrs" / "gate" / "umrs_gate.py").exists()
    assert hs.read_site_record(SITE).get("venue") is None


def test_reenrolment_at_a_different_destination_is_refused(person, site, capsys):
    """One record cannot silently move its signer to another physical gate."""
    hs.record_site_signer(SITE, "ed25519", person)
    hs.update_site_record(SITE, venue=SITE, domain=DOMAIN, ssh_destination="first-login")
    argv = [
        "add",
        SITE,
        "--manual",
        "--ssh",
        "second-login",
        "--home",
        "/home/alice",
        "--key-type",
        "ed25519",
    ]
    assert run(argv) != 0
    assert "different SSH destination" in capsys.readouterr().err
    assert hs.read_site_record(SITE)["ssh_destination"] == "first-login"


def test_reenrolment_with_an_unknown_legacy_destination_is_refused(person, site, capsys):
    """Legacy cancel/revoke stay available; unsafe re-provisioning does not guess."""
    hs.record_site_signer(SITE, "ed25519", person)
    hs.update_site_record(SITE, venue=SITE, domain=DOMAIN)
    argv = ["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]
    assert run(argv) != 0
    assert "no recorded SSH destination" in capsys.readouterr().err
    assert hs.site_signer(SITE, "ed25519")[1] == person


def test_add_manual_needs_home(person, site, capsys):
    assert run(["add", SITE, "--manual", "--key-type", "ed25519"]) == 1
    assert "--home" in capsys.readouterr().err


def test_add_sends_only_fixed_scripts_over_own_ssh(person, site, ssh_calls, remote_home):
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert ssh_calls == [
        ["ssh", HOST, "sh", "-c", shlex.quote(hpc_remote.PROBE_SCRIPT)],
        ["ssh", HOST, "sh", "-c", shlex.quote(hpc_remote.INSTALL_SCRIPT)],
    ]
    # nothing variable rides the command line: it all went through stdin
    flat = " ".join(" ".join(call) for call in ssh_calls)
    variables = (site.keys[OLD_ID].split()[1], "alice", DOMAIN, str(remote_home), OLD_ID)
    assert [v for v in variables if v in flat] == []
    login = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    fp = hs.public_key_info(person)[2]
    assert site.calls[-1] == ("verify", login, (fp,), False, True)


def test_add_writes_the_layout_with_tight_modes(person, site, ssh_calls, remote_home):
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    gate_dir = remote_home / ".umrs" / "gate"
    keys = remote_home / ".ssh" / "authorized_keys"
    names = ("umrs_gate.py", "gate.json", "allowed_signers", "site.json")
    files = [gate_dir / n for n in names] + [keys]
    dirs = (gate_dir, remote_home / ".ssh")
    assert [_mode(p) for p in (*dirs, *files)] == ["0o700"] * 2 + ["0o600"] * 5
    assert (gate_dir / "umrs_gate.py").read_bytes() == hs.gate_source()
    assert (gate_dir / "site.json").read_bytes() == canonical(PROFILE)  # byte for byte
    (line,) = keys.read_text().splitlines()
    gate = f"{remote_home}/.umrs/gate/umrs_gate.py"
    head = f'restrict,command="/usr/bin/python3 -I {gate} --site {SITE}" '
    assert line == head + site.keys[OLD_ID]


def test_installed_gate_answers_probe_and_hello(person, site, ssh_calls, remote_home):
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    probe = _gate(remote_home, "probe")
    assert probe.returncode == hpc_gate.EXIT_REFUSED
    assert json.loads(probe.stderr)["error"] == "gate_probe_refusal"
    hello = _gate(remote_home, "hello")
    assert hello.returncode == 0, hello.stderr
    answer = json.loads(hello.stdout)
    assert answer["gate_sha256"] == hs.gate_sha256() and answer["site"] == SITE
    assert [s["fingerprint"] for s in answer["signers"]] == [hs.public_key_info(person)[2]]


def test_add_twice_keeps_one_line_and_repairs_a_missing_newline(
    person, site, ssh_calls, remote_home
):
    ssh_dir = remote_home / ".ssh"
    ssh_dir.mkdir(mode=0o755)
    (ssh_dir / "authorized_keys").write_text("ssh-ed25519 AAAA other-key")  # no newline
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    lines = (ssh_dir / "authorized_keys").read_text().splitlines()
    assert lines[0] == "ssh-ed25519 AAAA other-key"
    assert len(lines) == 2 and lines[1].endswith(OLD_ID)
    assert _mode(ssh_dir) == "0o700"


def test_add_declined_installs_nothing(person, site, ssh_calls, monkeypatch, remote_home):
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 1
    assert len(ssh_calls) == 1  # the probe only
    assert not (remote_home / ".umrs").exists()


def test_add_refuses_gate_version_skew(person, site, ssh_calls, capsys):
    site.gate_sha = "0" * 64
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 1
    assert "version skew" in capsys.readouterr().err
    assert all(call[-1] != shlex.quote(hpc_remote.INSTALL_SCRIPT) for call in ssh_calls)


def test_add_refuses_a_tampered_transport_key(person, site, ssh_calls, capsys):
    site.keys[OLD_ID] = site.keys[OLD_ID] + "\nssh-ed25519 AAAA attacker"
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 1
    assert "refused" in capsys.readouterr().err
    assert ssh_calls == []


def test_an_option_shaped_ssh_destination_is_refused(person, site, ssh_calls, capsys):
    assert run(["add", SITE, "--ssh=-oProxyCommand=touch /tmp/x", "--key-type", "ed25519"]) == 1
    assert "ssh destination" in capsys.readouterr().err
    assert ssh_calls == []


# ── verify, status, link ─────────────────────────────────────────────────────


def test_verify_sends_login_and_public_fingerprint(person, site, capsys):
    assert run(["verify", SITE, "--login", "alice"]) == 0
    assert site.calls == [("verify", "alice", (hs.public_key_info(person)[2],), False, True)]
    assert "alloc-1" in capsys.readouterr().out


def test_status_compares_the_gate_sha(site, capsys):
    site.account = {"status": "active", "venue": SITE, "gate_sha256": "f" * 64}
    assert run(["status", SITE]) == 0
    assert "differs from the one the site verified" in capsys.readouterr().err
    assert run(["status", SITE, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gate_match"] is False and out["local_gate_sha256"] == hs.gate_sha256()
    site.account["gate_sha256"] = hs.gate_sha256()
    assert run(["status", SITE]) == 0
    assert "differs" not in capsys.readouterr().err


def test_link_and_unlink_call_the_routes(person, site):
    assert run(["link", "--project", "p1", "--account", "alloc-1", "--site", SITE]) == 0
    assert run(["unlink", "--project", "p1", "--site", SITE]) == 0
    assert site.calls == [("link", "p1", "alloc-1"), ("unlink", "p1")]


# ── revoke and rotate ────────────────────────────────────────────────────────


def _seed_keys(remote_home: Path, site: FakeSite) -> Path:
    ssh_dir = remote_home / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    keys = ssh_dir / "authorized_keys"
    line = f'restrict,command="x --site {SITE}" {site.keys[OLD_ID]}'
    keys.write_text(f"ssh-ed25519 AAAA mine\n{line}\nssh-ed25519 BBBB laptop\n")
    keys.chmod(0o600)
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    return keys


def test_revoke_destroys_the_key_and_prints_the_line_to_delete(person, site, ssh_calls, capsys):
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    assert run(["revoke", SITE]) == 0
    assert site.calls == [("revoke",)] and ssh_calls == []
    assert f"' umrs-transport-{SITE}-{OLD_ID}'" in capsys.readouterr().out


def test_revoke_remove_line_edits_over_own_ssh(person, site, ssh_calls, remote_home, monkeypatch):
    keys = _seed_keys(remote_home, site)
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["revoke", SITE, "--remove-line", "--ssh", HOST]) == 0
    assert site.calls == [("revoke",)]
    assert ssh_calls == [
        ["ssh", HOST, "/usr/bin/python3", "-I", "-c", shlex.quote(hpc_remote.EDIT_LINES_PY)],
        ["ssh", HOST, "/usr/bin/python3", "-I", "-c", shlex.quote(hpc_remote.EPOCH_PY)],
    ]
    assert OLD_ID not in " ".join(ssh_calls[0])
    # review 2026-11-09: the gate itself ends every earlier grant, relayed by no one
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_000
    assert _mode(_epoch(remote_home)) == "0o600"
    assert keys.read_text() == "ssh-ed25519 AAAA mine\nssh-ed25519 BBBB laptop\n"
    assert _mode(keys) == "0o600"


def test_revoke_remove_line_advances_an_existing_epoch(
    person, site, ssh_calls, remote_home, monkeypatch
):
    _seed_keys(remote_home, site)
    _epoch(remote_home).parent.mkdir(parents=True)
    _epoch(remote_home).write_bytes(hs.epoch_bytes(1_800_000_100))
    monkeypatch.setattr(hpc_cli, "_clock", lambda: 1_800_000_000.0)
    assert run(["revoke", SITE, "--remove-line", "--ssh", HOST]) == 0
    assert hpc_gate.read_epoch(str(remote_home / ".umrs" / "gate")) == 1_800_000_101


def test_revoke_without_an_account_is_refused(person, site, capsys):
    assert run(["revoke", SITE]) == 1
    assert site.calls == [] and "no HPC account" in capsys.readouterr().err


def test_rotate_replaces_the_line_and_verifies(person, site, ssh_calls, remote_home):
    keys = _seed_keys(remote_home, site)
    assert run(["rotate", SITE, "--ssh", HOST]) == 0
    mine, laptop, new = keys.read_text().splitlines()
    assert (mine, laptop) == ("ssh-ed25519 AAAA mine", "ssh-ed25519 BBBB laptop")
    assert new.endswith(" " + site.keys[NEW_ID])
    assert [c[:2] for c in site.calls] == [("transport-key", True), ("verify", site.calls[1][1])]
    flat = " ".join(map(" ".join, ssh_calls))
    assert (NEW_ID in flat, OLD_ID in flat) == (False, False)


def _fork() -> None:
    hs.update_site_record(SITE, witness_fork={"seen_at": "2026-11-09T00:00:00Z", "reason": "x"})


def test_a_recorded_fork_refuses_rotate(person, site, ssh_calls, capsys):
    """K20: a fork blocks new authority (rotate), before any request."""
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    _fork()
    assert run(["rotate", SITE, "--ssh", HOST]) == 1
    err = capsys.readouterr().err
    assert "FORK" in err and f"r4 hpc status {SITE} --witness --clear-fork" in err
    assert site.calls == [] and ssh_calls == []


def test_a_recorded_fork_never_blocks_revoke(person, site, ssh_calls, capsys):
    """K20: revoke takes authority away, so it proceeds and shows the fork."""
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    _fork()
    assert run(["revoke", SITE]) == 0
    assert site.calls == [("revoke",)]
    captured = capsys.readouterr()
    assert "FORK" in captured.err and "revoke is allowed" in captured.err
    assert "revoked" in captured.out


def test_rotate_manual_prints_the_swap(person, site, monkeypatch, capsys):
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    monkeypatch.setattr(hpc_remote, "_run", lambda *a, **k: pytest.fail("--manual never SSHes"))
    assert run(["rotate", SITE, "--manual", "--home", "/home/alice"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        f"In ~/.ssh/authorized_keys at the site, delete the line ending in "
        f"' umrs-transport-{SITE}-{OLD_ID}'"
    )
    assert out[2].endswith(f"umrs-transport-{SITE}-{NEW_ID}")
    assert out[-1] == f"Then run: r4 hpc verify {SITE} --login <your login at the site>"


# ── B1 review fixes (2026-11-08) ─────────────────────────────────────────────


def _client():
    from r4.client import Client
    from r4.config import Config

    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client._is_umrs_direct = True
    for verb in ("get", "post", "delete"):
        setattr(client.http, verb, MagicMock(return_value={}))
    return client


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.hpc_transport_key(rotate=True),
        lambda c: c.hpc_transport_key(),
        lambda c: c.hpc_verify("a", signers=[], uv_required=True, allow_non_sk=False),
        lambda c: c.hpc_link("p", "a"),
        lambda c: c.hpc_unlink("p"),
        lambda c: c.hpc_revoke(),
    ],
)
def test_the_client_refuses_account_changes_in_a_companion_shell(monkeypatch, call):
    """Not only the CLI: ``python -c`` through the client is refused too."""
    from r4.errors import R4Error

    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    client = _client()
    with pytest.raises(R4Error, match="person's act"):
        call(client)
    assert [m.call_count for m in (client.http.post, client.http.delete)] == [0, 0]
    client.hpc_account()  # reading stays open
    assert client.http.get.call_count == 1


def test_a_declined_rotate_changes_nothing_at_the_site(person, site, monkeypatch, capsys):
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run(["rotate", SITE, "--manual", "--home", "/home/alice"]) == 1
    assert site.calls == [] and "nothing rotated" in capsys.readouterr().err


def test_a_failed_line_swap_prints_the_manual_swap(person, site, monkeypatch, capsys):
    site.account = {"status": "active", "venue": SITE, "transport_key_id": OLD_ID}
    monkeypatch.setattr(
        hpc_cli, "_where", lambda args: hpc_remote.RemoteHome(login="alice", home="/home/alice")
    )

    def broken(*_a, **_k):
        raise hs.HpcSigningError("ssh bdrt-login: connection closed")

    monkeypatch.setattr(hpc_remote, "edit_lines", broken)
    assert run(["rotate", SITE, "--ssh", HOST]) == 1
    err = capsys.readouterr().err
    assert f"delete the line ending in ' umrs-transport-{SITE}-{OLD_ID}'" in err
    assert f"umrs-transport-{SITE}-{NEW_ID}" in err and "connection closed" in err


def test_verify_signs_with_the_key_add_installed(person, site, capsys):
    sk = hs.key_path("sk")
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(sk)], check=True)
    assert run(["verify", SITE, "--login", "alice"]) == 1
    assert "--key-type" in capsys.readouterr().err and site.calls == []
    assert run(["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    assert run(["verify", SITE, "--login", "alice"]) == 0
    fingerprint = hs.public_key_info(person)[2]
    assert site.calls[-1] == ("verify", "alice", (fingerprint,), False, True)


def test_add_with_a_key_missing_its_pub_fails_cleanly(person, site, capsys):
    Path(f"{person}.pub").unlink()
    argv = ["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]
    assert run(argv) == 1
    assert "has no .pub" in capsys.readouterr().err


def test_a_second_site_add_installs_a_distinct_signing_key(person, site, monkeypatch, tmp_path):
    other = "rccs"
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_site = FakeSite(other_root, other)
    hs.record_site_signer(SITE, "ed25519", person)
    monkeypatch.setattr(hpc_cli, "_site_client", lambda name: other_site if name == other else site)
    assert run(["add", other, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    first = hs.read_site_record(SITE)["fingerprint"]
    second = hs.read_site_record(other)["fingerprint"]
    assert second != first
    _, key = hs.site_signer(other, "ed25519")
    assert key == hs.key_path("ed25519", other)


# ── wave B2: the site profile, the records, the witness ─────────────────────


def test_add_records_what_a_signed_intent_is_checked_against(person, site, ssh_calls):
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    login = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    record = hs.read_site_record(SITE)
    wanted = {
        "venue": SITE,
        "domain": "r4.example.org",
        "login": login,
        "kind": "ed25519",
        "ssh_destination": HOST,
        "site_profile_sha256": hashlib.sha256(canonical(PROFILE)).hexdigest(),
    }
    assert {k: record.get(k) for k in wanted} == wanted
    assert (sp.installed_profile(SITE), sp.queue_seconds(SITE)) == (PROFILE, 600)
    path = hs.site_record_path(SITE)
    assert (_mode(path), _mode(path.parent)) == ("0o600", "0o700")


def test_a_profile_copy_and_a_site_record_never_share_a_file(person):
    """Fixer finding 2026-11-08: the copy for site ``X`` was
    ``sites/X.site.json``, the record of a site named ``X.site``; writing
    that record overwrote X's profile, and every gated trigger at X then
    refused ("no site profile installed")."""
    data = canonical(PROFILE)
    sp.remember(SITE, sp.SiteProfile(PROFILE, data, hashlib.sha256(data).hexdigest()))
    hs.update_site_record(f"{SITE}.site", venue="other")
    assert sp._copy_path(SITE) != hs.site_record_path(f"{SITE}.site")
    assert (sp.installed_profile(SITE), sp.queue_seconds(SITE)) == (PROFILE, 600)
    assert "domain" not in hs.read_site_record(f"{SITE}.site")
    assert _mode(sp._copy_path(SITE)) == "0o600"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda a: a.update(sha256="0" * 64), "sha256 does not match"),
        (lambda a: a.update(bytes_b64="%%%"), "not base64"),
        (lambda a: a.update(profile_answer(dict(PROFILE, site="other"))), "names site"),
        (lambda a: a.update(profile_answer(dict(PROFILE, extra=1))), "not valid"),
    ],
)
def test_add_refuses_a_bad_site_profile(
    person, site, ssh_calls, monkeypatch, capsys, change, reason
):
    answer = profile_answer()
    change(answer)
    monkeypatch.setattr(site, "hpc_site_profile", lambda: answer)
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 1
    assert reason in capsys.readouterr().err
    assert ssh_calls == []  # refused before the probe and the install


def test_profile_shows_the_diff_reinstalls_and_verifies(
    person, site, ssh_calls, remote_home, capsys
):
    assert run(["add", SITE, "--ssh", HOST, "--key-type", "ed25519"]) == 0
    capsys.readouterr()
    newer = json.loads(json.dumps(PROFILE))
    newer["slurm"]["queue_seconds"] = 900
    site.profile = newer
    ssh_calls.clear()
    assert run(["profile", SITE, "--ssh", HOST]) == 0
    out = capsys.readouterr().out
    assert "~ slurm.queue_seconds: 600 -> 900" in out
    assert ssh_calls == [["ssh", HOST, "sh", "-c", shlex.quote(hpc_remote.PROFILE_SCRIPT)]]
    site_json = remote_home / ".umrs" / "gate" / "site.json"
    assert (site_json.read_bytes(), _mode(site_json)) == (canonical(newer), "0o600")
    recorded = (sp.queue_seconds(SITE), hs.read_site_record(SITE)["site_profile_sha256"])
    assert recorded == (900, hashlib.sha256(canonical(newer)).hexdigest())
    assert site.calls[-1][0] == "verify"


def test_profile_manual_prints_one_exact_write(person, site, monkeypatch, capsys):
    assert run(["add", SITE, "--manual", "--home", "/home/alice", "--key-type", "ed25519"]) == 0
    hs.update_site_record(SITE, login="alice")
    monkeypatch.setattr(hpc_remote, "_run", lambda *a, **k: pytest.fail("--manual never SSHes"))
    capsys.readouterr()
    assert run(["profile", SITE, "--manual"]) == 0
    out = capsys.readouterr().out
    assert "No changes." in out
    assert f"printf '%s' {shlex.quote(canonical(PROFILE).decode())} > ~/.umrs/gate/site.json" in out
    assert out.rstrip().endswith(f"Then run: r4 hpc verify {SITE} --login alice")


def test_profile_needs_an_installed_gate(person, site, capsys):
    assert run(["profile", SITE, "--ssh", HOST]) == 1
    assert "r4 hpc add" in capsys.readouterr().err
    assert site.calls == []


def test_verify_warns_when_the_site_profile_is_stale(person, site, capsys):
    site.profile_current = False
    assert run(["verify", SITE, "--login", "alice"]) == 0
    assert f"r4 hpc profile {SITE}" in capsys.readouterr().err
    assert hs.read_site_record(SITE)["login"] == "alice"


def test_status_live_shows_the_sites_hello_of_my_gate(site, monkeypatch, capsys):
    live = {
        "gate_version": hpc_gate.GATE_VERSION,
        "gate_sha256": hs.gate_sha256(),
        "site_profile_sha256": "a" * 64,
        "log_head": {"n": 7, "h": "b" * 64},
        "current": {"gate": True, "site_profile": False},
    }
    monkeypatch.setattr(site, "hpc_gate", lambda: live, raising=False)
    assert run(["status", SITE, "--live"]) == 0
    out = capsys.readouterr().out
    assert f"sha256 {hs.gate_sha256()} (this r4's)" in out
    assert "stale: run `r4 hpc profile <site>`" in out
    assert f"7 entries, head {'b' * 64}" in out


def test_status_live_passes_the_exact_hello_to_release_verification(site, monkeypatch):
    from r4 import hpc_release

    live = {
        "gate_version": hpc_gate.GATE_VERSION,
        "gate_sha256": hs.gate_sha256(),
        "log_head": {"n": 0, "h": "0" * 64},
    }
    seen = {}
    monkeypatch.setattr(site, "hpc_gate", lambda: live, raising=False)
    monkeypatch.setattr(
        hpc_release,
        "verify_environment",
        lambda **kwargs: seen.update(kwargs) or object(),
    )
    assert run(["status", SITE, "--live"]) == 0
    assert seen["hello"] is live


def test_status_live_json_calls_release_verification_with_real_hello(site, monkeypatch, capsys):
    from r4 import hpc_release

    live = {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": hs.gate_sha256()}
    seen = {}
    hs.update_site_record(SITE, domain=DOMAIN)
    monkeypatch.setattr(site, "hpc_gate", lambda: live, raising=False)
    monkeypatch.setattr(
        hpc_release,
        "verify_environment",
        lambda **kwargs: seen.update(kwargs) or object(),
    )
    assert run(["status", SITE, "--live", "--json"]) == 0
    assert seen["domain"] == DOMAIN and seen["hello"] is live
    body = json.loads(capsys.readouterr().out)
    assert body["release_verified"] is True and body["live_gate"] == live


def test_status_live_json_propagates_release_failure(site, monkeypatch, capsys):
    from r4 import hpc_release

    def refuse(**_kwargs):
        raise hpc_release.ReleaseError("release_gate_hash_mismatch", "synthetic mismatch")

    hs.update_site_record(SITE, domain=DOMAIN)
    monkeypatch.setattr(
        site,
        "hpc_gate",
        lambda: {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": hs.gate_sha256()},
        raising=False,
    )
    monkeypatch.setattr(hpc_release, "verify_environment", refuse)
    assert run(["status", SITE, "--live", "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "release_gate_hash_mismatch" in captured.err


def test_status_witness_and_live_is_rejected_before_verification(site, monkeypatch, capsys):
    from r4 import hpc_release

    monkeypatch.setattr(hpc_cli, "_witness", lambda _args: 0)
    monkeypatch.setattr(
        hpc_release,
        "verify_environment",
        lambda **_kwargs: pytest.fail("combined witness/live must not silently skip verification"),
    )
    assert run(["status", SITE, "--witness", "--live"]) == 2
    assert "--witness and --live cannot be combined" in capsys.readouterr().err


def test_status_live_requires_recorded_domain_for_release_mode(site, monkeypatch, capsys):
    from r4 import hpc_release

    monkeypatch.setenv(hpc_release.MANIFEST_ENV, "release-requested")
    monkeypatch.setattr(
        site,
        "hpc_gate",
        lambda: {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": hs.gate_sha256()},
        raising=False,
    )
    assert run(["status", SITE, "--live"]) == 1
    assert "release_domain_unknown" in capsys.readouterr().err


def test_status_live_rejects_a_domain_mismatch_with_matching_gate_hash(site, monkeypatch, capsys):
    from r4 import hpc_release

    seen = {}
    hs.update_site_record(SITE, domain="b.example")

    def wrong_domain(**kwargs):
        seen.update(kwargs)
        raise hpc_release.ReleaseError("release_domain_mismatch", "synthetic wrong site")

    monkeypatch.setattr(
        site,
        "hpc_gate",
        lambda: {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": hs.gate_sha256()},
        raising=False,
    )
    monkeypatch.setattr(hpc_release, "verify_environment", wrong_domain)
    assert run(["status", SITE, "--live"]) == 1
    assert seen["domain"] == "b.example"
    assert "release_domain_mismatch" in capsys.readouterr().err


@pytest.mark.parametrize("json_args", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize(
    "head",
    [{"n": 6, "h": "a" * 64}, {"n": 7, "h": "b" * 64}],
    ids=["rollback", "substitution"],
)
def test_status_live_text_and_json_have_the_same_fork_verdict(
    site, monkeypatch, capsys, json_args, head
):
    from r4 import hpc_release

    hs.update_site_record(SITE, domain=DOMAIN, witness={"n": 7, "h": "a" * 64})
    live = {
        "gate_version": hpc_gate.GATE_VERSION,
        "gate_sha256": hs.gate_sha256(),
        "log_head": head,
    }
    monkeypatch.setattr(site, "hpc_gate", lambda: live, raising=False)
    monkeypatch.setattr(hpc_release, "verify_environment", lambda **_kwargs: object())
    assert run(["status", SITE, "--live", *json_args]) == 1
    captured = capsys.readouterr()
    fork = hs.recorded_fork(SITE)
    assert fork and fork["reason"]
    if json_args:
        assert json.loads(captured.out)["witness_fork"]["reason"] == fork["reason"]
    else:
        assert fork["reason"] in captured.err


def _gate_log(remote_home: Path, *details: str) -> None:
    folder = remote_home / ".umrs" / "gate" / "log"
    folder.mkdir(parents=True, exist_ok=True)
    log = hpc_gate.GateLog(str(folder / "gate.jsonl"))
    log.open()
    try:
        for n, detail in enumerate(details):
            verb, _, rest = detail.partition(" ")
            log.append(verb, "ok", rest, 1_700_000_000 + n)
    finally:
        log.close()


def test_status_witness_reads_the_log_over_own_ssh(site, ssh_calls, remote_home, capsys):
    from r4 import hpc_intents

    mine = "a" * 64
    hpc_intents.record_signed(mine, SITE)
    _gate_log(remote_home, "hello ", f"submit intent={mine} step=prep wd=w job=1")
    assert run(["status", SITE, "--witness", "--ssh", HOST]) == 0
    assert ssh_calls == [["ssh", HOST, "sh", "-c", shlex.quote(hpc_remote.LOG_SCRIPT)]]
    assert f"intent {mine}" in capsys.readouterr().out
    _gate_log(remote_home, f"submit intent={'b' * 64} step=fit wd=w job=2")
    assert run(["status", SITE, "--witness", "--ssh", HOST]) == 1
    err = capsys.readouterr().err
    assert f"intent {'b' * 64} was not signed by this r4" in err
