"""SL-CONF T2 — the four planes, live, on a running stand-alone stack.

Plan §3c.4 T2 (WS-09): one note goes through the knowledge graph, RAG, the
artifacts plane, a research loop and a team, driven by the real ``r4`` client
from a throwaway config. An ``e2e`` tier test (ADR-054: opt-in with
``R4_RUN_E2E=1``), and skipped by name when no stack answers::

    ./deploy/standalone/standalone.sh up --dev      # or: up (hardened)
    R4_RUN_E2E=1 STANDALONE_PROFILE=dev pytest tests/test_standalone_live_e2e.py

**Every assertion names an artifact** — a row, a vector, a cited source, a
finding, an event — never a job status. ``KNOWLEDGE_EMBED`` once "completed
successfully" having written nothing (plan §3b.3), and a team run said
"success" with both roles failed (report §12): a suite that trusts a status
passes on exactly the failures it exists to catch.

The stores are reached with ``standalone.sh exec`` (``compose exec -T``), the
only vocabulary valid in both profiles. The loop plane needs uploaded step code,
which only the dev profile runs (ADR-074); hardened skips it by name.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STANDALONE = ROOT / "deploy" / "standalone" / "standalone.sh"
WORKFLOW = ROOT / "demos" / "08_scientific_data_management" / "workflow" / "demo08_yield_eval"
PROFILE = os.environ.get("STANDALONE_PROFILE", "hardened")
PROJECT = "default"

GATEWAY = "http://127.0.0.1:8080"


def _stack_answers() -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(f"{GATEWAY}/health", timeout=2).close()
    except OSError:
        return False
    return True


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _stack_answers(),
        reason=f"no stand-alone stack answers on {GATEWAY} (deploy/standalone/standalone.sh up)",
    ),
]

NONCE = uuid.uuid4().hex[:8]
NOTE_NAME = f"t2-zeolite-{NONCE}.md"
# New bytes every run. Identical bytes dedupe to the artifact the project
# already holds, so every run after the first would pass the knowledge plane
# without extracting anything.
NOTE = (
    "# Zeolite adsorption note\n\n"
    "ZSM-5 zeolite adsorbs carbon dioxide at room temperature. Ion exchange with\n"
    "sodium raises its CO2 uptake. A group at Hokkaido University reported 2.1 mmol/g.\n"
    f"Sample batch {NONCE} was measured twice.\n"
)
QUESTION = "Does sodium exchange raise ZSM-5 CO2 uptake?"


# ── the stack and the client ────────────────────────────────────────────────


def _standalone(*args: str, stdin: str | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(
        [str(STANDALONE), *args],
        env={**os.environ, "STANDALONE_PROFILE": PROFILE},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"standalone.sh {args[0]}: {proc.stderr[-2000:]}"
    return proc.stdout


def _sql(query: str) -> list[list[str]]:
    script = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "\t" -c "$1"'
    out = _standalone("exec", "postgres", "sh", "-c", script, "sh", query)
    return [line.split("\t") for line in out.splitlines() if line]


def _count(query: str) -> int:
    rows = _sql(query)
    return int(rows[0][0]) if rows else 0


def _wait_for(probe: Callable[[], object], what: str, timeout: int) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(5)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


class _R4:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(home), "R4_TIMEOUT": "300"}
        self.env.pop("R4_TEAM_RUNS_DIR", None)
        # This checkout's client first: an r4 installed elsewhere on PATH may
        # be another version (one lacked the team-engine SPI, live 2026-10-09).
        venv = ROOT / ".venv" / "bin" / "r4"
        self.binary = os.environ.get("R4_BIN") or (
            str(venv) if venv.exists() else shutil.which("r4")
        )

    def __call__(self, *args: str, timeout: int = 600, ok: bool = True) -> str:
        proc = subprocess.run(
            [self.binary, *args],
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if ok:
            assert proc.returncode == 0, f"r4 {' '.join(args)}:\n{proc.stdout}\n{proc.stderr}"
        return proc.stdout


@pytest.fixture(scope="module")
def r4(tmp_path_factory) -> _R4:
    """A client logged in as the stack's scientist, in a config of its own."""
    client = _R4(tmp_path_factory.mktemp("r4-config"))
    for line in _standalone("token").splitlines():
        if line.startswith(("r4 source ", "r4 auth login ")):
            client(*shlex.split(line)[1:])
    return client


# ── knowledge graph + RAG ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def note(r4, tmp_path_factory) -> dict[str, str]:
    """Upload the note; wait for its graph and its vectors — the artifacts."""
    path = tmp_path_factory.mktemp("t2") / NOTE_NAME
    path.write_text(NOTE)
    out = r4("knowledge", "upload", "-p", PROJECT, str(path))
    job = re.search(r"Queued for knowledge ingest:\s*(job-\S+)", out).group(1)
    rows = _wait_for(
        lambda: _sql(
            "SELECT artifact_id FROM knowledge_artifacts "
            f"WHERE metadata->>'staged_from' LIKE '%/{NOTE_NAME}'"
        ),
        "the upload's knowledge_artifacts row",
        300,
    )
    artifact = rows[0][0]
    _wait_for(lambda: _vectors(artifact), "the note's vectors", 900)
    return {"artifact": artifact, "job": job}


def _vectors(artifact: str) -> int:
    return _count(
        "SELECT count(*) FROM knowledge_node_embeddings e "
        "JOIN knowledge_nodes n ON n.node_id = e.node_id "
        f"WHERE n.content->>'artifact_id' = '{artifact}' AND e.model_name LIKE '%/e5-base-v2'"
    )


def test_the_note_has_a_semantic_graph(note):
    """Entities and concepts the local model extracted, linked from the file node."""
    mentions = _count(
        "SELECT count(*) FROM knowledge_edges e JOIN knowledge_nodes n ON n.node_id = e.target_id "
        f"WHERE e.source_id LIKE 'node-file-%{note['artifact']}' AND e.relation_type = 'mentions' "
        "AND n.node_type IN ('entity', 'concept')"
    )
    assert mentions >= 1


def test_the_note_is_embedded_in_the_bundled_space(note):
    assert _vectors(note["artifact"]) >= 1


def test_a_question_is_answered_from_the_note(r4, note):
    out = r4("knowledge", "query", QUESTION, "--project", PROJECT)
    sources = out.split("--- Sources ---", 1)
    assert len(sources) == 2, out
    assert re.search(r"\[1\][^\n]*zeolite", sources[1]), sources[1][:600]


# ── artifacts: provenance and the A2L ledger ────────────────────────────────


def test_the_ingest_job_names_its_stored_artifact(r4, note):
    record = json.loads(r4("provenance", "show", note["job"], "--json"))
    stored = json.dumps(record.get("artifacts") or record)
    assert "projects/default/knowledge/artifacts/" in stored and "sha256" in stored


# An admin creates the root (ingestion is admin-only). The token is minted and
# spent inside the umrs container, through the edge, which is where identity is
# verified; the ledger trusts no bearer token on its own port.
_ROOT_DATASET = """
import json, subprocess, urllib.request
token = json.loads(subprocess.check_output(
    ["umrs-server", "admin", "token", "generate-jwt", "--user", "t2-admin", "--roles", "admin",
     "--project", "default", "--days", "1", "--no-gateway-url", "--json"]))["token"]
body = json.dumps({"type": "dataset", "uri": "s3://r4-data/t2/%s.h5"}).encode()
request = urllib.request.Request(
    "http://gateway-nginx:8080/gw/a2l/artifacts", data=body, method="POST",
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(request, timeout=30))["id"])
"""


def test_a_derived_artifact_carries_its_lineage(r4):
    """Root creation is admin-only (ingestion); the scientist derives through the gateway."""
    root = _standalone("exec", "umrs", "python", "-", stdin=_ROOT_DATASET % NONCE).strip()
    child = json.loads(
        r4(
            "artifact",
            "derive",
            "--type",
            "report",
            "--uri",
            f"s3://r4-data/t2/{NONCE}.md",
            "--parent",
            root,
            "--project",
            PROJECT,
        )
    )
    graph = json.loads(r4("artifact", "lineage", child["id"], "--json"))
    assert {root, child["id"]} <= {node["id"] for node in graph["nodes"]}
    assert (root, child["id"]) in {(e["parent_id"], e["child_id"]) for e in graph["edges"]}


def test_the_ledger_refuses_an_anonymous_caller():
    """No data without identity: a 401, or the browser login redirect."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=30)
    conn.request("GET", "/gw/a2l/artifacts")
    response = conn.getresponse()
    location = response.getheader("Location") or ""
    assert response.status == 401 or (response.status == 302 and "/auth/web-login" in location), (
        response.status,
        location,
    )


# ── the research loop ───────────────────────────────────────────────────────


@pytest.mark.skipif(
    PROFILE != "dev",
    reason="uploaded step code runs only in the dev profile (ADR-074; hardened waits on ADR-091)",
)
def test_a_loop_publishes_a_finding_evidenced_by_the_note(r4, note):
    r4("workflow", "upload", str(WORKFLOW))
    out = r4(
        "project",
        "loop",
        "start",
        PROJECT,
        QUESTION,
        "--max-iterations",
        "1",
        "--workflow",
        WORKFLOW.name,
    )
    loop = re.search(r"loop-[0-9a-f]{12}", out).group(0)
    findings = _wait_for(
        lambda: _sql(
            "SELECT replace(f->'evidence_refs'->0->>'excerpt', E'\\n', ' ') "
            "FROM research_loop_iterations i, "
            f"jsonb_array_elements(i.findings) f WHERE i.loop_id = '{loop}'"
        ),
        f"a finding from {loop}",
        900,
    )
    assert "ZSM-5" in findings[0][0], findings
    # Minted back into the spine with its loop (ADR-027 D6).
    assert _count(f"SELECT count(*) FROM knowledge_nodes WHERE node_id = 'loop:{loop}'") == 1


# ── teamup ──────────────────────────────────────────────────────────────────


def test_every_role_of_a_team_answers(r4):
    out = r4(
        "team",
        "launch",
        f"t2-{NONCE}",
        "--engine",
        "native",
        "--goal",
        QUESTION,
        "--roles",
        "pm,se",
        "--max-iterations",
        "1",
    )
    run_dir = Path(re.search(r"run_dir:\s*(\S+)", out).group(1))
    assert run_dir.is_relative_to(r4.home)  # beside the rest of r4's state
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    spoke = {
        e["actor"]["name"]
        for e in events
        if e["kind"] == "agent_message" and e["payload"].get("delta", "").strip()
    }
    assert spoke == {"pm", "se"}


# ── team plans (ADR-092) ────────────────────────────────────────────────────

_LENS_PLAN = {
    "plan_version": 1,
    "name": "t2-two-lens-notes",
    "description": "Two readers note a file through two lenses; a third merges their notes.",
    "params": {"target": {"type": "string", "required": True}},
    "agents": {
        "reader": {
            "instructions": "Read the file with file_read and answer briefly.",
            "tools": ["file_read"],
            "max_tool_rounds": 3,
        }
    },
    "concurrency": 2,
    "budget": {"max_tokens": 200000, "max_agents": 5},
    "phases": [
        {
            "id": "read",
            "title": "Read",
            "steps": [
                {
                    "id": "notes",
                    "kind": "agent",
                    "use": "reader",
                    "label": "reader",
                    "fanout": {"values": ["correctness", "naming"], "as": "lens"},
                    "prompt": "Read {{params.target}}. Through the lens of {{lens}}, "
                    "write at most two sentences of notes.",
                    "schema": {
                        "type": "object",
                        "properties": {"notes": {"type": "string", "maxLength": 800}},
                        "required": ["notes"],
                    },
                }
            ],
        },
        {
            "id": "merge",
            "title": "Merge",
            "steps": [
                {
                    "id": "merged",
                    "kind": "agent",
                    "label": "merger",
                    "prompt": "Merge these notes into at most three sentences:\n"
                    "{{steps.notes.output|json}}",
                }
            ],
        },
    ],
    "output": {"notes": "steps.notes.output", "merged": "steps.merged.output"},
}


def test_a_team_plan_runs_and_resumes(r4, tmp_path_factory):
    """Fan-out, a schema per agent, a merge, the journal and a free resume.
    Asserts the artifacts, not the model's quality: a small model may fail a
    schema, and that must show as a named null, never as a pass."""
    work = tmp_path_factory.mktemp("plan")
    target = work / "target.py"
    target.write_text("def mean(xs):\n    return sum(xs) / len(xs)\n")
    plan = work / "plan.json"
    plan.write_text(json.dumps(_LENS_PLAN))
    out = r4("team", "plan", "run", str(plan), "--arg", f"target={target}", ok=False)
    run_dir = Path(re.search(r"run_dir:\s*(\S+)", out).group(1))
    assert run_dir.is_relative_to(r4.home)
    first = _assert_first_attempt(run_dir)
    resumed = r4("team", "plan", "resume", run_dir.name, ok=False)
    _assert_resume_replays(run_dir, resumed, first)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _assert_first_attempt(run_dir: Path) -> dict[str, bool]:
    """The run's artifacts; returns each journalled call's ok by label."""
    for name in ("plan.json", "params.json", "resolution.json", "events.jsonl", "journal.jsonl"):
        assert (run_dir / name).is_file(), name
    result = json.loads((run_dir / "result.json").read_text())
    resolution = json.loads((run_dir / "resolution.json").read_text())
    assert (resolution["engine"], resolution["project"]) == ("plan", PROJECT)
    assert result["journal"] == "local-cache"
    assert result["budget"]["spent_tokens"] <= 200000
    assert all(len(s["null_reasons"]) == s["null"] for s in result["steps"].values())
    return _journal_oks(run_dir)


def _journal_oks(run_dir: Path) -> dict[str, bool]:
    """Every journalled call was metered; each call's ok by label."""
    records = _jsonl(run_dir / "journal.jsonl")
    assert all(r["usage"]["input_tokens"] > 0 for r in records)
    return {r["label"]: r["ok"] for r in records}


def _assert_resume_replays(run_dir: Path, resumed: str, first: dict[str, bool]) -> None:
    events = _jsonl(run_dir / "events.jsonl")
    _assert_seqs_increase(events)
    second = _attempt_completions(events, 2)
    # A replay costs nothing, and the CLI says exactly what the events say.
    assert all(c["tokens"] == 0 for c in second if c["replayed"])
    assert resumed.count("(replayed from the journal)") == sum(c["replayed"] for c in second)
    _assert_only_nulls_rerun({c["label"]: c["replayed"] for c in second}, first)


def _assert_seqs_increase(events: list[dict]) -> None:
    seqs = [e["seq"] for e in events]
    assert all(b > a for a, b in zip(seqs, seqs[1:], strict=False))


def _assert_only_nulls_rerun(replayed: dict[str, bool], first: dict[str, bool]) -> None:
    """A null reruns live; an ok reader (independent of the others) always replays."""
    live = {label for label, again in replayed.items() if not again}
    nulls = {label for label, ok in first.items() if not ok}
    readers = {label for label in first if label.startswith("reader")}
    assert nulls <= live
    assert not (readers - nulls) & live


def _attempt_completions(events: list[dict], attempt: int) -> list[dict]:
    """The agent completions of the ``attempt``-th run_started block."""
    starts = [i for i, e in enumerate(events) if e["kind"] == "run_started"]
    begin = starts[attempt - 1]
    end = starts[attempt] if attempt < len(starts) else len(events)
    return [e["payload"] for e in events[begin:end] if e["kind"] == "agent_completed"]
