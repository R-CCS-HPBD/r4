"""``r4 logout`` logs out the named account and every stored copy of it.

Cycle-2 security lane, 2026-10-21.

- ``--profile=alice`` and ``R4_PROFILE=alice`` were ignored (logout sniffed
  ``sys.argv`` for the bare word ``--profile``): the ACTIVE source's account
  was logged out and alice's key kept working.
- ``r4 source add lab --url GW`` then ``r4 auth login --gateway-url GW``
  stores one login under profile ``default`` AND on row ``lab``; either
  logout form left the other copy authenticating.
- The CURVE session was revoked at ``127.0.0.1:8082`` (the manager's default)
  instead of the deployment that registered it, carrying the JWT there; and a
  login kept only on a source row left ``curve-session.json`` valid.

Stab cycle 3 (2026-10-22, integrator decision): three rounds of inferring
where logout may send the credential to revoke a CURVE session each leaked
it to another deployment. Now ``r4 logout`` sends nothing in every form: it
deletes the session files (their client secret key exists only there) and
prints the server-side revoke command with a placeholder token. Only
``--revoke-server-session`` sends, and only on a proven match.

Repros: $C2/security/logout/t_profile_select.sh, $C2/security/creds/t07_dedup_default.sh,
$C2/security/creds/t03_curve_logout.sh.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

GW = "https://gw.example"
ALICE = "alice-login-key-0123456789"
BOB = "bob-login-key-0123456789"
Z85 = "0" * 40
CLIENT_SECRET = "client-secret-key-z85-" + "k" * 18
Y = "https://gw.deployment-y.example"

_ENV = (
    "R4_API_KEY",
    "R4_GATEWAY_URL",
    "R4_PROJECT_ID",
    "R4_PROFILE",
    "R4_SOURCE",
    "UMRS_CONFIG",
    "UMRS_BASE_URL",
)


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    import r4.auth
    import r4.config

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    monkeypatch.setattr("sys.argv", ["r4", "logout"])
    return root


def _login(name: str, key: str, url: str, *, profile: str | None = None) -> None:
    """What `r4 auth login` leaves: the auth-store key and the platform row."""
    from r4.auth import APIKeyManager
    from r4.source import Source, SourceStore, sync_umrs_token

    APIKeyManager().set_api_key(key, profile or name)
    row = Source(name=name, kind="platform", url=url, api_key=key)
    SourceStore().add(row)
    sync_umrs_token(row)


def _logout(argv: list[str]) -> None:
    import r4.config
    from r4._cli.logout import LogoutCommand

    r4.config._config = None
    parser = argparse.ArgumentParser()
    LogoutCommand().register(parser.add_subparsers(dest="cmd"))
    assert LogoutCommand().handle(parser.parse_args(["logout", *argv])) == 0


def _stored(root) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("argv,env", [(["--profile=alice"], None), ([], "alice")])
def test_the_named_profile_is_logged_out_not_the_active_one(xdg, monkeypatch, argv, env):
    from r4.source import SourceStore

    _login("alice", ALICE, GW)
    _login("bob", BOB, "https://other.example")
    SourceStore().set_active("bob")
    if env:
        monkeypatch.setenv("R4_PROFILE", env)
    _logout(argv)
    assert ALICE not in _stored(xdg)
    assert BOB in _stored(xdg)


@pytest.mark.parametrize("argv", [[], ["--profile", "default"], ["--profile", "lab"]])
def test_a_login_copied_to_another_row_is_cleared_everywhere(xdg, argv):
    from r4.auth import AuthManager
    from r4.source import SourceStore

    _login("lab", ALICE, GW, profile="default")  # login deduped onto row `lab`
    SourceStore().set_active("lab")
    _logout(argv)
    assert ALICE not in _stored(xdg)
    assert AuthManager(profile="default").get_auth_header() is None


def _session_file(
    root, name: str = "curve-session.json", *, url: str = GW, subject: str = ""
) -> Path:
    """A CURVE session file; ``subject`` empty = written before 2026-10-21."""
    data = {
        "session_id": "sess-1",
        "server_public_key": Z85,
        "client_public_key": Z85,
        "client_secret_key": CLIENT_SECRET,
        "expires_at": 4102444800.0,
        "umrs_base_url": url,
        "subject": subject,
    }
    path = root / "r4" / name
    path.write_text(json.dumps(data))
    return path


def test_logout_deletes_session_files_when_the_login_is_only_on_the_row(xdg, trap):
    from r4.curve_session import session_file_name_for
    from r4.source import Source, SourceStore

    SourceStore().add(Source(name="lab", kind="platform", url=GW, api_key=ALICE))
    SourceStore().set_active("lab")
    _session_file(xdg)
    _session_file(xdg, session_file_name_for(GW))
    _logout([])
    assert not list((xdg / "r4").glob("curve-session*.json"))
    assert trap == []


@pytest.mark.parametrize("argv", [[], ["--profile", "default"]])
def test_logout_removes_the_legacy_s3cfg_of_the_profile_user(xdg, argv):
    """The R4_WRITE_S3CFG=1 login path writes ``<umrs cfg>/s3cfg_<user>`` (c2-transport #3)."""
    from r4.config import ProfileConfigStore

    _login("default", ALICE, GW)
    ProfileConfigStore().save("default", umrs_username="alice")
    s3cfg = xdg / "umrs" / "s3cfg_alice"
    other = xdg / "umrs" / "s3cfg_bob"
    for path in (s3cfg, other):
        path.write_text("[default]\naccess_key = AK\nsecret_key = S3-SECRET\n")
    _logout(argv)
    assert not s3cfg.exists()
    assert other.exists()  # another account's file is not this logout's


# --------------------------------------------------------------- network trap


@pytest.fixture
def trap(monkeypatch) -> list[tuple[str, str, str]]:
    """Every HTTP path r4 uses, trapped: ``(path, url, credential)`` per attempt.

    httpx (sync/async), requests, urllib, http.client, raw sockets, r4's
    ``HTTPCore`` and the CURVE manager's own stdlib helpers. A trapped call
    is recorded, then fails like an unreachable server.
    """
    import http.client
    import socket
    import urllib.request

    import httpx

    import r4._http
    import r4.curve_session

    calls: list[tuple[str, str, str]] = []

    def blocked(name: str):
        def _call(*args, **kwargs):
            url = next((str(a) for a in args if "://" in str(a)), str(kwargs.get("url", "")))
            calls.append((name, url, str(kwargs.get("jwt_token", ""))))
            raise ConnectionRefusedError(f"network trap: {name}")

        return _call

    targets = [
        (urllib.request, "urlopen"),
        (urllib.request.OpenerDirector, "open"),
        (http.client.HTTPConnection, "connect"),
        (socket.socket, "connect"),
        (socket, "create_connection"),
        (httpx.Client, "send"),
        (httpx.AsyncClient, "send"),
        (r4._http.HTTPCore, "request"),
        (r4.curve_session.CurveSessionManager, "_http_post"),
    ]
    try:
        import requests

        targets.append((requests.Session, "send"))
    except ImportError:  # pragma: no cover - requests ships with the dev env
        pass
    for owner, attr in targets:
        monkeypatch.setattr(owner, attr, blocked(f"{getattr(owner, '__name__', owner)}.{attr}"))
    sender = staticmethod(blocked("CurveSessionManager._http_delete"))
    monkeypatch.setattr(r4.curve_session.CurveSessionManager, "_http_delete", sender)
    return calls


def _jwt(sub: str, exp: float) -> str:
    import base64

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'iss': 'https://kc', 'sub': sub, 'exp': exp})}.sig"


def _umrs_jwt(gateway: str, *, exp: float | None = None) -> str:
    """A JWT shaped like umrs admin ``_build_jwt_payload``: no ``iss``, ``sub`` is
    the username, ``gateway_url`` names the deployment that minted it."""
    import base64

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {"sub": "mnagaso", "roles": ["user"], "gateway_url": gateway}
    claims["exp"] = exp if exp is not None else 4102444800.0
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2ln"


def _bound(root, url: str, credential: str, name: str = "curve-session.json") -> Path:
    """A session file as written since 2026-10-21: it names its identity."""
    from r4.curve_session import session_subject

    return _session_file(root, name, url=url, subject=session_subject(credential))


def _record(profile: str, *, key: str = ALICE, **recorded: str) -> None:
    from r4.auth import AuthManager
    from r4.config import ProfileConfigStore

    AuthManager(profile=profile).save_api_key(key)
    ProfileConfigStore().save(profile, **recorded)


def _bind(profile: str, key: str = ALICE) -> None:
    """What ``r4 auth login --gateway-url URL`` adds since round 5: the record
    is bound to the one credential that login stored."""
    from r4.auth import LOGIN_BINDING, credential_digest
    from r4.config import ProfileConfigStore

    ProfileConfigStore().save(profile, **{LOGIN_BINDING: credential_digest(key)})


def _save_token(profile: str, token: str, expires_at: float | None = None) -> None:
    from r4.auth import AuthManager, TokenInfo

    AuthManager(profile=profile).save_token(TokenInfo(token, expires_at=expires_at))


# Each scenario earlier rounds covered: (profile, file kept?, secrets, setup).
# ``setup(root, monkeypatch)`` returns the session file under test.


def _recorded_match_bound(root, mp):
    _record("alice", gateway_url=GW, login_gateway_url=GW)
    _bind("alice")
    return _bound(root, GW, ALICE)


def _recorded_match_pre_subject(root, mp):
    _record("alice", login_gateway_url=GW)
    return _session_file(root)


def _recorded_mismatch_other_login(root, mp):
    _login("alice", ALICE, GW)
    _login("bob", BOB, Y)
    return _bound(root, Y, BOB)


def _env_elsewhere(envvar: str):
    def setup(root, mp):
        _record("alice", gateway_url=GW, login_gateway_url=GW)
        mp.setenv(envvar, Y)
        return _session_file(root, url=Y)

    return setup


def _env_set_own_session(root, mp):
    _record("alice", gateway_url=GW, login_gateway_url=GW)
    _bind("alice")
    mp.setenv("R4_GATEWAY_URL", Y)
    mp.setenv("UMRS_BASE_URL", Y)
    return _bound(root, GW, ALICE)


def _env_only(bound: bool):
    def setup(root, mp):
        mp.setenv("R4_GATEWAY_URL", GW)
        mp.setenv("UMRS_BASE_URL", GW)
        mp.setenv("R4_API_KEY", ALICE)
        return _bound(root, GW, ALICE) if bound else _session_file(root)

    return setup


def _env_key_beside_recorded_login(root, mp):
    _record("alice", gateway_url=GW, login_gateway_url=GW)
    mp.setenv("R4_GATEWAY_URL", Y)
    mp.setenv("R4_API_KEY", BOB)
    return _session_file(root)


def _expired_jwt(root, mp):
    import time

    expired = _jwt("alice", time.time() - 600)
    _save_token("alice", expired, time.time() - 600)
    _record("alice", gateway_url=GW)
    return _bound(root, GW, expired)


def _two_credentials_two_deployments(root, mp):
    """`r4 login --api-key` at Y keeps x's OIDC token from GW (one username)."""
    _record("x", key=_umrs_jwt(Y), login_gateway_url=Y)
    _save_token("x", _umrs_jwt(GW))
    return _bound(root, Y, _umrs_jwt(Y))


def _subject_collision(root, mp):
    """A UMRS JWT has no ``iss``: one username on X and Y is one subject."""
    _record("x", key=_umrs_jwt(GW), login_gateway_url=GW)
    _record("y", key=_umrs_jwt(Y), login_gateway_url=Y)
    return _bound(root, Y, _umrs_jwt(Y))


def _no_deployment_recorded(root, mp):
    _record("alice", gateway_url=GW, login_gateway_url=GW)
    mp.setenv("UMRS_BASE_URL", Y)
    return _bound(root, "", ALICE)


def _profile_gateway_beside_home(root, mp):
    _record("alice", gateway_url=Y, login_gateway_url=GW)
    mp.setenv("R4_GATEWAY_URL", Y)
    return _bound(root, Y, ALICE)


def _bound_file_at_profile_gateway(root, mp):
    from r4.curve_session import session_file_name_for

    _login("alice", ALICE, GW)
    return _bound(root, GW, ALICE, session_file_name_for(GW))


#: (id, profile, kept, setup, proven): ``proven`` = the one scenario kind where
#: ``--revoke-server-session`` may send (the login that named the session's
#: deployment stored this very key: :func:`_bind`).
SCENARIOS = [
    ("recorded-match-bound", "alice", False, _recorded_match_bound, True),
    ("recorded-match-pre-subject", "alice", False, _recorded_match_pre_subject, False),
    ("recorded-mismatch-other-login", "alice", True, _recorded_mismatch_other_login, False),
    ("env-R4_GATEWAY_URL-elsewhere", "alice", True, _env_elsewhere("R4_GATEWAY_URL"), False),
    ("env-UMRS_BASE_URL-elsewhere", "alice", True, _env_elsewhere("UMRS_BASE_URL"), False),
    ("env-set-own-session", "alice", False, _env_set_own_session, True),
    ("env-only-pre-subject", "default", False, _env_only(False), False),
    ("env-only-bound", "default", False, _env_only(True), False),
    ("env-key-beside-recorded-login", "alice", False, _env_key_beside_recorded_login, False),
    ("expired-jwt", "alice", False, _expired_jwt, False),
    ("two-credentials-two-deployments", "x", False, _two_credentials_two_deployments, False),
    ("subject-collision", "x", True, _subject_collision, False),
    ("no-deployment-recorded", "alice", False, _no_deployment_recorded, False),
    ("profile-gateway-beside-home", "alice", False, _profile_gateway_beside_home, False),
    ("bound-file-at-profile-gateway", "alice", False, _bound_file_at_profile_gateway, False),
]

_SECRETS = (ALICE, BOB, CLIENT_SECRET, _umrs_jwt(GW), _umrs_jwt(Y))


def _assert_no_secret(capsys) -> str:
    out = "".join(capsys.readouterr())
    assert not [secret for secret in _SECRETS if secret in out]
    return out


@pytest.mark.parametrize(
    "profile,kept,setup", [s[1:4] for s in SCENARIOS], ids=[s[0] for s in SCENARIOS]
)
def test_logout_makes_no_network_request(xdg, monkeypatch, capsys, trap, profile, kept, setup):
    """Stab cycle 3 (2026-10-22): rounds 1-4 each found another way logout's
    inferred target sent a credential to another deployment. Every scenario
    they covered: nothing leaves the machine; this login's files go, another
    login's stay."""
    path = setup(xdg, monkeypatch)
    _logout(["--profile", profile])
    assert trap == []
    assert path.exists() is kept
    _assert_no_secret(capsys)


@pytest.mark.parametrize(
    "profile,kept,setup,proven", [s[1:] for s in SCENARIOS], ids=[s[0] for s in SCENARIOS]
)
def test_revoke_server_session_sends_only_on_a_proven_match(
    xdg, monkeypatch, capsys, trap, profile, kept, setup, proven
):
    path = setup(xdg, monkeypatch)
    _logout(["--profile", profile, "--revoke-server-session"])
    expected = [("CurveSessionManager._http_delete", f"{GW}/umrs/zmq/session/sess-1", ALICE)]
    assert trap == (expected if proven else [])
    assert path.exists() is kept
    _assert_no_secret(capsys)


def test_the_printed_revoke_command_carries_no_secret(xdg, monkeypatch, capsys, trap):
    _recorded_match_bound(xdg, monkeypatch)
    _logout(["--profile", "alice"])
    out = _assert_no_secret(capsys)
    command = f'curl -fsS -X DELETE -H "Authorization: Bearer <TOKEN>" {GW}/umrs/zmq/session/sess-1'
    assert command in out
    assert "expires on its own TTL" in out
    assert trap == []


def test_a_session_recording_no_deployment_prints_no_command(xdg, monkeypatch, capsys, trap):
    _no_deployment_recorded(xdg, monkeypatch)
    _logout(["--profile", "alice"])
    out = _assert_no_secret(capsys)
    assert "expires on its own TTL" in out
    assert "curl" not in out
    assert trap == []


def test_another_logins_kept_session_is_not_reported(xdg, monkeypatch, capsys, trap):
    _subject_collision(xdg, monkeypatch)
    _logout(["--profile", "x"])
    assert "sess-1" not in _assert_no_secret(capsys)


@pytest.fixture
def spy(monkeypatch, trap) -> list[tuple[str, str]]:
    """The trap, but ``_http_delete`` answers like a server that revoked it."""
    from r4.curve_session import CurveSessionManager

    sent: list[tuple[str, str]] = []
    sender = staticmethod(lambda url, jwt_token="", **_: sent.append((url, jwt_token)))
    monkeypatch.setattr(CurveSessionManager, "_http_delete", sender)
    return sent


def _umrs_jwt_only(root, mp):
    _record("x", key=_umrs_jwt(GW))  # the claim alone proves GW
    return _bound(root, GW, _umrs_jwt(GW)), _umrs_jwt(GW)


def _sole_key_recorded(root, mp):
    _login("alice", ALICE, GW)  # the row holds the same key: the bound one
    _record("alice", login_gateway_url=GW)
    _bind("alice")
    return _bound(root, GW, ALICE), ALICE


@pytest.mark.parametrize("setup", [_umrs_jwt_only, _sole_key_recorded])
def test_revoke_server_session_revokes_its_own_session(xdg, monkeypatch, capsys, spy, setup):
    path, credential = setup(xdg, monkeypatch)
    profile = "x" if setup is _umrs_jwt_only else "alice"
    _logout(["--profile", profile, "--revoke-server-session"])
    assert spy == [(f"{GW}/umrs/zmq/session/sess-1", credential)]
    assert not path.exists()
    assert f"revoked at {GW}" in capsys.readouterr().out


def _other_identity_bound_file(root, mp):
    from r4.curve_session import session_file_name_for

    _record("alice", gateway_url=GW, login_gateway_url=GW)
    return _bound(root, GW, BOB, session_file_name_for(GW))


def _two_plain_credentials(root, mp):
    _record("alice", login_gateway_url=GW)
    _save_token("alice", "alice-oidc-token-0123456789")
    return _bound(root, GW, "alice-oidc-token-0123456789")


def _unrecorded_plain_key(root, mp):
    _record("alice", gateway_url=GW)
    mp.setenv("UMRS_BASE_URL", GW)
    return _bound(root, GW, ALICE)


def _claim_beats_record(root, mp):
    _record("alice", key=_umrs_jwt(Y), login_gateway_url=GW)
    return _bound(root, GW, _umrs_jwt(Y))


def _expired_token_only(root, mp):
    import time

    expired = _umrs_jwt(GW, exp=time.time() - 600)
    _save_token("alice", expired, time.time() - 600)
    from r4.config import ProfileConfigStore

    ProfileConfigStore().save("alice", login_gateway_url=GW)
    return _bound(root, GW, expired)


REFUSALS = [
    ("pre-subject", "alice", _recorded_match_pre_subject, "records no subject"),
    ("no-deployment", "alice", _no_deployment_recorded, "records no deployment"),
    ("other-identity", "alice", _other_identity_bound_file, "another identity"),
    ("claim-mismatch", "x", _two_credentials_two_deployments, f"not proven to belong to {Y}"),
    ("claim-beats-record", "alice", _claim_beats_record, f"not proven to belong to {GW}"),
    ("two-credentials", "alice", _two_plain_credentials, f"not proven to belong to {GW}"),
    ("env-urls-only", "alice", _unrecorded_plain_key, f"not proven to belong to {GW}"),
    ("env-key-only", "default", _env_only(True), "no usable credential"),
    ("expired-beside-a-key", "alice", _expired_jwt, "another identity"),
    ("expired-token-only", "alice", _expired_token_only, "no usable credential"),
]


@pytest.mark.parametrize(
    "profile,setup,reason", [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS]
)
def test_revoke_server_session_refuses_on_any_doubt(
    xdg, monkeypatch, capsys, spy, profile, setup, reason
):
    path = setup(xdg, monkeypatch)
    _logout(["--profile", profile, "--revoke-server-session"])
    assert spy == []
    assert not path.exists()  # the local delete stands
    out = _assert_no_secret(capsys)
    assert "Not revoking CURVE session sess-1 on the server: " in out
    assert reason in out
    if setup is not _no_deployment_recorded:  # and the command to do it by hand
        assert 'Bearer <TOKEN>" https://' in out
        assert "/umrs/zmq/session/sess-1" in out


def test_auth_logout_takes_the_revoke_flag():
    from r4._cli.auth import AuthCommand

    parser = argparse.ArgumentParser()
    AuthCommand().register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["auth", "logout", "--revoke-server-session"])
    assert args.revoke_server_session is True


def test_a_failed_revoke_still_deletes_and_reports(xdg, monkeypatch, capsys, trap):
    """The proven match is sent once; an unreachable server keeps the local delete."""
    path = _recorded_match_bound(xdg, monkeypatch)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert [call[1] for call in trap] == [f"{GW}/umrs/zmq/session/sess-1"]
    assert not path.exists()
    out = _assert_no_secret(capsys)
    assert "Could not revoke CURVE session sess-1 on the server" in out
    assert 'Bearer <TOKEN>" ' in out


# ------------------------------- round 5: a record proves only its own credential
#
# Verifier, 2026-10-22: a claimless credential counted as proven by the
# profile's ``login_gateway_url`` whenever it was the profile's only stored
# credential. Logout keeps that record, and ``r4 source add`` / ``r4 init``
# store a new credential beside it: Y's key went to the stale X. A UMRS JWT
# has no ``iss``, so the subject check did not stop it (one username = one
# subject on every deployment). Repros: $C3/fixverify/r4-r4/bin/s1_realflow.sh,
# s2b_init.sh, s4.py.


def _claimless(tag: str, sub: str = "alice") -> str:
    """A JWT with no ``gateway_url`` claim and no ``iss`` (UMRS-shaped)."""
    import base64

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {"sub": sub, "tag": tag, "exp": 4102444800.0}
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2ln"


JX, JY = _claimless("X"), _claimless("Y")


def _stale_record_row_jwt(root, mp):
    """Logout kept ``login_gateway_url=GW``; `r4 source add alice --url Y` stored JY."""
    from r4.config import ProfileConfigStore
    from r4.source import Source, SourceStore

    ProfileConfigStore().save("alice", login_gateway_url=GW)
    SourceStore().add(Source(name="alice", kind="platform", url=Y, api_key=JY))
    return _bound(root, GW, JX)  # the same username's session at GW


def _stale_record_row_key(root, mp):
    from r4.config import ProfileConfigStore
    from r4.source import Source, SourceStore

    ProfileConfigStore().save("alice", login_gateway_url=GW)
    SourceStore().add(Source(name="alice", kind="platform", url=Y, api_key=ALICE))
    return _bound(root, GW, ALICE)


def _stale_record_beside_init_key(root, mp):
    """`r4 init --gateway-url Y --api-key JY` after a logout at GW."""
    _record("alice", key=JY, gateway_url=Y, login_gateway_url=GW)
    return _bound(root, GW, JX)


def _binding_of_another_key(root, mp):
    """The record is bound, but to the key the login stored then, not this one."""
    _record("alice", key=JY, gateway_url=Y, login_gateway_url=GW)
    _bind("alice", JX)
    return _bound(root, GW, JX)


STALE = [
    ("row-jwt", _stale_record_row_jwt),
    ("row-key", _stale_record_row_key),
    ("init-key", _stale_record_beside_init_key),
    ("binding-of-another-key", _binding_of_another_key),
]


@pytest.mark.parametrize("setup", [s[1] for s in STALE], ids=[s[0] for s in STALE])
def test_a_stale_login_record_proves_nothing(xdg, monkeypatch, capsys, spy, setup):
    path = setup(xdg, monkeypatch)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []
    assert not path.exists()
    out = _assert_no_secret(capsys)
    assert JY not in out and JX not in out
    assert f"not proven to belong to {GW}" in out


def _source_add(monkeypatch, name: str, url: str, key: str) -> None:
    import io

    from r4._cli.source import SourceCommand

    parser = argparse.ArgumentParser(prog="r4")
    SourceCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(key))
    args = parser.parse_args(["source", "add", name, "--url", url, "--api-key-stdin"])
    assert SourceCommand().handle(args) == 0


def _auth_login(monkeypatch, *argv: str, key: str = "", verdict=None, rc: int = 0) -> None:
    """The real `r4 auth login` (piped key, no prompts), its network legs
    stubbed; ``verdict`` is what the gateway's identity echo answers."""
    import io

    import r4.config
    from r4._cli import login
    from r4._cli.auth import AuthCommand

    for name in ("_finish_login", "_auto_populate_umrs_username"):
        monkeypatch.setattr(login, name, lambda *a, **k: None)
    monkeypatch.setattr(login, "_verify_identity_via_whoami", lambda *a, **k: verdict)
    parser = argparse.ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(key))
    monkeypatch.setattr(r4.config, "_config", None)
    args = parser.parse_args(["auth", "login", *argv])
    assert login.LoginCommand().handle(args) == rc


def test_the_real_flow_login_logout_source_add_sends_nothing(xdg, monkeypatch, capsys, spy):
    """s1_realflow.sh: login at GW, logout, `source add` at Y, then the flag."""
    _auth_login(monkeypatch, "--profile", "alice", "--gateway-url", GW, "--api-key-stdin", key=JX)
    _logout(["--profile", "alice"])
    _source_add(monkeypatch, "alice", Y, JY)
    path = _bound(xdg, GW, _claimless("X2"))  # the same username's session at GW
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []
    assert not path.exists()
    assert f"not proven to belong to {GW}" in _assert_no_secret(capsys)


def test_a_real_login_naming_its_gateway_proves_its_own_key(xdg, monkeypatch, capsys, spy):
    _auth_login(monkeypatch, "--profile", "alice", "--gateway-url", GW, "--api-key-stdin", key=JX)
    path = _bound(xdg, GW, JX)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == [(f"{GW}/umrs/zmq/session/sess-1", JX)]
    assert not path.exists()


def test_a_login_naming_no_gateway_binds_nothing(xdg, monkeypatch, capsys, spy):
    """An inferred home (here the active source) is not proof for a claimless key."""
    from r4.auth import LOGIN_BINDING
    from r4.config import ProfileConfigStore
    from r4.source import Source, SourceStore

    SourceStore().add(Source(name="lab", kind="platform", url=GW))
    SourceStore().set_active("lab")
    _auth_login(monkeypatch, "--profile", "alice", "--api-key-stdin", key=JX)
    assert ProfileConfigStore().load("alice").get("login_gateway_url") == GW
    assert LOGIN_BINDING not in ProfileConfigStore().load("alice")
    _bound(xdg, GW, JX)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []


def test_logout_drops_the_binding_and_a_new_login_rebinds(xdg, monkeypatch, capsys, spy):
    from r4.auth import LOGIN_BINDING, credential_digest
    from r4.config import ProfileConfigStore

    _auth_login(monkeypatch, "--profile", "alice", "--gateway-url", GW, "--api-key-stdin", key=JX)
    assert ProfileConfigStore().load("alice")[LOGIN_BINDING] == credential_digest(JX)
    _logout(["--profile", "alice"])
    assert LOGIN_BINDING not in ProfileConfigStore().load("alice")
    _auth_login(monkeypatch, "--profile", "alice", "--gateway-url", Y, "--api-key-stdin", key=JY)
    assert ProfileConfigStore().load("alice")[LOGIN_BINDING] == credential_digest(JY)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:18302\\@127.0.0.1:18301",
        "https://user@gw.example",
        "ftp://gw.example",
    ],
)
def test_revoke_refuses_a_session_url_parsers_disagree_on(xdg, monkeypatch, capsys, spy, url):
    from r4.config import ProfileConfigStore

    _record("alice", login_gateway_url=url)
    _bind("alice")
    _bound(xdg, url, ALICE)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []
    assert "not a plain http(s) URL" in _assert_no_secret(capsys)
    assert ProfileConfigStore().load("alice").get("api_key") is None


@pytest.mark.parametrize(
    "home,url",
    [
        ("http://127.0.0.1:18301", "http://localhost:18301"),
        ("http://localhost:18301", "http://127.0.0.1:18301"),
        ("http://127.1:18301", "http://127.0.0.1:18301"),
    ],
)
def test_revoke_refuses_another_spelling_of_the_login_host(
    xdg, monkeypatch, capsys, spy, home, url
):
    # `localhost` resolves to ::1 first: a key proven for 127.0.0.1:P went
    # to whatever listened on [::1]:P (2026-10-22 review).
    _record("alice", login_gateway_url=home)
    _bind("alice")
    _bound(xdg, url, ALICE)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []
    assert f"not proven to belong to {url}" in _assert_no_secret(capsys)


def test_revoke_quotes_the_session_id(xdg, monkeypatch, capsys, spy):
    _recorded_match_bound(xdg, monkeypatch)
    path = xdg / "r4" / "curve-session.json"
    data = json.loads(path.read_text())
    data["session_id"] = "../../admin/x?y=1"
    path.write_text(json.dumps(data))
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == [(f"{GW}/umrs/zmq/session/..%2F..%2Fadmin%2Fx%3Fy%3D1", ALICE)]


def test_logout_deletes_the_target_of_a_symlinked_session_file(xdg, monkeypatch, trap):
    _recorded_match_bound(xdg, monkeypatch)
    link = xdg / "r4" / "curve-session.json"
    target = xdg / "elsewhere.json"
    link.rename(target)
    link.symlink_to(target)
    _logout(["--profile", "alice"])
    assert not link.is_symlink() and not link.exists()
    assert not target.exists()  # it held the client secret key
    assert trap == []


# Stab cycle 3, round 6 (verifier F1/F8, $C3/fixverify/r4-r4/round6/bin/flows1.py,
# flows2.py): a login its gateway answered 401 still bound its key to that
# gateway, so --revoke-server-session later sent it there; and a gateway URL
# given with userinfo printed its password in the revoke command.


def test_a_login_the_gateway_rejected_binds_nothing(xdg, monkeypatch, capsys, spy):
    from r4._cli.login import REJECTED
    from r4.auth import LOGIN_BINDING
    from r4.config import ProfileConfigStore

    argv = ("--profile", "alice", "--gateway-url", GW, "--api-key-stdin")
    _auth_login(monkeypatch, *argv, key=JY, verdict=REJECTED, rc=1)
    stored = ProfileConfigStore().load("alice")
    assert stored.get("api_key") == JY  # still saved: offline login is by design
    assert LOGIN_BINDING not in stored
    path = _bound(xdg, GW, JY)
    _logout(["--profile", "alice", "--revoke-server-session"])
    assert spy == []
    assert not path.exists()
    assert f"not proven to belong to {GW}" in _assert_no_secret(capsys)


@pytest.mark.parametrize("flag", [[], ["--revoke-server-session"]])
def test_the_printed_revoke_command_masks_a_url_password(xdg, monkeypatch, capsys, spy, flag):
    url = "https://u:PW-in-url-secret@gw.example"
    _record("alice", gateway_url=url, login_gateway_url=url)
    _bind("alice")
    path = _bound(xdg, url, ALICE)
    _logout(["--profile", "alice", *flag])
    out = _assert_no_secret(capsys)
    assert spy == []
    assert not path.exists()
    assert "PW-in-url-secret" not in out
    assert "https://***@gw.example/umrs/zmq/session/sess-1" in out


@pytest.mark.parametrize(
    "url,shown",
    [
        ("https://gw.example:8443/x", "https://gw.example:8443/x"),
        ("http://u:pw@h:1", "http://***@h:1"),
        ("http://a@b@h", "http://***@h"),
        ("http://[::1", "<unparseable URL>"),
        ("", ""),
    ],
)
def test_display_url_masks_userinfo(url, shown):
    from r4.curve_session import display_url

    assert display_url(url) == shown
