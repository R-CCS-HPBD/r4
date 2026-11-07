"""P9 verb-parity contract (WP-08, G-P9-1/G-P9-4).

The canonical capability registry (r4.capabilities) is the single place a
management capability is declared. These tests prove every claimed transport
actually serves the verb — a future verb lands in the registry first and the
transports follow, never the other way around again.
"""

from __future__ import annotations

from r4.acp import ACP_PLATFORM_METHODS
from r4.capabilities import CAPABILITIES, capability_names, transports_of
from r4.companion.ui.bridge_server import (
    BRIDGE_RPC_NAMES,
    CompanionBridgeServer,
    _bridge_rpc_bindings,
)


class TestRegistryShape:
    def test_every_claimed_transport_is_known(self):
        for cap in CAPABILITIES:
            assert cap.acp is None or isinstance(cap.acp, str)
            assert cap.bridge is None or isinstance(cap.bridge, str)

    def test_names_are_unique_and_family_prefixed(self):
        names = [c.name for c in CAPABILITIES]
        assert len(names) == len(set(names))
        for cap in CAPABILITIES:
            assert cap.name.startswith(f"{cap.family}.")

    def test_every_capability_serves_at_least_one_rail(self):
        for cap in CAPABILITIES:
            assert cap.acp or cap.bridge, f"{cap.name} serves nothing"


class TestRailCoverage:
    def test_every_acp_claim_is_served(self):
        missing = {
            c.name: c.acp for c in CAPABILITIES if c.acp and c.acp not in ACP_PLATFORM_METHODS
        }
        assert not missing, f"ACP rail lacks: {missing}"

    def test_every_bridge_claim_is_served(self):
        missing = {
            c.name: c.bridge for c in CAPABILITIES if c.bridge and c.bridge not in BRIDGE_RPC_NAMES
        }
        assert not missing, f"bridge lacks: {missing}"

    def test_bridge_constant_matches_live_table(self):
        keys = set(_bridge_rpc_bindings(CompanionBridgeServer.__new__(CompanionBridgeServer)))
        assert keys == BRIDGE_RPC_NAMES

    def test_transports_of_reflects_claims(self):
        assert transports_of("jobs.list") == frozenset({"acp", "bridge"})
        assert transports_of("endpoints.stop") == frozenset({"acp"})
        assert transports_of("research.loop_stop") == frozenset({"acp"})
        assert transports_of("catalog.search") == frozenset({"acp"})

    def test_jobs_and_endpoints_on_acp(self):
        # G-P9-4: was CLI/tree-views only before WP-08.
        for verb in ("jobs/list", "jobs/status", "jobs/cancel", "endpoints/list", "endpoints/stop"):
            assert verb in ACP_PLATFORM_METHODS, verb

    def test_capability_count_is_pinned(self):
        # Growth is explicit: land new verbs here + on the rails together.
        # +5 (ADR-092 slice 3): teamplans.templates/run/result/cancel/runs.
        # +1 (2026-11-03): projects.register (bridge + CLI; no ACP, like delete).
        assert len(capability_names()) == 50


class TestCliSpellings:
    """G-P9-1 round 2 (2026-09-18): every declared CLI spelling parses.

    Spellings outside this map (research.* family, workflow describe/detail/
    results, mcp.tools+joint-list, knowledge.detail, catalog.search,
    knowledge.set_acquisition, commands.list) are deliberate Nones per the
    retirement-era and tool-only design notes on the Capability dataclass.
    """

    def test_every_declared_cli_spelling_parses(self):
        import subprocess
        import sys

        failures: dict[str, int] = {}
        for cap in CAPABILITIES:
            if not cap.cli:
                continue
            proc = subprocess.run(
                [sys.executable, "-m", "r4", *cap.cli.split(), "--help"],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                failures[cap.name] = proc.returncode
        assert not failures, f"CLI verbs broken for canonical rows: {failures}"

    def test_cli_map_covers_expected_rows_count(self):
        cli_rows = {c.name for c in CAPABILITIES if c.cli}
        # +3 (ADR-092 slice 3): team plan list/run/result.
        # +1 (2026-11-03): project register.
        assert len(cli_rows) == len(set(cli_rows)) == 32
