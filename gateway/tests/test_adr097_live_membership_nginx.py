"""ADR-097: nginx hands the client's selected project to the auth sidecar,
and an auth refusal reaches the client with its reason.

Behaviour was confirmed on the gateway's base image (nginx 1.28.1,
2026-10-22): a variable captured by ``auth_request_set`` survives the
``error_page`` jump to a named location, and a non-401/403 answer from the
auth subrequest is nginx's own 500.
"""

from __future__ import annotations

import re
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[2] / "gateway" / "nginx" / "conf.d" / "server.conf"


def _config() -> str:
    return _SERVER.read_text()


def _location_body(config: str, header: str) -> str:
    start = config.index(header)
    return config[start : config.index("\n    }", start)]


def test_both_auth_checks_forward_the_selected_project():
    config = _config()
    for header in ("location = /auth/validate {", "location = /auth/validate-optional {"):
        body = _location_body(config, header)
        assert "proxy_set_header X-Requested-Project-ID $http_x_project_id;" in body


def test_every_auth_lane_keeps_the_refusal_reason():
    config = _config()
    lanes = re.findall(
        r"^(\s*)auth_request /auth/validate(?:-optional)?;\n\1(.*)$", config, flags=re.M
    )
    assert len(lanes) >= 60
    assert all(
        line == "auth_request_set $auth_error $upstream_http_x_auth_error;" for _, line in lanes
    )


def test_a_refused_auth_check_answers_json_with_its_reason():
    config = _config()
    assert "error_page 403 = @error403;" in config
    refusal = _location_body(config, "location @error403 {")
    assert "default_type application/json;" in refusal
    assert '"code": "$auth_error"' in refusal


def test_an_undecided_auth_check_is_a_json_503_not_a_bare_500():
    config = _config()
    assert "error_page 500 502 503 504 = @error5xx;" in config
    assert '"code": "$auth_error"' in _location_body(config, "location @error5xx {")
