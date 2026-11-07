"""Tests for research loop control client methods + CLI handler."""

from __future__ import annotations

import json
import pathlib
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest


def _snapshot_calls(mock_core: MagicMock, method: str) -> list[str]:
    return [c.args[0] for c in getattr(mock_core, method).call_args_list]


class TestLoopClient:
    def test_start_loop_payload(self):
        from r4.client import Client

        mock_core = MagicMock()
        with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
            client = Client()
            mock_core.post = MagicMock(return_value={"loop_id": "loop-1", "status": "running"})
            out = client.start_loop("proj-a", "goal-x", max_iterations=5, approved=True)
        args, kwargs = mock_core.post.call_args
        assert args[0].endswith("/gw/loops")
        assert kwargs["json_body"]["project_id"] == "proj-a"
        assert kwargs["json_body"]["research_goal"] == "goal-x"
        assert kwargs["json_body"]["max_iterations"] == 5
        assert kwargs["json_body"]["approved"] is True
        assert out["loop_id"] == "loop-1"

    @staticmethod
    def _lifecycle_paths() -> tuple[list[str], list[str]]:
        from r4.client import Client

        mock_core = MagicMock()
        with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
            client = Client()
            mock_core.get = MagicMock(return_value={"ok": True})
            mock_core.post = MagicMock(return_value={"ok": True})
            client.list_loops()
            client.get_loop_status("loop-1")
            client.list_loop_iterations("loop-1")
            client.stop_loop("loop-1")
            client.pause_loop("loop-1")
            client.resume_loop("loop-1")
        return _snapshot_calls(mock_core, "get"), _snapshot_calls(mock_core, "post")

    @staticmethod
    def _assert_suffixes(paths: list[str], suffixes: list[str]) -> None:
        for suffix in suffixes:
            assert any(p.endswith(suffix) for p in paths), f"missing route {suffix}"

    def test_lifecycle_read_routes(self):
        get_paths, _ = self._lifecycle_paths()
        self._assert_suffixes(
            get_paths, ["/gw/loops", "/gw/loops/loop-1", "/gw/loops/loop-1/iterations"]
        )

    def test_lifecycle_control_routes(self):
        _, post_paths = self._lifecycle_paths()
        self._assert_suffixes(
            post_paths,
            ["/gw/loops/loop-1/stop", "/gw/loops/loop-1/pause", "/gw/loops/loop-1/resume"],
        )

    def test_start_loop_passes_workflow_name_only_when_given(self):
        from r4.client import Client

        mock_core = MagicMock()
        with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
            client = Client()
            mock_core.post = MagicMock(return_value={"loop_id": "loop-1"})
            client.start_loop("proj-a", "goal")
            assert "workflow_name" not in mock_core.post.call_args.kwargs["json_body"]
            client.start_loop("proj-a", "goal", workflow_name="demo08_yield_eval")
            assert (
                mock_core.post.call_args.kwargs["json_body"]["workflow_name"] == "demo08_yield_eval"
            )

    def test_start_loop_passes_max_tracks_only_above_one(self):
        """ADR-066 amendment 2 (D-34): parallel tracks are opt-in."""
        from r4.client import Client

        mock_core = MagicMock()
        with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
            client = Client()
            mock_core.post = MagicMock(return_value={"loop_id": "loop-1"})
            client.start_loop("proj-a", "goal")
            assert "max_tracks" not in mock_core.post.call_args.kwargs["json_body"]
            client.start_loop("proj-a", "goal", max_tracks=3)
            assert mock_core.post.call_args.kwargs["json_body"]["max_tracks"] == 3

    def test_approval_inbox_routes(self):
        """ADR-036 (a): pending approvals are answerable from any client."""
        from r4.client import Client

        mock_core = MagicMock()
        with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
            client = Client()
            mock_core.get = MagicMock(return_value={"pending": [], "durability": "postgres"})
            mock_core.post = MagicMock(return_value={"status": "approved"})
            out = client.list_loop_approvals(loop_id="loop-1")
            decided = client.decide_loop_approval("appr-1", "once")
        get_args, get_kwargs = mock_core.get.call_args
        assert get_args[0].endswith("/gw/companion/approvals/pending")
        assert get_kwargs["params"] == {"loop_id": "loop-1"}
        assert out["durability"] == "postgres"
        post_args, post_kwargs = mock_core.post.call_args
        assert post_args[0].endswith("/gw/companion/approvals/appr-1/decision")
        assert post_kwargs["json_body"] == {"decision": "once"}
        assert decided["status"] == "approved"


class TestLoopCli:
    def _ns(self, **kw):
        defaults = dict(
            loop_action="status",
            target="loop-1",
            goal=None,
            action=None,
            json=False,
            max_iterations=10,
            approve=False,
            interval=0.01,
            project_id=None,
            decision=None,
            workflow="",
        )
        defaults.update(kw)
        return Namespace(**defaults)

    def test_status_prints_snapshot(self, capsys):
        from r4._cli.project_knowledge import project_loop

        fake_client = MagicMock()
        fake_client.get_loop_status = MagicMock(
            return_value={
                "loop_id": "loop-1",
                "project_id": "proj",
                "status": "running",
                "phase": "executing",
                "current_iteration": 3,
                "max_iterations": 10,
                "total_hypotheses_tested": 3,
                "total_experiments_run": 2,
                "total_findings": 5,
                "success_rate": 0.66,
            }
        )
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(), MagicMock())
        out = capsys.readouterr().out
        assert "running" in out and "3/10" in out and "66%" in out

    def test_start_uses_saved_goal(self, capsys, tmp_path):
        from r4._cli.project_knowledge import project_loop

        pm = MagicMock()
        pm.data_dir.return_value = tmp_path
        (tmp_path / "research_plan.json").write_text(json.dumps({"research_goal": "saved-goal"}))

        fake_client = MagicMock()
        resp = {"loop_id": "loop-9", "status": "running", "message": "ok"}
        fake_client.start_loop = MagicMock(return_value=resp)
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(loop_action="start", target="proj-x"), pm)
        fake_client.start_loop.assert_called_once()
        args, kwargs = fake_client.start_loop.call_args
        assert args[0] == "proj-x"
        assert args[1] == "saved-goal"
        assert "started" in capsys.readouterr().out

    def test_stop_route(self, capsys):
        from r4._cli.project_knowledge import project_loop

        fake_client = MagicMock()
        fake_client.stop_loop = MagicMock(return_value={"status": "stopped", "message": "done"})
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(loop_action="stop"), MagicMock())
        fake_client.stop_loop.assert_called_once_with("loop-1")
        assert "stopping" not in capsys.readouterr().out  # sanity on message only

    def test_approvals_lists_pending_inbox(self, capsys):
        from r4._cli.project_knowledge import project_loop

        fake_client = MagicMock()
        fake_client.list_loop_approvals = MagicMock(
            return_value={
                "pending": [
                    {
                        "id": "appr-1",
                        "loop_id": "loop-1",
                        "iteration": 2,
                        "action": {"kind": "workflow_trigger", "workflow_name": "wf-x"},
                    }
                ],
                "durability": "postgres",
            }
        )
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(loop_action="approvals", target=None), MagicMock())
        fake_client.list_loop_approvals.assert_called_once_with(loop_id=None)
        out = capsys.readouterr().out
        assert "appr-1" in out and "wf-x" in out and "decide" in out

    def test_decide_posts_decision(self, capsys):
        from r4._cli.project_knowledge import project_loop

        fake_client = MagicMock()
        fake_client.decide_loop_approval = MagicMock(
            return_value={"approval_id": "appr-1", "status": "approved", "loop_id": "loop-1"}
        )
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(
                self._ns(loop_action="decide", target="appr-1", decision="once"), MagicMock()
            )
        fake_client.decide_loop_approval.assert_called_once_with("appr-1", "once")
        assert "approved" in capsys.readouterr().out

    def test_decide_requires_decision_flag(self):
        from r4._cli.project_knowledge import project_loop

        with patch("r4.client.Client", return_value=MagicMock()), pytest.raises(SystemExit):
            project_loop(self._ns(loop_action="decide", target="appr-1"), MagicMock())

    def test_loop_start_forwards_workflow_flag(self, capsys, tmp_path):
        from r4._cli.project_knowledge import project_loop

        pm = MagicMock()
        pm.data_dir.return_value = tmp_path
        fake_client = MagicMock()
        fake_client.start_loop = MagicMock(
            return_value={"loop_id": "loop-9", "status": "running", "workflow_name": "wf-a"}
        )
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(
                self._ns(loop_action="start", target="proj-x", goal="goal", workflow="wf-a"),
                pm,
            )
        assert fake_client.start_loop.call_args.kwargs["workflow_name"] == "wf-a"
        assert "started" in capsys.readouterr().out

    def test_loop_start_forwards_tracks_flag(self, capsys, tmp_path):
        from r4._cli.project_knowledge import project_loop

        pm = MagicMock()
        pm.data_dir.return_value = tmp_path
        fake_client = MagicMock()
        fake_client.start_loop = MagicMock(return_value={"loop_id": "loop-9", "status": "running"})
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(loop_action="start", target="proj-x", goal="goal", tracks=2), pm)
        assert fake_client.start_loop.call_args.kwargs["max_tracks"] == 2

    def test_tracks_flag_registered_and_bounded(self):
        import r4._cli.project as project_mod

        src = pathlib.Path(project_mod.__file__).read_text()
        assert '"--tracks"' in src and "choices=range(1, 5)" in src

    def test_start_prints_resolved_workflow(self, capsys, tmp_path):
        from r4._cli.project_knowledge import project_loop

        pm = MagicMock()
        pm.data_dir.return_value = tmp_path
        fake_client = MagicMock()
        fake_client.start_loop = MagicMock(
            return_value={
                "loop_id": "loop-9",
                "status": "running",
                "message": "ok",
                "workflow_name": "demo08_yield_eval",
            }
        )
        with patch("r4.client.Client", return_value=fake_client):
            project_loop(self._ns(loop_action="start", target="proj-x", goal="goal"), pm)
        out = capsys.readouterr().out
        assert "workflow: demo08_yield_eval" in out
        assert "loop watch loop-9" in out
