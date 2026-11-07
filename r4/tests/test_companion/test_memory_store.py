"""ADR-111: the local saved-memory store (one private file per fact)."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from r4.companion.memory_store import (
    INDEX_FILE,
    MemoryConflict,
    MemoryFact,
    MemoryStore,
    MemoryStoreError,
    import_legacy_memory,
    parse_fact,
    project_key,
    repository_root,
    store_for,
)


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory", "gw--demo")


def _fact(name: str = "sbatch-arrays", **kw) -> MemoryFact:
    base = {
        "name": name,
        "type": "feedback",
        "description": "Prefer sbatch arrays for parameter sweeps",
        "body": "One array job, not 200 jobs: the queue limit is per user.",
        "scope": "project",
        "source": "agent",
    }
    base.update(kw)
    return MemoryFact(**base)


def test_write_then_read_round_trips_front_matter_and_body(store):
    saved = store.write(_fact(source_session="sess-1", expires="2099-01-01"))
    path = store.scope_dir("project") / "sbatch-arrays.md"
    assert path.stat().st_mode & 0o777 == 0o600
    fact = store.get("sbatch-arrays", "project")
    assert fact is not None and fact.sha == saved.sha
    assert (fact.type, fact.source, fact.source_session) == ("feedback", "agent", "sess-1")
    assert fact.body.startswith("One array job")
    assert fact.created and fact.updated and fact.expires == "2099-01-01"


def test_index_view_is_rewritten_for_people(store):
    store.write(_fact())
    view = (store.scope_dir("project") / INDEX_FILE).read_text()
    assert "- sbatch-arrays [feedback]: Prefer sbatch arrays" in view


def test_replacing_needs_the_sha_that_was_read(store):
    first = store.write(_fact())
    with pytest.raises(MemoryConflict, match="already exists"):
        store.write(_fact(description="changed"))
    with pytest.raises(MemoryConflict, match="changed since it was read"):
        store.write(_fact(description="changed"), expected_sha="0" * 16)
    second = store.write(_fact(description="changed"), expected_sha=first.sha)
    assert second.sha != first.sha
    assert store.get("sbatch-arrays", "project").created == first.created


def test_forget_with_a_stale_sha_is_a_conflict(store):
    store.write(_fact())
    with pytest.raises(MemoryConflict):
        store.forget("sbatch-arrays", "project", expected_sha="stale")
    assert store.forget("sbatch-arrays", "project") is True
    assert store.forget("sbatch-arrays", "project") is False


@pytest.mark.parametrize("name", ["../escape", "a/b", "", "Upper", "-dash", "x" * 65])
def test_names_that_could_leave_the_scope_are_refused(store, name):
    with pytest.raises(MemoryStoreError):
        store.write(_fact(name=name))
    assert store.get(name, "project") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("type", "secret"), ("scope", "team"), ("description", ""), ("expires", "soon")],
)
def test_invalid_fields_are_refused(store, field, value):
    with pytest.raises(MemoryStoreError):
        store.write(_fact(**{field: value}))


def test_secret_shaped_text_is_refused_but_a_git_sha_is_fine(store):
    with pytest.raises(MemoryStoreError, match="credential"):
        store.write(_fact(body="token: Bearer abcdefghijklmnopqrstuvwxyz0123"))
    with pytest.raises(MemoryStoreError, match="credential"):
        store.write(_fact(body="password = hunter2hunter2"))
    saved = store.write(_fact(body="pinned at 4c57e715c3a1b0d2e9f8a7b6c5d4e3f2a1b0c9d8"))
    assert saved.sha


def test_read_only_store_refuses_writes(tmp_path):
    ro = MemoryStore(tmp_path / "memory", "p", read_only=True)
    with pytest.raises(MemoryStoreError, match="read-only"):
        ro.write(_fact())


def test_index_prompt_lists_live_facts_and_drops_expired_ones(store):
    store.write(_fact())
    past = (date.today() - timedelta(days=1)).isoformat()
    store.write(_fact(name="old-partition", description="Use ng-dgx-m0", expires=past))
    store.write(_fact(name="me", type="user", scope="user", description="Seismologist"))
    prompt = store.index_prompt()
    assert prompt.startswith("[Saved memory:")
    assert "## user\n- me [user]: Seismologist" in prompt
    assert "## project\n- sbatch-arrays [feedback]" in prompt
    assert "old-partition" not in prompt
    assert "One array job" not in prompt  # bodies stay out of the prompt


def test_index_prompt_is_capped_honestly(store):
    for n in range(40):
        store.write(_fact(name=f"fact-{n:02d}", description="d" * 150))
    prompt = store.index_prompt(cap=1000)
    assert len(prompt) < 1400
    assert prompt.rstrip().endswith("memory_read() lists it]")


def test_empty_store_injects_nothing(store):
    assert store.index_prompt() == ""


def test_two_sessions_writing_at_once_keep_every_fact(store):
    def write(n: int) -> None:
        other = MemoryStore(store.root, store.project)
        other.write(_fact(name=f"fact-{n:02d}"))

    threads = [threading.Thread(target=write, args=(n,)) for n in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.facts("project")) == 16
    view = (store.scope_dir("project") / INDEX_FILE).read_text()
    assert view.count("- fact-") == 16


def test_project_key_keeps_gateways_apart():
    a = project_key("demo", "https://rccs.example.org/gw")
    b = project_key("demo", "https://bdrt.example.org")
    assert a == "rccs.example.org--demo" and b == "bdrt.example.org--demo"


def test_a_linked_worktree_shares_its_repository_memory(tmp_path):
    main = tmp_path / "repo"
    (main / ".git" / "worktrees" / "lane").mkdir(parents=True)
    lane = main / ".r4" / "worktrees" / "lane"
    lane.mkdir(parents=True)
    (lane / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'lane'}\n")
    assert repository_root(lane / "sub") == main.resolve()
    assert project_key("", "", lane) == project_key("", "", main)
    assert project_key("", "", main).startswith("workspace--repo-")


def test_store_for_follows_the_client_project(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("R4_COMPANION_MEMORY_DIR", str(tmp_path / "legacy"))

    class _Config:
        project_id = "p1"
        gateway_url = "https://gw.example.org"

    class _Client:
        config = _Config()

    client = _Client()
    assert store_for(client).project == "gw.example.org--p1"
    client.config.project_id = "p2"
    assert store_for(client).project == "gw.example.org--p2"


# A Stripe-shaped fixture built at runtime: secret scanners (the public export's
# gitleaks gate, GitHub push protection) flag the literal form (2026-11-07).
_STRIPE_SHAPED_KEY = "sk_" + "live_" + "abcdefghijklmnopqrstu"


def test_legacy_memory_json_is_imported_once_into_the_user_scope(tmp_path):
    legacy = tmp_path / "memory.json"
    legacy.write_text(
        json.dumps(
            {
                "partition": {"key": "partition", "value": "gpu_a100", "source": "jupyter"},
                "project": "Seismic wave propagation",
                "token": "api_key = " + _STRIPE_SHAPED_KEY,
            }
        )
    )
    store = MemoryStore(tmp_path / "memory", "p")
    assert import_legacy_memory(store, legacy) == 2  # the secret-shaped one stays behind
    fact = store.get("partition", "user")
    assert fact is not None and fact.body == "gpu_a100" and fact.source == "import"
    assert import_legacy_memory(store, legacy) == 0
    assert legacy.is_file()  # the old file is kept


def test_parse_fact_rejects_files_without_front_matter():
    assert parse_fact("just text", "user") is None
    assert parse_fact("---\ntype: user\n---\nbody", "user") is None


# -- review 2026-11-07 ---------------------------------------------------------


def test_a_non_utf8_file_is_skipped_not_fatal(store):
    store.write(_fact())
    text = "---\nname: sjis\n---\n\u5206\u533a"
    (store.scope_dir("project") / "sjis.md").write_bytes(text.encode("cp932"))
    assert [f.name for f in store.facts()] == ["sbatch-arrays"]
    assert "sbatch-arrays" in store.index_prompt()


def test_non_ascii_keys_never_collapse():
    from r4.companion.memory_store import slugify

    a, b = slugify("\u30d7\u30ed\u30b8\u30a7\u30af\u30c8"), slugify("\u5206\u533a")
    assert a != b
    assert a.startswith("fact-") and len(a) == len("fact-") + 8
    assert slugify("Caf\u00e9 notes") != slugify("Cafe notes")
    assert slugify("plain words") == "plain-words"


def test_a_name_with_a_trailing_newline_is_refused(store):
    with pytest.raises(MemoryStoreError):
        store.write(_fact(name="foo\n"))


def test_a_legacy_file_that_is_not_a_mapping_is_ignored(tmp_path):
    legacy = tmp_path / "memory.json"
    legacy.write_text("[1, 2, 3]")
    store = MemoryStore(tmp_path / "memory", "p")
    assert import_legacy_memory(store, legacy) == 0
    assert store.index_prompt() == ""
