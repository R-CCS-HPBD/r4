"""Tests for the authz canary probe runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.authz_canary_probe import (
    ENV_IDENTITY,
    ProbeResult,
    _cross_scope_verdict,
    _substitute,
    _verdict,
    load_canaries,
    main,
    run_probes,
    validate_canary_data,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANARIES = REPO_ROOT / "tools" / "authz_canaries.yaml"


def _probe(**overrides: object) -> dict:
    row: dict[str, object] = {
        "id": "x",
        "path": "/gw/x",
        "expect_denied": True,
    }
    row.update(overrides)
    return row


class TestFileValidation:
    def test_default_canaries_validate(self) -> None:
        data, errors = load_canaries(DEFAULT_CANARIES)
        assert errors == []
        assert data is not None
        assert validate_canary_data(data) == []
        assert len(data["probes"]) >= 10

    def test_missing_fields_fail(self) -> None:
        findings = validate_canary_data({"probes": [{"id": "x"}]})
        assert any(f.code == "probe-fields" for f in findings)

    def test_duplicate_ids_fail(self) -> None:
        findings = validate_canary_data({"probes": [_probe(), _probe(path="/gw/y")]})
        assert any(f.code == "probe-id" for f in findings)

    def test_non_gw_path_still_allowed_but_must_start_slash(self) -> None:
        assert validate_canary_data({"probes": [_probe(path="rel")]})
        assert not validate_canary_data({"probes": [_probe(path="/ok")]})

    def test_only_expect_denied_supported(self) -> None:
        findings = validate_canary_data({"probes": [_probe(expect_denied=False)]})
        assert any(f.code == "probe-expect" for f in findings)


class TestVerdictLogic:
    def test_deny_statuses_pass(self) -> None:
        assert _verdict(401, b"")[0] == "pass"
        assert _verdict(403, b"")[0] == "pass"

    def test_503_denied_by_unavailability(self) -> None:
        assert _verdict(503, b"")[0] == "pass"

    def test_2xx_with_payload_fails(self) -> None:
        verdict, detail = _verdict(200, b'{"data": 1}')
        assert verdict == "fail"
        assert "leak" in detail

    def test_2xx_empty_body_warns(self) -> None:
        assert _verdict(200, b"")[0] == "warn"

    def test_transport_error_warns(self) -> None:
        assert _verdict(None, b"Connection refused")[0] == "warn"

    def test_unexpected_4xx_warns(self) -> None:
        assert _verdict(404, b"")[0] == "warn"


class TestLiveProbeRunner:
    def test_run_probes_maps_status_to_results(self) -> None:
        probes = [
            _probe(id="a", path="/gw/a"),
            _probe(id="b", path="/gw/b"),
            _probe(id="c", path="/gw/c"),
        ]

        with patch("tools.authz_canary_probe._fire") as fire:
            fire.side_effect = [(401, b""), (200, b'{"x": 1}'), (None, b"timeout")]
            results = run_probes("https://gw.test", probes, timeout=1)

        assert [r.verdict for r in results] == ["pass", "fail", "warn"]
        assert results[1].status == 200

    def test_gateway_url_normalization(self) -> None:
        with patch("tools.authz_canary_probe._fire") as fire:
            fire.return_value = (401, b"")
            run_probes("https://gw.test/", [_probe()], timeout=1)
        assert fire.call_args[0][0] == "https://gw.test/gw/x"


class TestCrossScopeProbes:
    def test_default_file_has_cross_scope_probes(self) -> None:
        data, errors = load_canaries(DEFAULT_CANARIES)
        assert errors == []
        cross = [p for p in data["probes"] if p.get("authenticated")]
        assert len(cross) >= 3

    def test_missing_identity_env_skips_probe(self) -> None:
        probes = [
            {
                "id": "cross",
                "path": "/gw/assets",
                "expect_denied": True,
                "authenticated": True,
                "forbidden_projects": "p2",
            }
        ]
        with patch.dict("os.environ", {}, clear=True):
            results = run_probes("https://gw.test", probes, timeout=1)
        assert results[0].verdict == "skip"

    def test_authenticated_cross_scope_2xx_payload_fails(self) -> None:
        env = dict.fromkeys(ENV_IDENTITY) | {
            "CANARY_IDENTITY_USER": "u",
            "CANARY_IDENTITY_PROJECT": "a",
            "CANARY_IDENTITY_TOKEN": "t",
        }
        probes = [
            {
                "id": "cross",
                "path": "/gw/assets",
                "expect_denied": True,
                "authenticated": True,
                "forbidden_projects": "p2",
            }
        ]
        with (
            patch.dict("os.environ", env, clear=True),
            patch("tools.authz_canary_probe._fire", return_value=(200, b'{"rows":[{}]}')),
        ):
            results = run_probes("https://gw.test", probes, timeout=1)
        assert results[0].verdict == "fail"

    def test_authenticated_cross_scope_denied_passes(self) -> None:
        for status in (401, 403, 404):
            assert _cross_scope_verdict(status, b"", {})[0] == "pass"

    def test_substitute_expands_env(self) -> None:
        with patch.dict("os.environ", {"CANARY_PROJECT_B": "p2"}):
            assert _substitute("/gw/x?id=${CANARY_PROJECT_B}") == "/gw/x?id=p2"


class TestMain:
    def test_offline_default_file_returns_0(self, capsys) -> None:
        assert main([]) == 0
        assert "canaries valid" in capsys.readouterr().out

    def test_bad_file_returns_2(self, tmp_path, capsys) -> None:
        _ = capsys  # fixture named for pytest
        assert main(["--canaries", str(tmp_path / "nope.yaml")]) == 2

    def test_live_fail_returns_1(self, capsys) -> None:
        _ = capsys  # fixture named for pytest
        with patch("tools.authz_canary_probe._fire", return_value=(200, b'{"leak": 1}')):
            assert main(["--gateway", "https://gw.test"]) == 1

    def test_live_all_denied_returns_0(self, capsys) -> None:
        with patch("tools.authz_canary_probe._fire", return_value=(401, b"")):
            assert main(["--gateway", "https://gw.test"]) == 0
        assert "OK:" in capsys.readouterr().out


def test_probe_result_format_stable() -> None:
    r = ProbeResult(id="x", path="/gw/x", status=401, verdict="pass", detail="denied")
    assert "[pass]" in r.format() and "401" in r.format()
