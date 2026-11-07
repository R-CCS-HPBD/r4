"""Tests for r4 data intake board + dashboard client (U3)."""

from __future__ import annotations

import pytest

from r4._cli.data import (
    _render_catalog_read,
    _render_catalog_search,
    _render_catalog_stats,
    _render_intake_board,
)
from r4.dashboard import DashboardClient


def _overview():
    return {
        "total_jobs": 42,
        "running_jobs": 3,
        "failed_jobs": 1,
        "system_health": "healthy",
        "workflows": {"total": 7, "running": 2, "failed": 0},
        "connectors": {
            "available_types": ["github", "gdrive"],
            "unavailable": ["federated"],
            "sync_state": None,
            "sync": {
                "github": {
                    "last_sync_at": "2026-08-01T05:00:00+00:00",
                    "status": "ok",
                    "docs_synced": 120,
                    "errors": 1,
                    "dlq": 2,
                },
            },
            "details": {
                "github": {"name": "github", "class": "GitHubConnector", "available": True},
                "gdrive": {"name": "gdrive", "class": "GoogleDriveConnector", "available": True},
                "federated": {
                    "name": "federated",
                    "class": "FederatedConnector",
                    "available": False,
                    "error": "import: ModuleNotFoundError: x",
                },
            },
        },
    }


def test_render_intake_board_shows_partition_and_sync_na(capsys):
    _render_intake_board(_overview())
    out = capsys.readouterr().out
    assert "✓ github" in out
    assert "✓ gdrive" in out
    assert "✗ federated" in out
    assert "ModuleNotFoundError" in out
    assert "sync: n/a" in out
    assert "42 total" in out
    assert "7 executions" in out
    assert "healthy" in out


def test_render_intake_board_shows_sync_state_row(capsys):
    _render_intake_board(_overview())
    out = capsys.readouterr().out
    assert "↳ ok @ 2026-08-01T05:00:00+00:00" in out
    assert "docs 120, errors 1, dlq 2" in out


def test_render_intake_board_missing_connectors(capsys):
    _render_intake_board({"total_jobs": 0})
    assert "dashboard section missing" in capsys.readouterr().out


class _FakeParent:
    class _Config:
        gateway_url = "http://x"
        timeout = 5

    class _Http:
        def __init__(self):
            self.urls = []

        def get(self, url, timeout=None):
            self.urls.append(url)
            if "/umrs/" in url:
                return {"connectors": {"ok": True}}
            raise Exception("404")

    def __init__(self, prefix="/gw"):
        self._config = self._Config()
        self._api_prefix = prefix
        self.http = self._Http()


def test_dashboard_client_falls_back_to_umrs_prefix():
    parent = _FakeParent(prefix="/gw")
    client = DashboardClient(parent)
    data = client.overview()
    assert data["connectors"]["ok"] is True
    assert parent.http.urls == [
        "http://x/gw/web/dashboard/overview",
        "http://x/umrs/web/dashboard/overview",
    ]


def test_dashboard_client_first_prefix_hit_no_fallback():
    parent = _FakeParent(prefix="/umrs")
    client = DashboardClient(parent)
    assert client.overview()["connectors"]["ok"] is True
    assert parent.http.urls == ["http://x/umrs/web/dashboard/overview"]


def test_dashboard_client_all_fail_raises():
    class _Http(_FakeParent._Http):
        def get(self, url, timeout=None):
            raise Exception("down")

    parent = _FakeParent()
    parent.http = _Http()
    client = DashboardClient(parent)
    with pytest.raises(Exception, match="Dashboard unreachable"):
        client.overview()


# ── r4 data catalog (ADR-031 catalogue plane) ────────────────────────────


def _search_payload(with_links: bool = True):
    return {
        "query": "xrd",
        "project_id": "proj-1",
        "results": [
            {
                "s3_key": "proj-1/xrd/run01.csv",
                "state": "ready",
                "detected_modality": "structured",
                "relevance_score": 0.5,
                "summary": "XRD scan\nsecond line",
                "tags": ["xrd", "batch-2"],
                "links": (
                    {
                        "document_id": "ragdoc-proj-1-run01",
                        "link_method": "s3_object",
                        "chunk_nodes": 4,
                        "vector_rows": [{"model": "gte-multilingual-base"}, {"model": None}],
                    }
                    if with_links
                    else None
                ),
            }
        ],
    }


def test_render_catalog_search_row_with_links(capsys):
    _render_catalog_search(_search_payload())
    out = capsys.readouterr().out
    assert "proj-1/xrd/run01.csv" in out
    assert "state=ready modality=structured" in out
    assert "tags: xrd, batch-2" in out
    # Summary second line must not leak into the row render.
    assert "second line" not in out
    assert "document=ragdoc-proj-1-run01 (via s3_object)" in out
    assert "chunk_nodes=4" in out
    assert "2 vector row(s) [gte-multilingual-base]" in out


def test_render_catalog_search_links_absent_is_honest_not_error(capsys):
    _render_catalog_search(_search_payload(with_links=False))
    out = capsys.readouterr().out
    assert "links: none (no canonical document minted)" in out
    assert "Traceback" not in out


def test_render_catalog_search_empty(capsys):
    _render_catalog_search({"query": "", "project_id": "p", "results": []})
    assert "(no catalog assets match)" in capsys.readouterr().out


def test_render_catalog_read_shows_range_and_links(capsys):
    _render_catalog_read(
        {
            "s3_key": "proj-1/xrd/run01.csv",
            "offset": 0,
            "length": 12,
            "total_size": 99,
            "truncated": True,
            "read_source": "extracted",
            "text": "2theta,intensity",
            "links": None,
        }
    )
    out = capsys.readouterr().out
    assert "bytes 0+12/99" in out
    assert "[truncated]" in out
    assert "source=extracted" in out
    assert "2theta,intensity" in out
    assert "links: none" in out


def test_render_catalog_stats(capsys):
    _render_catalog_stats(
        {
            "project_id": "proj-1",
            "object_count": 3,
            "states": {"ready": 2, "pending": 1},
            "modalities": {"structured": 3},
        }
    )
    out = capsys.readouterr().out
    assert "objects        : 3" in out
    assert "states         : pending: 1, ready: 2" in out
    assert "modalities     : structured: 3" in out


# ── D-39 rename: `r4 data assets` (new) + `catalog` alias (notice) ─────


def test_data_assets_dispatches_to_catalog_handler(capsys, monkeypatch):
    """The renamed surface rides the same handler — the alias keeps working."""
    from argparse import Namespace

    from r4._cli import data as data_cli

    seen = {}
    monkeypatch.setattr(data_cli, "_cmd_catalog", lambda args: seen.setdefault("ok", True) and 0)
    rc = data_cli.DataCommand().handle(Namespace(data_command="assets", catalog_command=None))
    assert rc == 0
    assert seen.get("ok") is True


def test_data_catalog_alias_prints_rename_notice(capsys, monkeypatch):
    from argparse import Namespace

    from r4._cli import data as data_cli

    monkeypatch.setattr(data_cli, "_cmd_catalog", lambda args: 0)
    rc = data_cli.DataCommand().handle(Namespace(data_command="catalog", catalog_command=None))
    assert rc == 0
    err = capsys.readouterr().err
    assert "now `r4 data assets`" in err
    assert "Scientific Catalog" in err
