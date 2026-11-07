"""D-27 pins: the skill-edit plane CLI — pending / approve (merge-on-green) / reject."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from r4._cli.skill import SkillCommand


class _FakeClient:
    """Records requests; serves a fixed pending list."""

    pending: list[dict] = []
    gets: list[str] = []
    posts: list[tuple[str, dict | None]] = []
    post_error: Exception | None = None

    def get(self, path: str, **kwargs):  # noqa: ARG002
        type(self).gets.append(path)
        if path == "/gw/skills/pending":
            return list(type(self).pending)
        return []

    def post(self, path: str, json=None, **kwargs):  # noqa: ARG002, A002
        if type(self).post_error is not None:
            raise type(self).post_error
        type(self).posts.append((path, json))
        return {
            "skill_id": "skl-1",
            "manifest": {"name": "compiled-method"},
            "enabled": True,
            "review_state": {"status": "approved", "reviewer": "alice"},
        }


@pytest.fixture()
def fake_client(monkeypatch):
    _FakeClient.pending = [
        {
            "skill_id": "skl-1",
            "manifest": {"name": "compiled-method"},
            "scope": "project",
            "project_id": "proj-a",
            "enabled": False,
            "review_state": {
                "status": "pending_review",
                "source_ir_ids": ["ir-1"],
                "proposed_at": "2026-09-24T00:00:00+00:00",
            },
        }
    ]
    _FakeClient.gets = []
    _FakeClient.posts = []
    _FakeClient.post_error = None
    monkeypatch.setattr("r4.client.Client", _FakeClient)


def _skill_dir(tmp_path: str, *, with_tests: bool, green: bool = True) -> Path:
    d = Path(tmp_path) / "reviewed-copy"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: compiled-method\n---\nbody\n")
    if with_tests:
        (d / "tests").mkdir(exist_ok=True)
        exit_line = "exit 0" if green else "exit 3"
        (d / "tests" / "run.sh").write_text(f"#!/usr/bin/env bash\necho 'suite ran'\n{exit_line}\n")
    return d


def _run(**overrides):
    base = {"skill_action": "pending"}
    base.update(overrides)
    return SkillCommand().handle(Namespace(**base))


def test_pending_lists_proposals(fake_client, capsys):
    assert _run() == 0
    out = capsys.readouterr().out
    assert "skl-1" in out
    assert "compiled-method" in out
    assert "/gw/skills/pending" in _FakeClient.gets


def test_pending_json_round_trips(fake_client, capsys):
    assert _run(as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["review_state"]["status"] == "pending_review"


def test_approve_needs_a_local_skill_dir(fake_client, capsys):
    rc = _run(skill_action="approve", target="compiled-method", skill_dir=None, note="", timeout=10)
    assert rc == 1
    assert "merge-on-green" in capsys.readouterr().err.lower() or _FakeClient.posts == []


def test_approve_without_tests_refuses_by_name(fake_client, tmp_path, capsys):
    skill_dir = _skill_dir(tmp_path, with_tests=False)
    rc = _run(
        skill_action="approve",
        target="compiled-method",
        skill_dir=str(skill_dir),
        note="",
        timeout=10,
    )
    assert rc == 1
    assert _FakeClient.posts == []
    assert "no tests" in capsys.readouterr().err


def test_approve_red_suite_never_merges(fake_client, tmp_path, capsys):
    skill_dir = _skill_dir(tmp_path, with_tests=True, green=False)
    rc = _run(
        skill_action="approve",
        target="compiled-method",
        skill_dir=str(skill_dir),
        note="",
        timeout=10,
    )
    assert rc == 1
    assert _FakeClient.posts == []
    assert "never merges" in capsys.readouterr().err


def test_approve_green_suite_merges(fake_client, tmp_path, capsys):
    skill_dir = _skill_dir(tmp_path, with_tests=True, green=True)
    rc = _run(
        skill_action="approve",
        target="compiled-method",
        skill_dir=str(skill_dir),
        note="verified",
        timeout=10,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Merged" in out
    ((path, body),) = _FakeClient.posts
    assert path == "/gw/skills/skl-1/review"
    assert body["decision"] == "approved"
    assert body["note"] == "verified"
    receipt = body["test_receipt"]
    assert receipt["exit_code"] == 0
    assert receipt["tests_present"] is True
    assert receipt["runner"] == "r4 skill test"


def test_approve_resolves_by_id(fake_client, tmp_path, capsys):
    skill_dir = _skill_dir(tmp_path, with_tests=True)
    rc = _run(
        skill_action="approve",
        target="skl-1",
        skill_dir=str(skill_dir),
        note="",
        timeout=10,
    )
    assert rc == 0
    assert _FakeClient.posts[0][0] == "/gw/skills/skl-1/review"


def test_approve_surfaces_server_refusal(fake_client, tmp_path, capsys):
    _FakeClient.post_error = RuntimeError("403 skills.merge_not_green")
    skill_dir = _skill_dir(tmp_path, with_tests=True)
    rc = _run(
        skill_action="approve",
        target="compiled-method",
        skill_dir=str(skill_dir),
        note="",
        timeout=10,
    )
    assert rc == 1
    assert "Review refused" in capsys.readouterr().err


def test_reject_posts_decision(fake_client, capsys):
    rc = _run(skill_action="reject", target="compiled-method", note="not reusable")
    assert rc == 0
    ((path, body),) = _FakeClient.posts
    assert path == "/gw/skills/skl-1/review"
    assert body["decision"] == "rejected"
    assert body.get("test_receipt") is None  # reject carries no receipt


def test_target_miss_refuses_by_name(fake_client, capsys):
    _FakeClient.pending = []
    rc = _run(skill_action="reject", target="missing", note="")
    assert rc == 1
    assert "No pending skill proposal" in capsys.readouterr().err
