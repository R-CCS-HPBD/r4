"""H-1 leftovers (2026-10-18): /gw/ops/connect-info forwards the signed tuple.

Strict UMRS emits the caller's s3cfg from GET /umrs/ops/connect-info only for
a gateway-signed identity tuple. The gateway block forwarded X-User-ID alone,
so a signed-in caller behind the gateway got discovery without the s3cfg.
The block must take the project, admin flag and HMAC from the optional
validator and forward them, and stay optional (open discovery).
"""

from __future__ import annotations

import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[2] / "gateway" / "nginx" / "conf.d" / "server.conf"

_BLOCK = "= /gw/ops/connect-info"

_TUPLE = (
    ("$auth_user_id", "x_user_id", "X-User-ID"),
    ("$auth_verified_project_id", "x_verified_project_id", "X-Verified-Project-ID"),
    ("$auth_verified_is_admin", "x_verified_is_admin", "X-Verified-Is-Admin"),
    ("$auth_verified_hmac", "x_verified_hmac", "X-Verified-HMAC"),
)


def _location_blocks() -> dict[str, str]:
    text = CONF.read_text()
    blocks: dict[str, str] = {}
    for match in re.finditer(r"^[ \t]*location([^{]+)\{", text, re.M):
        depth, end = 1, match.end()
        while depth and end < len(text):
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        blocks[match.group(1).strip()] = text[match.start() : end]
    return blocks


def _connect_info() -> str:
    blocks = _location_blocks()
    assert _BLOCK in blocks, "the /gw/ops/connect-info block is gone"
    return blocks[_BLOCK]


def test_connect_info_stays_optional_auth() -> None:
    body = _connect_info()
    assert "auth_request /auth/validate-optional;" in body
    assert "auth_request /auth/validate;" not in body
    assert "rewrite ^ /umrs/ops/connect-info break;" in body


def test_connect_info_takes_the_signed_tuple_from_the_validator() -> None:
    body = _connect_info()
    missing = [
        var
        for var, upstream, _ in _TUPLE
        if f"auth_request_set {var} $upstream_http_{upstream};" not in body
    ]
    assert not missing, f"connect-info does not capture: {missing}"


def test_connect_info_forwards_the_signed_tuple() -> None:
    body = _connect_info()
    missing = [
        header for var, _, header in _TUPLE if f"proxy_set_header {header} {var};" not in body
    ]
    assert not missing, f"connect-info does not forward: {missing}"


def test_connect_info_forwards_no_unsigned_project_header() -> None:
    """Only the signed tuple: the raw X-Project-ID is not identity here."""
    assert "proxy_set_header X-Project-ID" not in _connect_info()


def test_connect_info_matches_the_ops_block_variables() -> None:
    """Same variable names as the neighbouring /gw/ops/ block."""
    ops = _location_blocks()["/gw/ops/"]
    for var, upstream, header in _TUPLE:
        assert f"auth_request_set {var} $upstream_http_{upstream};" in ops
        assert f"proxy_set_header {header} {var};" in ops
