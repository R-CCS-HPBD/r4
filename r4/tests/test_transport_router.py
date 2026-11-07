"""Tests for the Plan 4 TransportRouter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from r4._transport_router import TransportRouter, transport_fallback_hint
from r4.config import Config, DeployMode, TransportMode


def _config(**overrides) -> Config:
    base = dict(
        deploy_mode=DeployMode.HYBRID,
        transport_mode=TransportMode.AUTO,
        use_umrs_transport=True,
    )
    base.update(overrides)
    cfg = Config()
    for k, v in base.items():
        setattr(cfg, k, v)
    return cfg


def test_route_http_when_transport_mode_gateway():
    cfg = _config(transport_mode=TransportMode.GATEWAY)
    r = TransportRouter(cfg)
    assert r.route("job_list") == "http"


def test_route_zmq_when_transport_mode_direct_and_eligible():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    assert r.route("job_list") == "zmq"


def test_route_http_for_gateway_only_operations():
    cfg = _config(transport_mode=TransportMode.AUTO, deploy_mode=DeployMode.HPC_LOCAL)
    r = TransportRouter(cfg)
    # chat must never take ZMQ even in HPC_LOCAL
    assert r.route("chat") == "http"


def test_execute_prefers_zmq_when_routed():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="http-result")
    zmq = MagicMock(return_value="zmq-result")
    assert r.execute("job_list", http_fn=http, zmq_fn=zmq) == "zmq-result"
    http.assert_not_called()
    zmq.assert_called_once()


def test_execute_uses_http_when_router_picks_http():
    cfg = _config(transport_mode=TransportMode.GATEWAY)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="http-result")
    zmq = MagicMock(return_value="zmq-result")
    assert r.execute("job_list", http_fn=http, zmq_fn=zmq) == "http-result"
    zmq.assert_not_called()


def test_execute_falls_back_to_http_when_zmq_transport_unavailable():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="http-ok")
    zmq = MagicMock(side_effect=ConnectionError("broker down"))
    assert r.execute("job_list", http_fn=http, zmq_fn=zmq) == "http-ok"
    zmq.assert_called_once()
    http.assert_called_once()


def test_execute_logs_transport_fallback_hint(caplog):
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="http-ok")
    zmq = MagicMock(side_effect=ConnectionError("broker down"))

    assert r.execute("job_list", http_fn=http, zmq_fn=zmq) == "http-ok"

    assert transport_fallback_hint() in caplog.text
    assert "job_list" in caplog.text


def test_execute_propagates_non_transport_zmq_errors():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="http-ok")
    zmq = MagicMock(side_effect=RuntimeError("programmer error"))

    with pytest.raises(RuntimeError, match="programmer error"):
        r.execute("job_list", http_fn=http, zmq_fn=zmq)

    http.assert_not_called()


def test_execute_honours_zmq_unavailable_filter():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    zmq = MagicMock(side_effect=ValueError("programmer error, not transport"))
    http = MagicMock()
    with pytest.raises(ValueError):
        r.execute("job_list", http_fn=http, zmq_fn=zmq, zmq_unavailable=(ConnectionError,))
    http.assert_not_called()


def test_execute_without_zmq_fn_always_goes_http():
    cfg = _config(transport_mode=TransportMode.DIRECT)
    r = TransportRouter(cfg)
    http = MagicMock(return_value="ok")
    assert r.execute("job_list", http_fn=http, zmq_fn=None) == "ok"
    http.assert_called_once()


def test_client_exposes_router_property():
    from r4 import Client

    c = Client(api_key="sk-test", gateway_url="http://gw")
    assert isinstance(c.router, TransportRouter)
    # property returns same instance on second access
    assert c.router is c.router


def test_control_clients_do_not_bypass_transport_router():
    source_root = Path(__file__).resolve().parents[1] / "src" / "r4"
    for relative_path in ("jobs.py", "workflows.py", "models.py"):
        text = (source_root / relative_path).read_text()

        assert "should_use_zmq(" not in text
        assert ".execute(" in text
