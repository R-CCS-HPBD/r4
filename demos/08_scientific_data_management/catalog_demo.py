#!/usr/bin/env python3
"""Lab data catalog demo helpers — upload, poll ingestion, catalog search/read."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Minimal 1×1 PNG (red pixel) used when sample-chart.png is not present.
_MIN_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_SKIP_DIR_NAMES = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})

_DEFAULT_FIXTURE_DIR = Path(__file__).with_name("fixture")


@dataclass(frozen=True)
class FixtureFile:
    """One file in the Blank Project fixture tree."""

    path: Path
    upload_name: str
    expected_modality: str


def ensure_sample_chart(path: Path) -> Path:
    if path.is_file():
        return path
    path.write_bytes(base64.b64decode(_MIN_PNG_B64))
    return path


def load_fixture_manifest(fixture_dir: Path) -> dict:
    manifest_path = fixture_dir / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing fixture manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _minimal_pdf_bytes(title: str) -> bytes:
    body = f"%PDF-1.4\n% {title}\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    return body.encode("utf-8")


def ensure_blank_project_fixture(fixture_dir: Path | None = None) -> Path:
    """Create missing Blank Project fixture files (15 minimal PDFs, README)."""
    root = fixture_dir or _DEFAULT_FIXTURE_DIR
    manifest = load_fixture_manifest(root)
    paper_count = int(manifest.get("paper_count", 15))
    pattern = str(manifest.get("paper_name_pattern", "gcmc-mof-paper-{n:02d}.pdf"))

    papers_dir = root / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, paper_count + 1):
        name = pattern.format(n=index)
        paper_path = papers_dir / name
        if not paper_path.is_file():
            paper_path.write_bytes(
                _minimal_pdf_bytes(f"GCMC MOF yield study paper {index:02d}")
            )

    readme_path = root / "README.md"
    if not readme_path.is_file():
        readme_path.write_text(
            "# Blank Project (Demo 08)\n\n"
            "GCMC MOF yield research — papers, notebooks, code, and lab CSV.\n",
            encoding="utf-8",
        )
    return root


def collect_blank_project_files(fixture_dir: Path) -> list[FixtureFile]:
    """Walk the fixture tree and return uploadable files with expected modalities."""
    ensure_blank_project_fixture(fixture_dir)
    files: list[FixtureFile] = []
    for path in sorted(fixture_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(fixture_dir).as_posix()
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(fixture_dir).parts):
            continue
        if relative == "MANIFEST.json":
            continue

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            modality = "document"
        elif suffix == ".ipynb":
            modality = "notebook"
        elif suffix in {".py", ".c", ".cpp", ".rs", ".go"}:
            modality = "code"
        elif suffix in {".csv", ".tsv", ".json"}:
            modality = "structured"
        elif suffix in {".md", ".txt"}:
            modality = "text"
        elif suffix == ".png":
            modality = "image"
        else:
            modality = "binary"
        files.append(FixtureFile(path=path, upload_name=relative, expected_modality=modality))
    return files


def upload_lab_files(csv_path: Path, png_path: Path, *, prefix: str = "demo-08") -> list[dict]:
    from r4.vision import VisionClient

    vc = VisionClient()
    uploads: list[dict] = []
    for file_path, name in ((csv_path, "batch-3-yields.csv"), (png_path, "sample-chart.png")):
        key = f"uploads/{prefix}/{name}"
        payload = vc.upload_file(file_path, purpose="lab-data", key=key)
        uploads.append(payload)
        print(f"[upload] {name} -> {payload.get('uri') or payload.get('key')}")
    return uploads


def upload_blank_project_files(
    fixture_dir: Path,
    *,
    prefix: str = "demo-08-blank",
) -> list[dict]:
    """Upload the full Blank Project artifact mix through the gateway."""
    from r4.vision import VisionClient

    vc = VisionClient()
    uploads: list[dict] = []
    for item in collect_blank_project_files(fixture_dir):
        key = f"uploads/{prefix}/{item.upload_name}"
        payload = vc.upload_file(item.path, purpose="lab-data", key=key)
        payload["_expected_modality"] = item.expected_modality
        payload["_upload_name"] = item.upload_name
        uploads.append(payload)
        print(f"[upload] {item.upload_name} ({item.expected_modality}) -> {payload.get('key')}")
    print(f"[upload] {len(uploads)} Blank Project files uploaded under uploads/{prefix}/")
    return uploads


def upload_matchers(uploads: list[dict]) -> list[str]:
    """Build s3_key markers from upload responses for catalog hit correlation."""
    matchers: list[str] = []
    for payload in uploads:
        key = str(payload.get("key") or "")
        if not key:
            continue
        matchers.append(key)
        upload_name = str(payload.get("_upload_name") or "")
        if upload_name:
            matchers.append(upload_name)
        if "/" in key:
            matchers.append(key.split("/", 1)[1])
    return matchers


def _hit_matches_upload(hit: dict, matchers: list[str]) -> bool:
    s3_key = str(hit.get("s3_key") or "")
    return any(s3_key == marker or s3_key.endswith(marker) for marker in matchers)


def assert_csv_preview(text: str, *, expected_batch: str = "batch-3") -> None:
    """Raise ValueError when catalog read preview lacks expected CSV signals."""
    required = ("batch_id,yield_pct", expected_batch, "85")
    missing = [item for item in required if item not in text]
    if missing:
        raise ValueError(f"catalog read missing expected content: {missing}")


def assert_modality_coverage(hits: list[dict], expected: set[str]) -> None:
    """Raise ValueError when ready catalog hits do not cover expected modalities."""
    found = {str(hit.get("detected_modality") or "") for hit in hits}
    found.discard("")
    missing = expected - found
    if missing:
        raise ValueError(
            f"catalog missing modalities {sorted(missing)}; found {sorted(found)}"
        )


def wait_catalog_ready(
    client,
    query: str,
    *,
    upload_matchers: list[str] | None = None,
    timeout_sec: int = 120,
    poll_sec: float = 3.0,
    min_ready: int = 1,
) -> list[dict]:
    deadline = time.time() + timeout_sec
    last_states: list[str] = []
    while time.time() < deadline:
        data = client.get("/gw/catalog/search", params={"q": query, "limit": 100})
        results = data.get("results", [])
        last_states = [str(r.get("state", "?")) for r in results]
        all_ready = [r for r in results if r.get("state") == "ready"]
        ready = all_ready
        if upload_matchers:
            ready = [r for r in all_ready if _hit_matches_upload(r, upload_matchers)]
        if len(ready) >= min_ready:
            print(f"[catalog] {len(ready)} ready hit(s) for q={query!r}")
            return ready
        if upload_matchers and all_ready:
            print(
                f"[catalog] {len(all_ready)} ready hit(s) unrelated to this upload; waiting..."
            )
        elif [r for r in results if r.get("state") == "pending"]:
            pending = [r for r in results if r.get("state") == "pending"]
            print(f"[catalog] waiting ({len(pending)} pending, states={last_states})...")
        else:
            print(f"[catalog] no hits yet for q={query!r}; retrying...")
        time.sleep(poll_sec)
    print(
        f"[catalog] timed out after {timeout_sec}s (last states: {last_states or 'none'})",
        file=sys.stderr,
    )
    return []


def wait_blank_project_ready(
    client,
    uploads: list[dict],
    manifest: dict,
    *,
    timeout_sec: int = 300,
    poll_sec: float = 3.0,
) -> list[dict]:
    """Poll until all uploaded Blank Project files reach catalog ``ready`` state."""
    matchers = upload_matchers(uploads)
    expected_count = len(uploads)
    queries = manifest.get("search_queries", {})
    search_terms = list(dict.fromkeys(queries.values())) or ["blank"]

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        combined: dict[str, dict] = {}
        for term in search_terms:
            data = client.get("/gw/catalog/search", params={"q": term, "limit": 100})
            for hit in data.get("results", []):
                if hit.get("state") != "ready":
                    continue
                if not _hit_matches_upload(hit, matchers):
                    continue
                combined[str(hit.get("s3_key"))] = hit

        ready_count = len(combined)
        if ready_count >= expected_count:
            print(f"[catalog] all {ready_count}/{expected_count} uploads ready")
            return list(combined.values())

        pending = expected_count - ready_count
        print(f"[catalog] waiting for ingestion ({ready_count}/{expected_count} ready)...")
        time.sleep(poll_sec)

    print(
        f"[catalog] timed out: only {len(combined)}/{expected_count} uploads ready",
        file=sys.stderr,
    )
    return list(combined.values())


def catalog_read(client, s3_key: str, *, length: int = 4096) -> dict:
    return client.get(
        "/gw/catalog/read",
        params={"s3_key": s3_key, "offset": 0, "length": length},
    )


def run_upload_and_catalog(
    csv_path: Path,
    png_path: Path,
    query: str,
    timeout_sec: int,
    *,
    prefix: str = "demo-08",
) -> int:
    from r4.client import Client

    png_path = ensure_sample_chart(png_path)
    uploads = upload_lab_files(csv_path, png_path, prefix=prefix)
    matchers = upload_matchers(uploads)
    client = Client()
    hits = wait_catalog_ready(
        client,
        query,
        upload_matchers=matchers,
        timeout_sec=timeout_sec,
    )
    if not hits:
        return 1

    csv_hits = [h for h in hits if h.get("s3_key", "").endswith(".csv")]
    target = csv_hits[0] if csv_hits else hits[0]
    s3_key = target["s3_key"]
    print(f"[catalog] top hit: {s3_key} (score={target.get('relevance_score')})")
    if target.get("deterministic_summary"):
        print(f"[catalog] summary: {target['deterministic_summary']}")

    payload = catalog_read(client, s3_key)
    text = payload.get("text") or ""
    print(
        f"[read] bytes {payload.get('offset', 0)}-"
        f"{payload.get('offset', 0) + payload.get('length', 0)} "
        f"truncated={payload.get('truncated')}"
    )
    if not text.strip():
        print("[read] empty preview", file=sys.stderr)
        return 1
    try:
        assert_csv_preview(text)
    except ValueError as exc:
        print(f"[read] {exc}", file=sys.stderr)
        return 1
    print("[read] preview:")
    print(text[:500])
    return 0


def run_blank_project_catalog(
    fixture_dir: Path,
    timeout_sec: int,
    *,
    prefix: str = "demo-08-blank",
) -> int:
    from r4.client import Client

    fixture_dir = ensure_blank_project_fixture(fixture_dir)
    manifest = load_fixture_manifest(fixture_dir)
    uploads = upload_blank_project_files(fixture_dir, prefix=prefix)
    client = Client()
    hits = wait_blank_project_ready(
        client,
        uploads,
        manifest,
        timeout_sec=timeout_sec,
    )
    if len(hits) < len(uploads):
        return 1

    expected_modalities = set(manifest.get("expected_modalities", []))
    try:
        assert_modality_coverage(hits, expected_modalities)
    except ValueError as exc:
        print(f"[catalog] {exc}", file=sys.stderr)
        return 1

    queries = manifest.get("search_queries", {})
    matchers = upload_matchers(uploads)

    csv_query = str(queries.get("csv", "batch-3"))
    csv_hits = wait_catalog_ready(
        client,
        csv_query,
        upload_matchers=matchers,
        timeout_sec=min(30, timeout_sec),
        min_ready=1,
    )
    csv_hit = next((h for h in csv_hits if h.get("s3_key", "").endswith(".csv")), None)
    if csv_hit is None:
        print("[catalog] no CSV hit for raw read", file=sys.stderr)
        return 1

    payload = catalog_read(client, csv_hit["s3_key"])
    text = payload.get("text") or ""
    try:
        assert_csv_preview(text)
    except ValueError as exc:
        print(f"[read] {exc}", file=sys.stderr)
        return 1
    print("[read] CSV preview:")
    print(text[:400])

    code_query = str(queries.get("code", "torch"))
    code_hits = wait_catalog_ready(
        client,
        code_query,
        upload_matchers=matchers,
        timeout_sec=min(30, timeout_sec),
        min_ready=1,
    )
    code_hit = next((h for h in code_hits if h.get("detected_modality") == "code"), None)
    if code_hit:
        print(f"[catalog] code hit: {code_hit['s3_key']}")
    else:
        print("[catalog] warning: no code modality hit (non-fatal)", file=sys.stderr)

    notebook_query = str(queries.get("notebook", "analysis"))
    nb_hits = wait_catalog_ready(
        client,
        notebook_query,
        upload_matchers=matchers,
        timeout_sec=min(30, timeout_sec),
        min_ready=1,
    )
    nb_hit = next((h for h in nb_hits if h.get("detected_modality") == "notebook"), None)
    if nb_hit:
        print(f"[catalog] notebook hit: {nb_hit['s3_key']}")
    else:
        print("[catalog] warning: no notebook modality hit (non-fatal)", file=sys.stderr)

    print(f"[catalog] modalities present: {sorted({h.get('detected_modality') for h in hits})}")
    return 0


def dry_run_blank_project(fixture_dir: Path) -> dict:
    """Validate Blank Project fixture without calling the gateway."""
    root = ensure_blank_project_fixture(fixture_dir)
    manifest = load_fixture_manifest(root)
    files = collect_blank_project_files(root)
    paper_count = sum(1 for item in files if item.expected_modality == "document")
    return {
        "dry_run": True,
        "mode": "blank",
        "fixture_dir": str(root),
        "file_count": len(files),
        "paper_count": paper_count,
        "expected_modalities": manifest.get("expected_modalities", []),
        "modalities": sorted({item.expected_modality for item in files}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scientific data catalog demo helper")
    parser.add_argument(
        "--mode",
        choices=("blank", "legacy"),
        default=os.environ.get("R4_DEMO_CATALOG_MODE", "blank"),
        help="blank = RC1 artifact mix; legacy = CSV + PNG only",
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=Path(__file__).with_name("fixture"),
        help="Blank Project fixture directory",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path(__file__).with_name("batch-3-yields.csv"),
        help="CSV lab file to upload (legacy mode)",
    )
    parser.add_argument(
        "--png",
        type=Path,
        default=Path(__file__).with_name("sample-chart.png"),
        help="PNG lab file to upload (legacy mode; created if missing)",
    )
    parser.add_argument(
        "--query",
        default="batch-3",
        help="Catalog search query after ingestion (legacy mode)",
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("R4_DEMO_CATALOG_PREFIX", "demo-08-blank"),
        help="Upload key prefix under uploads/ (env: R4_DEMO_CATALOG_PREFIX)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("R4_DEMO_CATALOG_TIMEOUT_SEC", "300")),
        help="Seconds to wait for catalog ready state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs only; do not call the gateway",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        if args.mode == "blank":
            summary = dry_run_blank_project(args.fixture_dir)
            print(json.dumps(summary, indent=2))
            if summary["paper_count"] < 15:
                print("[dry-run] expected 15 papers", file=sys.stderr)
                return 1
            return 0
        ensure_sample_chart(args.png)
        if not args.csv.is_file():
            print(f"Missing CSV: {args.csv}", file=sys.stderr)
            return 1
        print(json.dumps({"dry_run": True, "mode": "legacy", "csv": str(args.csv), "png": str(args.png)}))
        return 0

    if args.mode == "blank":
        return run_blank_project_catalog(
            args.fixture_dir,
            args.timeout,
            prefix=args.prefix,
        )

    if not args.csv.is_file():
        print(f"Missing CSV: {args.csv}", file=sys.stderr)
        return 1

    return run_upload_and_catalog(
        args.csv,
        args.png,
        args.query,
        args.timeout,
        prefix=args.prefix,
    )


if __name__ == "__main__":
    raise SystemExit(main())
