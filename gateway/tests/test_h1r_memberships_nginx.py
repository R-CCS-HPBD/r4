"""H-1 memberships lane (2026-10-18): nginx forwards the memberships HMAC.

Every block that forwards ``X-Verified-Project-Memberships`` must also carry
the sidecar's ``X-Verified-Memberships-HMAC`` (UMRS ignores an unsigned
list), and the static dashboard shell gets an authenticated ``/gw/web/``
block so its API calls reach UMRS with signed identity.
"""

from __future__ import annotations

import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[2] / "gateway" / "nginx" / "conf.d" / "server.conf"

_SET_MEMBERSHIPS = (
    "auth_request_set $auth_verified_project_memberships "
    "$upstream_http_x_verified_project_memberships;"
)
_SET_HMAC = (
    "auth_request_set $auth_verified_memberships_hmac $upstream_http_x_verified_memberships_hmac;"
)
_FWD_MEMBERSHIPS = (
    "proxy_set_header X-Verified-Project-Memberships $auth_verified_project_memberships;"
)
_FWD_HMAC = "proxy_set_header X-Verified-Memberships-HMAC $auth_verified_memberships_hmac;"


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


def _forwarding_blocks() -> dict[str, str]:
    return {name: body for name, body in _location_blocks().items() if _FWD_MEMBERSHIPS in body}


def test_memberships_forwarding_blocks_exist() -> None:
    assert len(_forwarding_blocks()) >= 60


def test_every_memberships_forward_carries_the_memberships_hmac() -> None:
    missing = [
        name
        for name, body in _forwarding_blocks().items()
        if _SET_HMAC not in body or _FWD_HMAC not in body
    ]
    assert not missing, f"blocks forward memberships without the memberships HMAC: {missing}"


def test_memberships_hmac_is_never_forwarded_without_the_list() -> None:
    stray = [
        name
        for name, body in _location_blocks().items()
        if _FWD_HMAC in body and (_FWD_MEMBERSHIPS not in body or _SET_MEMBERSHIPS not in body)
    ]
    assert not stray, f"memberships HMAC forwarded without its list: {stray}"


def test_gw_web_shell_block_authenticates_and_forwards_identity() -> None:
    blocks = _location_blocks()
    assert "/gw/web/" in blocks, "the static dashboard shell needs a /gw/web/ block"
    body = blocks["/gw/web/"]
    assert "auth_request /auth/validate;" in body
    assert "rewrite ^/gw/web/(.*)$ /umrs/web/$1 break;" in body
    assert "proxy_pass http://umrs;" in body
    for header in (
        "proxy_set_header X-User-ID $auth_user_id;",
        "proxy_set_header X-Verified-Project-ID $auth_verified_project_id;",
        "proxy_set_header X-Verified-Is-Admin $auth_verified_is_admin;",
        "proxy_set_header X-Verified-HMAC $auth_verified_hmac;",
        _FWD_MEMBERSHIPS,
        _FWD_HMAC,
    ):
        assert header in body, header


def test_gw_web_dashboard_block_is_kept() -> None:
    blocks = _location_blocks()
    assert (
        "rewrite ^/gw/web/dashboard/(.*)$ /umrs/web/dashboard/$1 break;"
        in blocks["/gw/web/dashboard/"]
    )
