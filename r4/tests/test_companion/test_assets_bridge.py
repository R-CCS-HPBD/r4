"""Pins for the F4 assets bridge verbs (snapshot + patch rails)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.ui.bridge_server import CompanionBridgeServer


class TestAssetsBridgeHandlers:
    @pytest.fixture
    def server(self):
        srv = CompanionBridgeServer.__new__(CompanionBridgeServer)
        srv._client = MagicMock()
        srv._client.config = MagicMock(profile="default", gateway_url="", project_id="")
        return srv

    def test_snapshot_filters_and_shape(self, server):
        server._session = MagicMock()
        server._session.client.list_assets = MagicMock(
            return_value={
                "rows": [{"kind": "model", "id": "m1"}],
                "snapshot_hash": "sha-aaa",
            }
        )
        out = server.assets_snapshot({"kinds": "model", "project_id": "p1"})
        server._session.client.list_assets.assert_called_once_with(kinds="model", project_id="p1")
        assert out["rows"] == [{"kind": "model", "id": "m1"}]
        assert out["snapshot_hash"] == "sha-aaa"

    def test_snapshot_omits_filters_when_blank(self, server):
        server._session = MagicMock()
        server._session.client.list_assets = MagicMock(return_value={"rows": []})
        out = server.assets_snapshot({})
        server._session.client.list_assets.assert_called_once_with(kinds="", project_id="")
        assert out["rows"] == []
        assert out["snapshot_hash"] is None

    def test_patch_requires_base(self, server):
        with pytest.raises(ValueError, match="base"):
            server.assets_patch({})

    def test_patch_rejects_non_dict_base(self, server):
        with pytest.raises(ValueError, match="base"):
            server.assets_patch({"base": "oops"})

    def test_patch_threads_deltas_and_tombstones(self, server):
        server._session = MagicMock()
        server._session.client.assets_patch = MagicMock(
            return_value={
                "rows": [{"kind": "endpoint", "id": "e2"}],
                "tombstoned": ["model/m1"],
                "snapshot_hash": "sha-bbb",
            }
        )
        base = {"model/m1": "shape-old"}
        out = server.assets_patch({"base": base, "kinds": "endpoint", "project_id": "p2"})
        server._session.client.assets_patch.assert_called_once_with(
            base=base, kinds="endpoint", project_id="p2"
        )
        assert out["rows"] == [{"kind": "endpoint", "id": "e2"}]
        assert out["tombstoned"] == ["model/m1"]
        assert out["snapshot_hash"] == "sha-bbb"

    def test_patch_empty_diff_is_honest(self, server):
        server._session = MagicMock()
        server._session.client.assets_patch = MagicMock(
            return_value={"rows": [], "tombstoned": [], "snapshot_hash": "sha-ccc"}
        )
        out = server.assets_patch({"base": {"m/i": "x"}})
        assert out["rows"] == []
        assert out["tombstoned"] == []
        assert out["snapshot_hash"] == "sha-ccc"
