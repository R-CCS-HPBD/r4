"""ADR-093 S8 probe (benchmarks/retrieval/portable_probe.py) and the second
installation script (s8_second_install.sh), without a stack.

Bundles are built here in the r4-kb v1.2/v1.3 flat layout the umrs export
writes; the offline reader is R4's own (``RAGClient.load_bundle``). The
script runs against a stub ``standalone.sh`` and a stub container engine in
a scratch checkout: it never reaches a real engine or port of the host.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.retrieval import portable_probe as pp

DOCS = ("101", "102", "103")
SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "retrieval" / "s8_second_install.sh"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def _chunk(doc: str, index: int, *, chunk_text: bool) -> dict:
    text = f"Abstract {doc}, part {index}: the claim is about {doc}."
    row = {
        "node_id": f"node-{doc}-{index}",
        "project_id": "default",
        "label": f"scifact-{doc}.md#{index}",
        "node_type": "chunk",
        "content": {
            "kind": "chunk",
            "artifact_id": f"art-{doc}",
            "chunk_index": index,
            "chunk_start": 0,
            "chunk_end": len(text),
            "chunk_md5": _md5(text),
            "excerpt": text[:500],
        },
        "content_hash": f"h-{doc}-{index}",
        "source_ir": "",
    }
    return {**row, "text": text} if chunk_text else row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _manifest(chunk_text: bool, chunks: int) -> dict:
    tables = {
        name: {"count": count, "file": f"{name}.jsonl"}
        for name, count in (("artifacts", len(DOCS)), ("nodes", chunks + 1), ("edges", 0))
    }
    manifest = {
        "format": "r4-kb",
        "version": pp.V13 if chunk_text else pp.V12,
        "project_id": "default",
        "generator": "umrs",
        "exported_at": "2026-10-11T00:00:00Z",
        "embedding": {"model": "e5-base-v2", "dimensions": 3, "pending": False},
        "tables": tables,
    }
    if chunk_text:
        summary = dict.fromkeys(("artifact", "missing", "no_artifact", "truncated"), 0)
        summary |= {"chunks": chunks, "search_text": chunks, "max_chars": 4096}
        manifest |= {"chunk_text": True, "chunk_text_summary": summary}
    return manifest


def make_bundle(root: Path, *, chunk_text: bool, edit=None) -> Path:
    """A flat r4-kb bundle directory: 3 artifacts, 2 chunks each, an entity
    node, a vector per chunk. ``edit(rows, manifest)`` may change it first."""
    root.mkdir(parents=True)
    chunks = [_chunk(doc, i, chunk_text=chunk_text) for doc in DOCS for i in range(2)]
    entity = {"node_id": "ent-1", "project_id": "default", "content": {"kind": "entity"}}
    manifest = _manifest(chunk_text, len(chunks))
    if edit is not None:
        edit(chunks, manifest)
    (root / "manifest.json").write_text(json.dumps(manifest))
    _write_jsonl(root / "nodes.jsonl", [*chunks, entity])
    artifacts = [
        {
            "artifact_id": f"art-{d}",
            "content_hash": "c",
            "metadata": {"source_name": f"scifact-{d}.md"},
        }
        for d in DOCS
    ]
    _write_jsonl(root / "artifacts.jsonl", artifacts)
    vectors = [
        {"node_id": r["node_id"], "model_name": "/models/e5-base-v2", "embedding": [1.0, 0.0, 0.5]}
        for r in chunks
    ]
    _write_jsonl(root / "node_embeddings.jsonl", vectors)
    _write_jsonl(root / "edges.jsonl", [])
    return root


def archive(directory: Path) -> Path:
    """The server's transport form: ``<job>.kb.tar.gz`` holding ``bundle/``."""
    path = directory.with_name(f"{directory.name}.kb.tar.gz")
    with tarfile.open(path, "w:gz") as tar:
        tar.add(directory, arcname="bundle")
    return path


@pytest.fixture
def pair(tmp_path):
    return (
        make_bundle(tmp_path / "v13", chunk_text=True),
        make_bundle(tmp_path / "v12", chunk_text=False),
    )


# ── verify: the offline gates ────────────────────────────────────────────────


GATES = ("md5", "v12_shape", "offline_count")


def _verdicts(got: dict) -> dict:
    return {name: gate["pass"] for name, gate in got["gates"].items()}


def test_a_good_pair_passes_every_offline_gate(pair):
    got = pp.verify(*pair)
    md5, counts = got["gates"]["md5"], got["gates"]["offline_count"]
    observed = {
        "verdicts": _verdicts(got),
        "md5": (md5["chunks"], md5["with_text"], md5["md5_rate"]),
        "v13": {k: counts["v13"][k] for k in ("offline_chunks", "exported_chunks", "vectors")},
        "reader": (counts["v13"]["text_sources"], counts["v13"]["space"]),
        "v12_declared": counts["v12"]["declared_chunks"],
        "artifacts": (got["artifacts"]["v13"]["complete"], got["warnings"]),
        "excerpts": got["gates"]["v12_shape"]["chunks_with_excerpt"],
    }
    assert observed == {
        "verdicts": dict.fromkeys(GATES, True),
        "md5": (6, 6, 1.0),
        "v13": {"offline_chunks": 6, "exported_chunks": 6, "vectors": 6},
        "reader": ({"node": 6}, "e5-base-v2"),
        "v12_declared": None,
        "artifacts": (True, []),
        "excerpts": 6,
    }


def test_the_archives_the_server_ships_verify_the_same_and_leave_nothing(pair, tmp_path):
    v13, v12 = (archive(path) for path in pair)
    got = pp.verify(v13, v12)
    assert all(g["pass"] for g in got["gates"].values())
    assert got["bundles"]["v13"]["sha256"] == pp.sha256_file(v13)
    assert not list(tmp_path.glob(".*.extracted"))


def test_one_chunk_text_that_does_not_hash_to_its_md5_fails_the_md5_gate(tmp_path):
    def corrupt(chunks, _manifest):
        chunks[2]["text"] += " (edited)"

    v13 = make_bundle(tmp_path / "v13", chunk_text=True, edit=corrupt)
    md5 = pp.md5_check(pp.read_bundle(v13))
    assert (md5["md5_mismatch"], md5["mismatched_nodes"], md5["pass"]) == (1, ["node-102-0"], False)


@pytest.mark.parametrize(
    "edit",
    [
        lambda _chunks, m: m["chunk_text_summary"].update(missing=1),
        lambda _chunks, m: m["chunk_text_summary"].update(truncated=1),
        lambda chunks, _m: chunks[0].update(text_truncated=True),
        lambda _chunks, m: m.update(version="1.2"),
        lambda chunks, _m: [c.pop("text") for c in chunks],
    ],
    ids=["missing", "truncated-summary", "truncated-row", "says-1.2", "no-text"],
)
def test_the_md5_gate_also_needs_the_whole_v13_export(tmp_path, edit):
    v13 = make_bundle(tmp_path / "v13", chunk_text=True, edit=edit)
    assert pp.md5_check(pp.read_bundle(v13))["pass"] is False


def test_chunks_of_inactive_artifacts_carry_no_text_and_do_not_fail_md5(tmp_path):
    def orphan(chunks, manifest):
        chunks[0].pop("text")
        manifest["chunk_text_summary"]["no_artifact"] = 1

    md5 = pp.md5_check(pp.read_bundle(make_bundle(tmp_path / "v13", chunk_text=True, edit=orphan)))
    assert (md5["without_text"], md5["pass"]) == (1, True)


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (lambda chunks, _m: chunks[0].update(text="leaked"), "carry text keys"),
        (lambda _chunks, m: m.update(version="1.3"), "version"),
        (lambda _chunks, m: m.update(chunk_text=False), "manifest carries"),
        (lambda _chunks, m: m.update(extra_key=1), "manifest keys differ"),
        (lambda _chunks, m: m["tables"].pop("edges"), "tables differ"),
        (lambda chunks, _m: chunks[1]["content"].update(kind="note"), "chunk nodes differ"),
        (lambda chunks, _m: chunks[1].update(new_column=1), "chunk-row keys differ"),
    ],
)
def test_the_flag_less_bundle_must_keep_the_v12_shape(tmp_path, edit, problem):
    v13 = pp.read_bundle(make_bundle(tmp_path / "v13", chunk_text=True))
    v12 = pp.read_bundle(make_bundle(tmp_path / "v12", chunk_text=False, edit=edit))
    shape = pp.v12_shape(v12, v13)
    assert shape["pass"] is False and any(problem in p for p in shape["problems"])


def test_raw_objects_in_the_flag_less_bundle_break_its_shape(pair):
    v13, v12 = pair
    _write_jsonl(v12 / "raw_objects.jsonl", [{"content_hash": "a" * 64}])
    shape = pp.v12_shape(pp.read_bundle(v12), pp.read_bundle(v13))
    assert shape["problems"] == ["1 raw objects, none asked for"]


def test_the_offline_count_must_match_the_export_and_the_manifest(pair, tmp_path):
    v13, v12 = pair
    short = pp.verify(v13, v12, offline=lambda _path: {"chunks": 5})
    assert short["gates"]["offline_count"]["pass"] is False

    def declare_seven(_chunks, manifest):
        manifest["chunk_text_summary"]["chunks"] = 7

    other = make_bundle(tmp_path / "v13b", chunk_text=True, edit=declare_seven)
    assert pp.verify(other, v12)["gates"]["offline_count"]["v13"]["pass"] is False


def test_a_short_artifact_list_is_named_before_any_import(pair):
    v13, v12 = pair
    rows = [json.loads(line) for line in (v13 / "artifacts.jsonl").read_text().splitlines()]
    _write_jsonl(v13 / "artifacts.jsonl", rows[:1])
    got = pp.verify(v13, v12)
    check = got["artifacts"]["v13"]
    assert (check["listed"], check["declared"], check["chunk_artifacts_unlisted"]) == (1, 3, 2)
    assert check["complete"] is False and "artifacts.jsonl lists 1" in got["warnings"][0]


# ── export: the source installation ──────────────────────────────────────────


class FakeKnowledge:
    def __init__(self, receipt: dict) -> None:
        self.receipt, self.exports, self.imports = receipt, [], []
        self.unsigned: list[bool] = []

    def export(self, project, *, chunk_text=False, allow_unsigned=False):
        self.exports.append((project, chunk_text))
        self.unsigned.append(allow_unsigned)
        return dict(self.receipt)

    def download_export(self, key, dest):
        Path(dest).write_bytes(f"archive {key}".encode())
        return dest

    def import_bundle(self, path, *, allow_unsigned=False):
        self.imports.append((path, allow_unsigned))
        return {"node_count": 7, "search_text_from_node": 6, "nodes_skipped_identical": 0}


def _client(statuses, receipt=None):
    receipt = receipt or {
        "job_id": "job-1",
        "status": "processing",
        "archive_key": "default/k.kb.tar.gz",
    }
    queue = list(statuses)

    def get(_job_id):
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        attempt = SimpleNamespace(
            status_detail="failed",
            exit_reason="ChunkTextRefused: chunk text of 'default' refused: no registry row",
        )
        return SimpleNamespace(status=status, attempts=[attempt])

    return SimpleNamespace(
        knowledge=FakeKnowledge(receipt),
        jobs=SimpleNamespace(get=get),
        config=SimpleNamespace(gateway_url="http://user:secret@localhost:18080/?token=x"),
    )


def _ticks():
    now = [0.0]

    def clock():
        now[0] += 1.0
        return now[0]

    return clock


def test_export_waits_for_the_job_and_downloads_its_archive(tmp_path):
    client = _client(["queued", "running", "completed"])
    got = pp.export_bundle(
        client, "default", chunk_text=True, out_dir=tmp_path, sleep=lambda _s: None, clock=_ticks()
    )
    assert client.knowledge.exports == [("default", True)]
    assert Path(got["path"]).name == "default-v13.kb.tar.gz" and got["job_id"] == "job-1"
    assert got["sha256"] == pp.sha256_file(Path(got["path"]))


def test_a_failed_export_names_the_maintainer_step(tmp_path):
    with pytest.raises(pp.ProbeError, match="registers the project"):
        pp.export_bundle(
            _client(["running", "failed"]),
            "default",
            chunk_text=True,
            out_dir=tmp_path,
            sleep=lambda _s: None,
            clock=_ticks(),
        )


def test_an_export_that_never_ends_times_out(tmp_path):
    with pytest.raises(pp.ProbeError, match="still 'running'"):
        pp.export_bundle(
            _client(["running"]),
            "default",
            chunk_text=False,
            out_dir=tmp_path,
            timeout=3,
            sleep=lambda _s: None,
            clock=_ticks(),
        )


def test_a_client_side_fallback_is_not_a_server_bundle(tmp_path):
    fallback = {
        "status": "ready",
        "partial": True,
        "partial_reason": "server-side export unavailable",
    }
    with pytest.raises(pp.ProbeError, match="server-side export unavailable"):
        pp.export_bundle(
            _client(["completed"], fallback), "default", chunk_text=False, out_dir=tmp_path
        )


# ── import: the second installation only ─────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "env", "error"),
    [
        ("ai4s-standalone", {"COMPOSE_PROJECT_NAME": "ai4s-standalone"}, "refusing"),
        ("", {}, "refusing"),
        ("ai4s-s8", {}, "unset"),
        ("ai4s-s8", {"COMPOSE_PROJECT_NAME": "ai4s-standalone"}, "not ai4s-s8"),
    ],
)
def test_the_import_refuses_any_installation_but_the_second(target, env, error):
    with pytest.raises(pp.ProbeError, match=error):
        pp.check_target(target, env)


def test_the_import_records_the_report_without_credentials(pair):
    v13, _v12 = pair
    bundle = archive(v13)
    client = _client(["completed"])
    got = pp.import_bundle(client, bundle, allow_unsigned=False, target="ai4s-s8")
    assert client.knowledge.imports == [(str(bundle), False)]
    assert got["bundle"]["version"] == "1.3" and got["warnings"] == []
    assert got["target"]["gateway_url"] == "http://localhost:18080/"


@pytest.mark.parametrize(
    ("report", "version", "warning"),
    [
        ({"nodes_skipped_other_project": 9}, "1.3", "not fresh"),
        ({"rows_skipped_deduped": 2}, "1.2", "not fresh"),
        ({"search_text_pending": 4}, "1.3", "search_text_pending=4"),
        ({"degraded": True, "degraded_reason": "x"}, "1.2", "degraded"),
    ],
)
def test_an_import_that_was_not_clean_says_so(report, version, warning):
    assert any(warning in w for w in pp.import_checks(report, version))


def test_a_v12_import_may_leave_search_text_to_the_backfill():
    assert pp.import_checks({"search_text_pending": 6}, "1.2") == []


def test_the_import_command_writes_its_record(pair, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pp.run, "RESULTS", tmp_path / "results")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "ai4s-s8")
    bundle = archive(pair[0])
    args = pp.parser().parse_args(["import", str(bundle), "--target-compose-project", "ai4s-s8"])
    assert pp.cmd_import(args, client=_client(["completed"])) == 0
    record = json.loads(Path(json.loads(capsys.readouterr().out)["file"]).read_text())
    assert record["kind"] == "portable-import" and record["label"] == "v1.3"


# ── the gate over the retrieval files ────────────────────────────────────────


def _retrieve(values: dict[int, tuple[float, float]], **over) -> dict:
    rows = [
        {"claim": c, "label": "SUPPORT", "retrieval": {"ndcg@10": n, "recall@5": r}}
        for c, (n, r) in values.items()
    ]
    rows.append({"claim": 999, "label": "NOT_ENOUGH_INFO"})  # a retrieve arm runs NEI too
    return {"kind": "retrieve", "arm": "retrieve:dense", "claims_set": "dev", "rows": rows, **over}


SOURCE = dict.fromkeys(range(10), (0.8, 0.9))
GOOD_OFFLINE = {"gates": {name: {"pass": True} for name in ("md5", "v12_shape", "offline_count")}}


def test_an_import_within_a_hundredth_of_ndcg_passes():
    v13 = dict.fromkeys(range(10), (0.809, 0.9))
    got = pp.s8_gate(GOOD_OFFLINE, _retrieve(SOURCE), _retrieve(v13), _retrieve(v13))
    ndcg = got["gates"]["import_ndcg"]
    assert ndcg["delta"] == pytest.approx(0.009) and ndcg["n"] == 10 and ndcg["pass"] is True
    assert got["gates"]["r5_v13_ge_v12"]["pass"] is True and got["pass"] is True


@pytest.mark.parametrize("value", [0.78, 0.82])
def test_an_import_off_by_more_than_a_hundredth_fails(value):
    v13 = dict.fromkeys(range(10), (value, 0.9))
    got = pp.s8_gate(GOOD_OFFLINE, _retrieve(SOURCE), _retrieve(v13), _retrieve(v13))
    assert got["gates"]["import_ndcg"]["pass"] is False and got["pass"] is False


def test_r5_of_v13_may_not_fall_below_v12():
    v12 = dict.fromkeys(range(10), (0.8, 0.9))
    v13 = {c: (0.8, 0.9 if c else 0.0) for c in range(10)}
    got = pp.s8_gate(GOOD_OFFLINE, _retrieve(SOURCE), _retrieve(v13), _retrieve(v12))
    r5 = got["gates"]["r5_v13_ge_v12"]
    assert r5["delta"] == pytest.approx(-0.09) and r5["pass"] is False


def test_an_errored_claim_or_another_arm_makes_the_retrieval_gates_unusable():
    errored = _retrieve(SOURCE)
    errored["rows"][0] = {"claim": 0, "label": "SUPPORT", "error": "ReadTimeout"}
    got = pp.s8_gate(GOOD_OFFLINE, _retrieve(SOURCE), errored, _retrieve(SOURCE))
    assert got["gates"]["import_ndcg"]["complete"] is False
    assert got["gates"]["import_ndcg"]["pass"] is False
    fused = pp.s8_gate(
        GOOD_OFFLINE, _retrieve(SOURCE), _retrieve(SOURCE, arm="retrieve:fused"), _retrieve(SOURCE)
    )
    assert fused["gates"]["import_ndcg"]["file_problems"] and fused["pass"] is False


def test_a_failed_offline_gate_fails_s8_and_its_warnings_carry_over():
    offline = {
        **GOOD_OFFLINE,
        "gates": {**GOOD_OFFLINE["gates"], "md5": {"pass": False}},
        "warnings": ["short"],
    }
    got = pp.s8_gate(offline, _retrieve(SOURCE), _retrieve(SOURCE), _retrieve(SOURCE))
    assert (
        got["gates"]["md5"]["pass"] is False
        and got["pass"] is False
        and got["warnings"] == ["short"]
    )


def _gate_argv(tmp_path: Path, verify_file: str) -> list[str]:
    argv = ["gate", "--verify", verify_file]
    for name in ("source", "v13", "v12"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(_retrieve(SOURCE)))
        argv += [f"--{name}", str(path)]
    return argv


def test_the_gate_and_verify_commands_write_their_records(pair, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pp.run, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(pp.backends, "code_provenance", lambda: {"stub": True})
    v13, v12 = pair
    assert pp.main(["verify", "--v13", str(v13), "--v12", str(v12)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["gates"] == dict.fromkeys(GATES, True)
    assert pp.main(_gate_argv(tmp_path, verified["file"])) == 0
    out = json.loads(capsys.readouterr().out)
    record = json.loads(Path(out["file"]).read_text())
    observed = (out["pass"], record["kind"], record["slice"], record["definitions"]["import_ndcg"])
    assert observed == (True, "portable-gate", "S8", pp.DEFINITIONS["import_ndcg"])


def test_a_probe_error_exits_two_with_its_message(capsys, monkeypatch):
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    assert pp.main(["import", "b.kb.tar.gz", "--target-compose-project", "ai4s-s8"]) == 2
    assert "COMPOSE_PROJECT_NAME is unset" in capsys.readouterr().err


# ── s8_second_install.sh, on a stub checkout and a stub engine ───────────────


@pytest.fixture
def stub_repo(tmp_path):
    """A checkout whose standalone.sh and engine only log what they are asked."""
    repo = tmp_path / "repo"
    standalone = repo / "deploy" / "standalone"
    standalone.mkdir(parents=True)
    (repo / "umrs" / "src" / "umrs").mkdir(parents=True)
    log = tmp_path / "calls.log"
    script = standalone / "standalone.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "standalone ${COMPOSE_PROJECT_NAME} ${STANDALONE_PROFILE} '
        '${STANDALONE_PROFILES} $*" >>"$S8_TEST_LOG"\n'
    )
    engine = tmp_path / "engine"
    engine.write_text(
        "#!/usr/bin/env bash\n"
        'echo "engine $*" >>"$S8_TEST_LOG"\n'
        '[[ "$1 $2 $3" != "image inspect ai4s-s8-"* ]]\n'  # only the new tags are missing
    )
    for path in (script, engine):
        path.chmod(0o755)
    (standalone / "compose.yaml").write_text('ports: ["127.0.0.1:8080:8080"]\n')
    (standalone / "compose.dev.yaml").write_text('ports: ["127.0.0.1:5432:5432"]\n')
    return SimpleNamespace(root=repo, log=log, engine=engine, compose=standalone)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _script(stub, *args, port=None, env_file=None):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(stub.root.parent),
        "ADR093_REPO_ROOT": str(stub.root),
        "S8_TEST_LOG": str(stub.log),
        "STANDALONE_ENGINE": str(stub.engine),
        "S8_SOURCE_GATEWAY_PORT": str(port or _free_port()),
    }
    if env_file is not None:
        env["S8_ENV_FILE"] = str(env_file)
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )
    calls = stub.log.read_text().splitlines() if stub.log.exists() else []
    return proc, calls


def test_up_brings_up_a_second_compose_project_reusing_the_source_images(stub_repo):
    proc, calls = _script(stub_repo, "up")
    assert proc.returncode == 0, proc.stderr
    expected = [
        "standalone ai4s-s8 dev core bootstrap",
        "standalone ai4s-s8 dev core up",
        "engine tag ai4s-standalone-gateway-sidecar ai4s-s8-gateway-sidecar",
    ]
    missing = [call for call in expected if call not in calls]
    minted = [call for call in calls if " token" in call]
    # the token is the maintainer's: named, never minted here
    named = (
        "(M) Log an ISOLATED r4 config" in proc.stdout,
        "token scientist default" in proc.stdout,
    )
    assert (missing, minted, named) == ([], [], (True, True))
    assert "sequential ports" in proc.stderr


def test_up_refuses_while_the_source_stack_holds_the_fixed_ports(stub_repo):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        proc, calls = _script(stub_repo, "up", port=busy.getsockname()[1])
    assert proc.returncode != 0 and "sequential mode" in proc.stderr
    assert "(M) Stop the source stack first" in proc.stderr and calls == []


def test_up_runs_in_parallel_once_compose_reads_the_port_names(stub_repo):
    (stub_repo.compose / "compose.yaml").write_text(
        'ports: ["127.0.0.1:${STANDALONE_GATEWAY_PORT:-8080}:8080"]\n'
    )
    (stub_repo.compose / "compose.dev.yaml").write_text(
        'ports: ["${STANDALONE_POSTGRES_PORT:-5432}:5432"]\n'
    )
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        proc, _calls = _script(stub_repo, "up", port=busy.getsockname()[1])
    assert proc.returncode == 0, proc.stderr
    assert "parallel ports" in proc.stderr and "http://localhost:18080" in proc.stdout


def test_wipe_needs_yes_and_then_resets_only_the_second_project(stub_repo):
    proc, calls = _script(stub_repo, "wipe")
    assert proc.returncode != 0 and "--yes" in proc.stderr and calls == []
    proc, calls = _script(stub_repo, "wipe", "--yes")
    assert proc.returncode == 0 and calls == ["standalone ai4s-s8 dev core reset --yes"]


def test_the_script_refuses_the_source_stacks_project_name(stub_repo, tmp_path):
    env_file = tmp_path / "bad.env"
    env_file.write_text("COMPOSE_PROJECT_NAME=ai4s-standalone\n")
    for cmd in (["env"], ["up"], ["down"], ["wipe", "--yes"]):
        proc, calls = _script(stub_repo, *cmd, env_file=env_file)
        assert proc.returncode != 0 and "refusing" in proc.stderr and calls == []


def test_env_and_steps_print_the_run_without_touching_anything(stub_repo):
    proc, calls = _script(stub_repo, "env")
    assert proc.returncode == 0 and calls == []
    assert "export COMPOSE_PROJECT_NAME=ai4s-s8" in proc.stdout
    proc, calls = _script(stub_repo, "steps")
    assert proc.returncode == 0 and calls == []
    steps = proc.stdout
    for needle in (
        "export --project default --chunk-text",
        "verify --v13",
        '--graph "" --label s8-source',
        "import ",
        "--target-compose-project ai4s-s8",
        "wipe --yes",
        "gate --verify",
        "set_data_class",
    ):
        assert needle in steps
    assert steps.index("s8-v13") < steps.index("wipe --yes") < steps.index("s8-v12")


def test_the_override_file_carries_no_secret():
    text = (SCRIPT.parent / "s8_second_install.env").read_text()
    keys = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert "COMPOSE_PROJECT_NAME" in keys
    assert not any(word in key for key in keys for word in ("PASSWORD", "SECRET", "TOKEN", "KEY"))


def test_an_unsigned_export_is_asked_for_and_recorded(tmp_path):
    client = _client(["completed"])
    got = pp.export_bundle(
        client,
        "default",
        chunk_text=False,
        out_dir=tmp_path,
        allow_unsigned=True,
        sleep=lambda _s: None,
        clock=_ticks(),
    )
    assert client.knowledge.unsigned == [True] and got["unsigned"] is True


@pytest.mark.parametrize(
    ("reason", "hint"),
    [
        (
            "backend_submission_failed: bundle signing key is not configured (UMRS_SECRET_KEY)",
            "bootstrap",
        ),
        (
            "ChunkTextRefused: project 'p' is sensitive: its chunk text stays",
            "registers the project",
        ),
        ("something else", "r4 job status"),
    ],
)
def test_a_failed_export_names_its_own_cause(reason, hint):
    attempt = SimpleNamespace(exit_reason=reason, status_detail="failed")
    job = SimpleNamespace(status="failed", attempts=[attempt])
    client = SimpleNamespace(jobs=SimpleNamespace(get=lambda _id: job))
    with pytest.raises(pp.ProbeError, match=hint) as raised:
        pp.wait_for_job(client, "job-1", timeout=10, poll=1, sleep=lambda _s: None)
    assert reason in str(raised.value)
