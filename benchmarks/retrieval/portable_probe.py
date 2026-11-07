"""ADR-093 S8: portable chunk text (D-12), the bundle and the import.

The S8 gate (ADR-093, "Slices, components and gates") reads: "md5 100%.
Import retrieval within 0.01 nDCG. Without the flag the bundle has the v1.2
shape. Offline chunk count matches the export. v1.3 r@5 >= v1.2."

    P=".venv/bin/python benchmarks/retrieval/portable_probe.py"
    # on the SOURCE installation (XDG_CONFIG_HOME logged into it)
    $P export --project default --chunk-text --out ~/s8-bundles   # r4-kb v1.3
    $P export --project default --out ~/s8-bundles                # r4-kb v1.2
    # offline, no stack: md5, the v1.2 shape, the offline reader's count
    $P verify --v13 ~/s8-bundles/default-v13.kb.tar.gz --v12 ~/s8-bundles/default-v12.kb.tar.gz
    # on the SECOND installation (s8_second_install.sh; its own r4 config)
    $P import ~/s8-bundles/default-v13.kb.tar.gz --target-compose-project ai4s-s8
    # ... run.py retrieve on each side, then the gate over the files:
    $P gate --verify <verify.json> --source <dense source> --v13 <dense v13> --v12 <dense v12>

``s8_second_install.sh steps`` prints the whole sequence in order, with the
steps only the maintainer may take named.

Definitions (recorded in every result file):

- **md5**: every chunk text the v1.3 bundle carries hashes to its node's
  ``content.chunk_md5``, the manifest says ``1.3`` and ``chunk_text: true``,
  and the export left no chunk of an exported artifact without text
  (``chunk_text_summary.missing == 0``, ``truncated == 0``). Chunks of
  artifacts that are not active (orphans, wave F) carry no text by design and
  are reported as ``no_artifact``.
- **v1.2 shape**: the flag-less bundle says ``1.2``, carries no
  ``chunk_text``/``chunk_text_summary`` manifest key and no ``text``,
  ``text_header`` or ``text_truncated`` on any node, has the v1.3 manifest's
  keys and tables minus the v1.3 additions, its chunk rows have the v1.3 chunk
  rows' keys minus the text keys, it holds the same chunk nodes, and it
  carries no raw objects (none were asked for).
- **offline count**: R4's offline reader (``RAGClient.load_bundle``, what
  ``r4 chat --knowledge`` loads) holds as many chunks as the bundle has chunk
  nodes, and as many as the v1.3 manifest says the export wrote; for both
  bundles.
- **import nDCG**: |mean nDCG@10 (v1.3 import) - mean nDCG@10 (source)| <=
  0.01, ``retrieve:dense`` over the 188 dev evidence claims, paired on claim
  ids, both files complete (no error, same claims). Both sides search the
  whole project (``run.py retrieve --graph ""``): a bundle is a project, and
  an import lands in the target project's default graph.
- **r@5**: mean recall@5 of the v1.3 import >= that of the v1.2 import, the
  same arm and pairing rule.

``verify`` also checks that ``artifacts.jsonl`` lists every artifact a chunk
names (``artifacts``): an import restores only listed artifacts, so a short
list breaks the import gate before any import is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402
import comparisons  # noqa: E402
import metrics  # noqa: E402
import run  # noqa: E402

#: The flag-less bundle's version, and the chunk-text one's (ADR-093 D-12).
V12, V13 = "1.2", "1.3"
#: What only a v1.3 bundle may carry: manifest keys and chunk-node keys.
V13_MANIFEST_KEYS = ("chunk_text", "chunk_text_summary")
V13_NODE_KEYS = ("text", "text_header", "text_truncated")
NDCG_TOLERANCE = 0.01
GATE_ARM = "retrieve:dense"
#: KNOWLEDGE_EXPORT job states (umrs ``JobStatus``) that end the wait.
DONE, FAILED = ("completed", "all_complete"), ("failed", "cancelled")
#: The live stand-alone stack's compose project: never an import target.
SOURCE_COMPOSE_PROJECT = "ai4s-standalone"
MAX_LISTED = 20

DEFINITIONS = {
    "md5": (
        "every chunk text of the v1.3 bundle hashes to content.chunk_md5; manifest 1.3 with "
        "chunk_text true; chunk_text_summary.missing == 0 and truncated == 0"
    ),
    "v12_shape": (
        "version 1.2; no chunk_text keys in the manifest and no text keys on any node; "
        "the v1.3 manifest keys, tables and chunk-row keys minus the v1.3 additions; "
        "the same chunk nodes; no raw objects"
    ),
    "offline_count": "RAGClient.load_bundle holds as many chunks as the bundle's chunk nodes",
    "import_ndcg": "|mean nDCG@10 v1.3 import - source| <= 0.01, retrieve:dense, paired, complete",
    "r5": "mean recall@5 of the v1.3 import >= the v1.2 import's, retrieve:dense, paired, complete",
}


class ProbeError(RuntimeError):
    """A step that cannot give a measurement; the message says what to do."""


# ── export (the source installation) ─────────────────────────────────────────


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_name(project: str, chunk_text: bool) -> str:
    return f"{project}-{'v13' if chunk_text else 'v12'}.kb.tar.gz"


def _attempt_detail(job: Any) -> str:
    """The last attempt's reason: its exit reason (where the backend's error
    lands) and its status detail, whichever are set."""
    attempts = getattr(job, "attempts", None) or []
    last = attempts[-1] if attempts else None
    if last is None:
        return "no attempt recorded"
    parts = (getattr(last, "exit_reason", None), getattr(last, "status_detail", None))
    return " — ".join(str(p) for p in parts if p) or "no reason recorded"


#: The maintainer step a failed export names, by what its reason says.
_EXPORT_HINTS = (
    (
        "UMRS_SECRET_KEY",
        "the server has no bundle signing key: run `standalone.sh bootstrap` (it adds "
        "UMRS_SECRET_KEY) and restart umrs, or export with --allow-unsigned",
    ),
    (
        "chunk text",
        "a --chunk-text export of a project with no registered owner and data class "
        "fails closed (ADR-093 D-12): the maintainer registers the project (README, S8)",
    ),
)


def _export_hint(detail: str) -> str:
    for marker, hint in _EXPORT_HINTS:
        if marker.lower() in detail.lower():
            return hint
    return "see the job's attempts (`r4 job status <id>`)"


def wait_for_job(
    client: Any,
    job_id: str,
    *,
    timeout: float,
    poll: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """The export job once it ends; ``ProbeError`` when it fails or outlasts
    ``timeout`` seconds."""
    started = clock()
    while True:
        job = client.jobs.get(job_id)
        status = str(getattr(job, "status", "") or "")
        if status in DONE:
            return job
        if status in FAILED:
            detail = _attempt_detail(job)
            raise ProbeError(f"export job {job_id} {status}: {detail}. {_export_hint(detail)}.")
        if clock() - started > timeout:
            raise ProbeError(f"export job {job_id} still {status!r} after {timeout:.0f} s")
        sleep(poll)


def export_bundle(
    client: Any,
    project: str,
    *,
    chunk_text: bool,
    out_dir: Path,
    allow_unsigned: bool = False,
    timeout: float = 7200.0,
    poll: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """``r4 project export [--chunk-text]`` then ``r4 knowledge
    export-download``: the server's KNOWLEDGE_EXPORT, waited for, and its
    archive downloaded. A client-side fallback (an older server) is refused:
    it is not a server bundle."""
    started = clock()
    receipt = client.knowledge.export(project, chunk_text=chunk_text, allow_unsigned=allow_unsigned)
    if receipt.get("partial") or not receipt.get("archive_key"):
        raise ProbeError(
            f"no server export for {project}: {receipt.get('partial_reason') or receipt}"
        )
    job_id = str(receipt.get("job_id") or receipt.get("bundle_id"))
    wait_for_job(client, job_id, timeout=timeout, poll=poll, sleep=sleep, clock=clock)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / bundle_name(project, chunk_text)
    client.knowledge.download_export(receipt["archive_key"], str(dest))
    return {
        "project": project,
        "chunk_text": chunk_text,
        "unsigned": allow_unsigned,
        "job_id": job_id,
        "archive_key": receipt["archive_key"],
        "seconds": round(clock() - started, 1),
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": sha256_file(dest),
    }


# ── reading a bundle (offline) ───────────────────────────────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    """A JSONB column as exported: a dict, or its JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def is_chunk(row: dict[str, Any]) -> bool:
    return _as_dict(row.get("content")).get("kind") == "chunk"


@dataclass
class Bundle:
    """What the checks read of one bundle."""

    path: Path
    manifest: dict[str, Any]
    chunks: list[dict[str, Any]] = field(default_factory=list)
    artifact_ids: set[str] = field(default_factory=set)
    files: list[str] = field(default_factory=list)
    raw_objects: int = 0

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "")

    def chunk_ids(self) -> set[str]:
        return {str(row.get("node_id") or "") for row in self.chunks}


def bundle_from_dir(path: Path, root: Path) -> Bundle:
    """A bundle read from its (extracted) directory."""
    return Bundle(
        path=Path(path),
        manifest=json.loads((root / "manifest.json").read_text(encoding="utf-8")),
        chunks=[row for row in _jsonl(root / "nodes.jsonl") if is_chunk(row)],
        artifact_ids={
            str(row["artifact_id"])
            for row in _jsonl(root / "artifacts.jsonl")
            if row.get("artifact_id")
        },
        files=sorted(p.name for p in root.iterdir()),
        raw_objects=sum(1 for _row in _jsonl(root / "raw_objects.jsonl")),
    )


def read_bundle(path: Path) -> Bundle:
    """A ``.kb.tar.gz`` archive or a bundle directory, unpacked by r4's own
    reader (hostile-archive safe), read, and the unpacked copy removed."""
    from r4.knowledge_bundle import KnowledgeBundleReader

    reader = KnowledgeBundleReader(Path(path))
    try:
        return bundle_from_dir(Path(path), reader._work_dir)
    finally:
        reader.cleanup()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _text_counts(bundle: Bundle) -> dict[str, Any]:
    """Chunks carrying text, those cut at the export's limit, and the node
    ids whose text does not hash to its ``chunk_md5``."""
    texts = [(row, row.get("text")) for row in bundle.chunks]
    carried = [(row, text) for row, text in texts if isinstance(text, str) and text]
    bad = [
        str(row.get("node_id"))
        for row, text in carried
        if _md5(text) != _as_dict(row.get("content")).get("chunk_md5")
    ]
    truncated = sum(bool(row.get("text_truncated")) for row, _text in carried)
    return {"with_text": len(carried), "bad": bad, "truncated": truncated}


def _whole_export(bundle: Bundle, summary: dict[str, Any]) -> bool:
    """A v1.3 manifest whose export left no exported chunk without text."""
    stamped = bundle.version == V13 and bundle.manifest.get("chunk_text") is True
    return stamped and not summary.get("missing") and not summary.get("truncated")


def md5_check(bundle: Bundle) -> dict[str, Any]:
    """The md5 gate over the v1.3 bundle (see ``DEFINITIONS["md5"]``)."""
    counts = _text_counts(bundle)
    with_text, bad = counts["with_text"], counts["bad"]
    summary = _as_dict(bundle.manifest.get("chunk_text_summary"))
    verified = with_text > 0 and not bad and not counts["truncated"]
    return {
        "chunks": len(bundle.chunks),
        "with_text": with_text,
        "md5_ok": with_text - len(bad),
        "md5_rate": (with_text - len(bad)) / with_text if with_text else None,
        "md5_mismatch": len(bad),
        "mismatched_nodes": bad[:MAX_LISTED],
        "without_text": len(bundle.chunks) - with_text,
        "truncated": counts["truncated"],
        "chunk_text_summary": summary,
        "pass": verified and _whole_export(bundle, summary),
    }


def _chunk_keys(bundle: Bundle) -> set[str]:
    return {key for row in bundle.chunks for key in row}


def _manifest_problems(v12: Bundle, v13: Bundle) -> list[str]:
    problems = []
    if v12.version != V12:
        problems.append(f"version {v12.version!r}, not {V12}")
    leaked = sorted(set(V13_MANIFEST_KEYS) & set(v12.manifest))
    if leaked:
        problems.append(f"manifest carries {leaked}")
    expected = set(v13.manifest) - set(V13_MANIFEST_KEYS)
    if set(v12.manifest) != expected:
        diff = sorted(set(v12.manifest) ^ expected)
        problems.append(f"manifest keys differ from the v1.3 bundle's minus D-12: {diff}")
    tables12 = set(_as_dict(v12.manifest.get("tables")))
    if tables12 != set(_as_dict(v13.manifest.get("tables"))):
        problems.append(f"tables differ: {sorted(tables12)}")
    return problems


def _node_problems(v12: Bundle, v13: Bundle) -> list[str]:
    problems = []
    carrying = sum(1 for row in v12.chunks if any(key in row for key in V13_NODE_KEYS))
    if carrying:
        problems.append(f"{carrying} chunk nodes carry text keys")
    if _chunk_keys(v12) != _chunk_keys(v13) - set(V13_NODE_KEYS):
        diff = sorted(_chunk_keys(v12) ^ (_chunk_keys(v13) - set(V13_NODE_KEYS)))
        problems.append(f"chunk-row keys differ: {diff}")
    if v12.chunk_ids() != v13.chunk_ids():
        problems.append(
            f"chunk nodes differ: {len(v12.chunk_ids() ^ v13.chunk_ids())} in one bundle only"
        )
    if v12.raw_objects:
        problems.append(f"{v12.raw_objects} raw objects, none asked for")
    return problems


def v12_shape(v12: Bundle, v13: Bundle) -> dict[str, Any]:
    """The flag-less bundle has the v1.2 shape (``DEFINITIONS["v12_shape"]``)."""
    problems = _manifest_problems(v12, v13) + _node_problems(v12, v13)
    excerpts = sum(1 for row in v12.chunks if _as_dict(row.get("content")).get("excerpt"))
    return {
        "version": v12.version,
        "chunks": len(v12.chunks),
        "chunks_with_excerpt": excerpts,
        "problems": problems,
        "pass": not problems,
    }


def offline_reader_summary(path: Path) -> dict[str, Any]:
    """What R4's offline reader loads from a bundle (``RAGClient.load_bundle``,
    the path ``r4 chat --knowledge`` and ``R4_KNOWLEDGE_BUNDLE`` take)."""
    from r4.rag import RAGClient

    rag = RAGClient(None)
    rag.load_bundle(Path(path))
    try:
        return rag.local_bundle_summary()
    finally:
        rag.unload_bundle()


def offline_count(bundle: Bundle, offline: dict[str, Any]) -> dict[str, Any]:
    declared = _as_dict(bundle.manifest.get("chunk_text_summary")).get("chunks")
    passed = offline.get("chunks") == len(bundle.chunks) and declared in (None, len(bundle.chunks))
    return {
        "offline_chunks": offline.get("chunks"),
        "exported_chunks": len(bundle.chunks),
        "declared_chunks": declared,
        "text_sources": offline.get("text_sources"),
        "vectors": offline.get("vectors"),
        "space": offline.get("space"),
        "pass": passed,
    }


def artifact_check(bundle: Bundle) -> dict[str, Any]:
    """Does ``artifacts.jsonl`` list every artifact a chunk names? An import
    restores the listed artifacts only."""
    named = {str(_as_dict(r.get("content")).get("artifact_id") or "") for r in bundle.chunks}
    named.discard("")
    unlisted = sorted(named - bundle.artifact_ids)
    declared = _as_dict(_as_dict(bundle.manifest.get("tables")).get("artifacts")).get("count")
    return {
        "listed": len(bundle.artifact_ids),
        "declared": declared,
        "chunk_artifacts": len(named),
        "chunk_artifacts_unlisted": len(unlisted),
        "unlisted_examples": unlisted[:MAX_LISTED],
        "complete": not unlisted and declared in (None, len(bundle.artifact_ids)),
    }


def _artifact_warning(name: str, check: dict[str, Any]) -> list[str]:
    if check["complete"]:
        return []
    return [
        f"{name}: artifacts.jsonl lists {check['listed']} artifacts (manifest: "
        f"{check['declared']}); {check['chunk_artifacts_unlisted']} artifacts its chunks "
        "name have no row, so an import cannot restore them (umrs bundle_export writes one "
        "page of list_artifacts). Fix the export before the import gate is read."
    ]


def verify(v13_path: Path, v12_path: Path, *, offline: Callable = offline_reader_summary) -> dict:
    """The three offline S8 gates, plus the artifact listing."""
    v13, v12 = read_bundle(v13_path), read_bundle(v12_path)
    counts = {
        "v13": offline_count(v13, offline(v13_path)),
        "v12": offline_count(v12, offline(v12_path)),
    }
    artifacts = {"v13": artifact_check(v13), "v12": artifact_check(v12)}
    return {
        "bundles": {
            name: {
                "path": str(b.path),
                "sha256": sha256_file(b.path) if b.path.is_file() else None,
                "version": b.version,
                "files": b.files,
                "manifest": b.manifest,
            }
            for name, b in (("v13", v13), ("v12", v12))
        },
        "gates": {
            "md5": md5_check(v13),
            "v12_shape": v12_shape(v12, v13),
            "offline_count": {**counts, "pass": all(c["pass"] for c in counts.values())},
        },
        "artifacts": artifacts,
        "warnings": _artifact_warning("v13", artifacts["v13"])
        + _artifact_warning("v12", artifacts["v12"]),
    }


# ── import (the second installation) ─────────────────────────────────────────


def check_target(target: str, environ: dict[str, str] | None = None) -> str:
    """The import goes to the second installation only: the shell must be
    the one ``s8_second_install.sh env`` set up, and never the live stack's."""
    env = os.environ if environ is None else environ
    current = env.get("COMPOSE_PROJECT_NAME", "")
    if not target or target == SOURCE_COMPOSE_PROJECT:
        raise ProbeError(f"refusing to import into {target or 'an unnamed'} installation")
    if current != target:
        raise ProbeError(
            f"COMPOSE_PROJECT_NAME is {current or 'unset'}, not {target}: run "
            '`eval "$(benchmarks/retrieval/s8_second_install.sh env)"` first'
        )
    return target


def import_checks(report: dict[str, Any], version: str) -> list[str]:
    """What an import into a fresh installation should not report."""
    warnings = []
    for key in ("nodes_skipped_identical", "nodes_skipped_other_project", "rows_skipped_deduped"):
        if report.get(key):
            warnings.append(f"{key}={report[key]}: the target was not fresh (wipe it first)")
    if version == V13 and report.get("search_text_pending"):
        warnings.append(f"search_text_pending={report['search_text_pending']} from a v1.3 bundle")
    if report.get("degraded"):
        warnings.append(f"degraded: {report.get('degraded_reason')}")
    return warnings


def import_bundle(client: Any, path: Path, *, allow_unsigned: bool, target: str) -> dict[str, Any]:
    """``POST /umrs/knowledge/import`` (``client.knowledge.import_bundle``)."""
    version = read_bundle(path).version
    started = time.monotonic()
    report = client.knowledge.import_bundle(str(path), allow_unsigned=allow_unsigned)
    gateway = getattr(getattr(client, "config", None), "gateway_url", None)
    return {
        "bundle": {"path": str(path), "sha256": sha256_file(path), "version": version},
        "target": {"compose_project": target, "gateway_url": backends.public_url(gateway)},
        "allow_unsigned": allow_unsigned,
        "seconds": round(time.monotonic() - started, 1),
        "report": report,
        "warnings": import_checks(report, version),
    }


# ── the gate over the retrieval files ────────────────────────────────────────


def _retrieval_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in record.get("rows") or [] if "error" in r or "retrieval" in r]


def _file_problems(record: dict[str, Any]) -> list[str]:
    problems = []
    if record.get("kind") != "retrieve" or record.get("arm") != GATE_ARM:
        problems.append(f"{record.get('kind')} {record.get('arm')}, not retrieve {GATE_ARM}")
    if record.get("claims_set") != "dev":
        problems.append(f"claims {record.get('claims_set')}, not dev")
    return problems


def _scored(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {r["claim"]: r for r in rows if "error" not in r}


def _mean_or_none(values: list[float]) -> float | None:
    return metrics.mean(values) if values else None


def paired_mean(a: dict[str, Any], b: dict[str, Any], key: str) -> dict[str, Any]:
    """Mean ``key`` of two retrieve files over the claims both scored, the
    paired delta (b - a) and what the pairing left out."""
    rows_a, rows_b = _retrieval_rows(a), _retrieval_rows(b)
    ok_a, ok_b = _scored(rows_a), _scored(rows_b)
    shared = sorted(set(ok_a) & set(ok_b))
    xs = [ok_a[c]["retrieval"][key] for c in shared]
    ys = [ok_b[c]["retrieval"][key] for c in shared]
    out = comparisons.completeness(rows_a, rows_b)
    out.update(
        n=len(shared),
        a=_mean_or_none(xs),
        b=_mean_or_none(ys),
        paired=metrics.paired_delta(xs, ys) if xs else None,
        file_problems=_file_problems(a) + _file_problems(b),
    )
    out["delta"] = out["b"] - out["a"] if shared else None
    out["usable"] = bool(shared) and out["complete"] and not out["file_problems"]
    return out


def import_ndcg_gate(source: dict[str, Any], v13: dict[str, Any]) -> dict[str, Any]:
    pair = paired_mean(source, v13, "ndcg@10")
    within = pair["delta"] is not None and abs(pair["delta"]) <= NDCG_TOLERANCE + 1e-12
    return {"threshold": NDCG_TOLERANCE, **pair, "pass": pair["usable"] and within}


def r5_gate(v12: dict[str, Any], v13: dict[str, Any]) -> dict[str, Any]:
    pair = paired_mean(v12, v13, "recall@5")
    at_least = pair["delta"] is not None and pair["delta"] >= -1e-12
    return {**pair, "pass": pair["usable"] and at_least}


def s8_gate(
    verified: dict[str, Any], source: dict[str, Any], v13: dict[str, Any], v12: dict[str, Any]
) -> dict[str, Any]:
    """Every S8 gate: the three offline ones from ``verify``, the two
    retrieval ones from the run.py retrieve files."""
    offline = verified.get("gates") or {}
    gates = {
        "md5": {"pass": bool((offline.get("md5") or {}).get("pass"))},
        "v12_shape": {"pass": bool((offline.get("v12_shape") or {}).get("pass"))},
        "offline_count": {"pass": bool((offline.get("offline_count") or {}).get("pass"))},
        "import_ndcg": import_ndcg_gate(source, v13),
        "r5_v13_ge_v12": r5_gate(v12, v13),
    }
    return {
        "gates": gates,
        "diagnostic": {"v12_import_vs_source_ndcg": paired_mean(source, v12, "ndcg@10")},
        "warnings": list(verified.get("warnings") or []),
        "pass": all(g["pass"] for g in gates.values()),
    }


# ── commands ─────────────────────────────────────────────────────────────────


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write(kind: str, body: dict[str, Any], label: str = "", out_dir: Path | None = None) -> Path:
    """``<stamp>-portable-<kind>-s8[-label].json`` under ``results/``."""
    out_dir = out_dir or run.RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    record = {
        "schema_version": run.SCHEMA_VERSION,
        "kind": f"portable-{kind}",
        "slice": "S8",
        "label": label,
        "at": stamp,
        "definitions": DEFINITIONS,
        **body,
    }
    path = out_dir / f"{stamp}-portable-{kind}-s8{f'-{label}' if label else ''}.json"
    path.write_text(json.dumps(record, indent=1, default=str))
    return path


def _client() -> Any:
    from r4.client import Client

    return Client()


def cmd_export(args: argparse.Namespace, client: Any = None) -> int:
    receipt = export_bundle(
        client or _client(),
        args.project,
        chunk_text=args.chunk_text,
        out_dir=Path(args.out).expanduser(),
        allow_unsigned=args.allow_unsigned,
        timeout=args.timeout,
        poll=args.poll,
    )
    Path(f"{receipt['path']}.receipt.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt), flush=True)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    body = verify(Path(args.v13).expanduser(), Path(args.v12).expanduser())
    body["provenance"] = {"code": backends.code_provenance()}
    path = write("verify", body, args.label)
    gates = {name: gate["pass"] for name, gate in body["gates"].items()}
    print(json.dumps({"file": str(path), "gates": gates, "warnings": body["warnings"]}))
    return 0


def cmd_import(args: argparse.Namespace, client: Any = None) -> int:
    target = check_target(args.target_compose_project)
    body = import_bundle(
        client or _client(),
        Path(args.bundle).expanduser(),
        allow_unsigned=args.allow_unsigned,
        target=target,
    )
    path = write("import", body, args.label or f"v{body['bundle']['version']}")
    print(json.dumps({"file": str(path), "warnings": body["warnings"]}))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    files = {"verify": args.verify, "source": args.source, "v13": args.v13, "v12": args.v12}
    loaded = {name: comparisons.load(path) for name, path in files.items()}
    body = s8_gate(loaded["verify"], loaded["source"], loaded["v13"], loaded["v12"])
    body["files"] = files
    path = write("gate", body, args.label)
    gates = {name: gate["pass"] for name, gate in body["gates"].items()}
    print(json.dumps({"file": str(path), "gates": gates, "pass": body["pass"]}))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="KNOWLEDGE_EXPORT on the current r4 config's stack")
    ex.add_argument("--project", default="default")
    ex.add_argument("--chunk-text", action="store_true", help="r4-kb v1.3 (ADR-093 D-12)")
    ex.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="the server's opt-out when it has no signing key (recorded in the receipt)",
    )
    ex.add_argument("--out", required=True, help="directory for <project>-v1x.kb.tar.gz")
    ex.add_argument("--timeout", type=float, default=7200.0, help="seconds to wait for the job")
    ex.add_argument("--poll", type=float, default=15.0)
    ve = sub.add_parser("verify", help="the offline gates (no stack)")
    ve.add_argument("--v13", required=True)
    ve.add_argument("--v12", required=True)
    ve.add_argument("--label", default="")
    im = sub.add_parser("import", help="POST /umrs/knowledge/import on the second installation")
    im.add_argument("bundle")
    im.add_argument("--target-compose-project", required=True, help="e.g. ai4s-s8")
    im.add_argument("--allow-unsigned", action="store_true", help="the server's explicit opt-out")
    im.add_argument("--label", default="")
    ga = sub.add_parser("gate", help="every S8 gate from the verify and retrieve files")
    for name in ("verify", "source", "v13", "v12"):
        ga.add_argument(f"--{name}", required=True)
    ga.add_argument("--label", default="")
    return p


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "export": cmd_export,
    "verify": cmd_verify,
    "import": cmd_import,
    "gate": cmd_gate,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return _COMMANDS[args.cmd](args)
    except ProbeError as exc:
        print(f"S8: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
