"""Tests for `r4 map` (security-boundary connection map)."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from r4._cli.map import (
    MapCommand,
    build_map,
    render_ascii,
    render_dot,
    render_json,
)


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("R4_CONFIG", raising=False)


def _ep(**overrides) -> dict:
    base = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "chat-3b",
        "served_model_name": "chat-3b",
        "status": "ready",
        "backend": "slurm",
        "tier": "system",
        "egress_domains": [],
        "mcp_servers": [],
        "mounts": {},
    }
    base.update(overrides)
    return base


# ---------- graph construction ---------------------------------------------


class TestBuildMap:
    def test_minimum_graph_user_and_gateway(self):
        nodes, edges = build_map(
            user_id="alice",
            project_id="proj",
            gateway_url="http://gw:8080",
            endpoints=[],
        )
        kinds = {n.kind for n in nodes}
        assert "user" in kinds
        assert "gateway" in kinds
        assert "umrs" in kinds  # UMRS node is always part of the base graph
        # safe edges: user -> gateway -> umrs
        assert len(edges) == 2
        assert all(e.safe for e in edges)

    def test_endpoint_offline_is_safe(self):
        nodes, edges = build_map(
            user_id="alice",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep()],
        )
        ep_nodes = [n for n in nodes if n.kind == "endpoint"]
        assert len(ep_nodes) == 1
        assert ep_nodes[0].has_egress is False
        assert ep_nodes[0].has_user_mounts is False
        # all edges safe
        assert all(e.safe for e in edges)

    def test_endpoint_with_egress_domain_creates_unsafe_edge(self):
        nodes, edges = build_map(
            user_id="alice",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(egress_domains=["api.example.com"])],
        )
        external_nodes = [n for n in nodes if n.kind == "external"]
        assert len(external_nodes) == 1
        assert external_nodes[0].label == "external:api.example.com"
        unsafe = [e for e in edges if not e.safe]
        assert len(unsafe) == 1
        assert "egress" in unsafe[0].label

    def test_multiple_egress_domains_yield_multiple_unsafe_edges(self):
        nodes, edges = build_map(
            user_id="alice",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[
                _ep(egress_domains=["a.com", "b.org"]),
            ],
        )
        unsafe = [e for e in edges if not e.safe]
        assert len(unsafe) == 2
        externals = {n.label for n in nodes if n.kind == "external"}
        assert externals == {"external:a.com", "external:b.org"}

    def test_mcp_servers_marked_as_user_mount_warning(self):
        nodes, _ = build_map(
            user_id="alice",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(mcp_servers=["custom-mcp"])],
        )
        ep = [n for n in nodes if n.kind == "endpoint"][0]
        assert ep.has_user_mounts is True

    def test_umrs_node_carries_dataplane_note(self):
        nodes, edges = build_map(user_id="a", project_id="p", gateway_url="http://gw", endpoints=[])
        umrs_edges = [e for e in edges if e.dst.startswith("umrs (")]
        assert len(umrs_edges) == 1
        assert "50110" in (umrs_edges[0].note or "")

    def test_registry_mcp_local_is_safe_external_is_unsafe(self):
        servers = [
            {"name": "platform", "url": "http://localhost:50131", "status": "healthy"},
            {"name": "context7", "url": "https://mcp.context7.com/mcp", "status": "unknown"},
        ]
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[],
            mcp_servers=servers,
        )
        labels = {n.label for n in nodes}
        assert "mcp:platform" in labels
        assert "mcp-ext:context7" in labels
        unsafe = [e for e in edges if not e.safe]
        assert len(unsafe) == 1
        assert unsafe[0].dst == "mcp-ext:context7"

    def test_user_config_mcp_stdio_safe_http_unsafe(self):
        entries = [
            {"name": "fetch", "type": "stdio", "url": "uvx mcp-server-fetch"},
            {"name": "deepwiki", "type": "http", "url": "https://mcp.deepwiki.com/mcp"},
        ]
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[],
            user_mcp_servers=entries,
        )
        labels = {n.label for n in nodes}
        assert "mcp-local:fetch" in labels
        assert "mcp-user:deepwiki" in labels
        unsafe_dsts = {e.dst for e in edges if not e.safe}
        assert unsafe_dsts == {"mcp-user:deepwiki"}

    def test_mcp_section_renders_both_sources(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[],
            mcp_servers=[{"name": "platform", "url": "http://localhost:1", "status": "healthy"}],
            user_mcp_servers=[{"name": "deepwiki", "type": "http", "url": "https://x"}],
        )
        out = render_ascii(nodes, edges)
        assert "MCP servers (vigilance view)" in out
        assert "mcp:platform — registry · healthy" in out
        assert "mcp-user:deepwiki — external" in out
        # actionable guidance for declared-only rows
        assert "NOT agent-visible" in out
        assert "r4-mcp remote-proxy --config" in out
        assert "r4 mcp register <name> --url http://localhost:50800/<name>" in out

    def test_disabled_user_entry_renders_off_without_crossing(self):
        entries = [
            {"name": "fetch", "type": "stdio", "url": "uvx x", "enabled": False},
            {"name": "deepwiki", "type": "http", "url": "https://x", "enabled": True},
        ]
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[],
            user_mcp_servers=entries,
        )
        out = render_ascii(nodes, edges)
        assert "mcp-local:fetch — disabled (off)" in out
        safe_or_not = [e for e in edges if "mcp-local:fetch" in (e.src, e.dst)]
        assert safe_or_not == []
        unsafe_dsts = {e.dst for e in edges if not e.safe}
        assert unsafe_dsts == {"mcp-user:deepwiki"}

    def test_guidance_absent_when_only_registry_rows(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[],
            mcp_servers=[{"name": "platform", "url": "http://localhost:1", "status": "healthy"}],
        )
        out = render_ascii(nodes, edges)
        assert "r4-mcp remote-proxy" not in out

    def test_tier_classification_falls_back_to_user(self):
        nodes, _ = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(tier=None, owner_type=None)],
        )
        ep = [n for n in nodes if n.kind == "endpoint"][0]
        assert ep.tier == "user"


# ---------- renderers -------------------------------------------------------


class TestRenderAscii:
    def test_no_endpoints_message(self):
        nodes, edges = build_map(user_id="a", project_id="p", gateway_url="http://gw", endpoints=[])
        out = render_ascii(nodes, edges)
        assert "no endpoints provisioned" in out
        assert "r4 pool up" in out

    def test_offline_endpoint_marked_safe(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep()],
        )
        out = render_ascii(nodes, edges)
        assert "No unsafe crossings" in out
        assert "EGRESS" not in out  # no warning marker

    def test_egress_endpoint_renders_unsafe_warning_block(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(egress_domains=["api.example.com"])],
        )
        out = render_ascii(nodes, edges)
        assert "⚠ EGRESS" in out
        assert "api.example.com" in out
        assert "Unsafe boundary crossings detected" in out
        assert "data may leave the platform" in out

    def test_mount_warning_for_custom_mcp(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(mcp_servers=["my-mcp"])],
        )
        out = render_ascii(nodes, edges)
        assert "⚠ MOUNT" in out
        assert "my-mcp" in out

    def test_tier_markers_distinct(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[
                _ep(instance_id="ep-sys", model="m1", served_model_name="m1", tier="system"),
                _ep(instance_id="ep-prj", model="m2", served_model_name="m2", tier="project"),
                _ep(instance_id="ep-usr", model="m3", served_model_name="m3", tier="user"),
            ],
        )
        out = render_ascii(nodes, edges)
        assert "[SYS]" in out
        assert "[PROJ]" in out
        assert "[USR]" in out


class TestRenderDot:
    def test_emits_valid_digraph(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep(egress_domains=["api.example.com"])],
        )
        dot = render_dot(nodes, edges)
        assert dot.startswith("digraph r4_map {")
        assert dot.rstrip().endswith("}")
        assert '"external:api.example.com"' in dot
        # Unsafe edges must be styled distinctly
        assert "color=red" in dot
        assert "dashed" in dot

    def test_safe_only_graph_has_no_red_edges(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep()],
        )
        dot = render_dot(nodes, edges)
        # No external/red unsafe edge — only the gray default edge color.
        assert "external:" not in dot
        # Safe-edge color is the dim gray, distinct from "color=red".
        assert "color=red" not in dot


class TestRenderJson:
    def test_json_round_trip_contains_nodes_and_edges(self):
        nodes, edges = build_map(
            user_id="a",
            project_id="p",
            gateway_url="http://gw",
            endpoints=[_ep()],
        )
        payload = json.loads(render_json(nodes, edges))
        assert "nodes" in payload and "edges" in payload
        assert any(n["kind"] == "user" for n in payload["nodes"])


# ---------- command handler ------------------------------------------------


def _ns(**overrides) -> Namespace:
    base = Namespace(
        dot=False,
        as_json=False,
        from_file=None,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestMapCommandHandler:
    def test_handle_uses_client_list_endpoints(self, capsys):
        cmd = MapCommand()
        fake_client = MagicMock()
        fake_client.list_endpoints.return_value = {
            "endpoints": [_ep(egress_domains=["api.x.com"])],
        }
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch(
                "r4.config.get_config",
                return_value=Namespace(
                    umrs_username="alice",
                    project_id="proj",
                    gateway_url="http://gw:8080",
                ),
            ),
        ):
            rc = cmd.handle(_ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "api.x.com" in out
        assert "EGRESS" in out

    def test_handle_from_file_skips_network(self, tmp_path, capsys):
        snap = tmp_path / "snap.json"
        snap.write_text(json.dumps({"endpoints": [_ep()]}))
        cmd = MapCommand()
        with (
            patch("r4.client.Client") as ClientCls,
            patch(
                "r4.config.get_config",
                return_value=Namespace(
                    umrs_username="alice",
                    project_id="proj",
                    gateway_url="http://gw:8080",
                ),
            ),
        ):
            rc = cmd.handle(_ns(from_file=str(snap)))
            ClientCls.assert_not_called()
        out = capsys.readouterr().out
        assert rc == 0
        assert "No unsafe crossings" in out

    def test_handle_dot_output(self, capsys):
        cmd = MapCommand()
        fake_client = MagicMock()
        fake_client.list_endpoints.return_value = {"endpoints": [_ep()]}
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch(
                "r4.config.get_config",
                return_value=Namespace(
                    umrs_username="alice",
                    project_id="proj",
                    gateway_url="http://gw",
                ),
            ),
        ):
            rc = cmd.handle(_ns(dot=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert out.strip().startswith("digraph r4_map {")

    def test_handle_json_output(self, capsys):
        cmd = MapCommand()
        fake_client = MagicMock()
        fake_client.list_endpoints.return_value = {"endpoints": []}
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch(
                "r4.config.get_config",
                return_value=Namespace(
                    umrs_username="alice",
                    project_id="proj",
                    gateway_url="http://gw",
                ),
            ),
        ):
            rc = cmd.handle(_ns(as_json=True))
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert rc == 0
        assert "nodes" in payload and "edges" in payload

    def test_handle_from_file_missing_returns_2(self, capsys, tmp_path):
        cmd = MapCommand()
        with patch(
            "r4.config.get_config",
            return_value=Namespace(
                umrs_username="alice",
                project_id="proj",
                gateway_url="http://gw",
            ),
        ):
            rc = cmd.handle(_ns(from_file=str(tmp_path / "missing.json")))
        assert rc == 2


class TestRegistration:
    def test_map_command_registered(self):
        """MapCommand is accessible as a top-level command."""
        from r4.cli import _build_parser

        parser = _build_parser()
        # Parse --help to list registered subcommands
        help_out = parser.format_help()
        assert "map" in help_out
