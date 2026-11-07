"""Build the public export tree of R4 and gate it (repo-rename plan P5a).

    python -m scripts.public_export.export --ref main --out /tmp/r4-export \\
        --report /tmp/r4-export-report.json

The export is a snapshot of one commit of the private development repository
(mnagaso/r4) without the paths in ``exclude.txt``; the private CI workflows
are replaced by ``public-workflows/``; ``public-overlay/`` is copied over the tree (public README, CONTRIBUTING); the ``umrs`` submodule stays a pointer
at the same commit with the public URL; a Markdown link into an excluded path
becomes its plain text. The tree is then gated:

- ``LICENSE`` is present;
- no file is larger than ``--max-file-mb``;
- no internal-detail pattern (``private-patterns.txt``) outside ``allow-patterns.txt``;
- no gitleaks finding (``gitleaks.toml`` holds the reviewed allowlist).

This script never touches a remote and never prints matched text, only paths
and pattern names. Pushing is the job of ``.github/workflows/public-export.yaml``
and happens only after every gate passed (AGENTS.md rule 7).
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC_UMRS_URL = "https://github.com/UMRS-org/umrs.git"
# The internal-detail patterns (internal subnets, home and cluster paths, group
# ids, personal names) are site facts themselves, so they live in
# private-patterns.txt, which exclude.txt keeps out of the export. A gate run
# without any pattern fails (a missing file must never pass silently).
PATTERNS_FILE = "private-patterns.txt"


def load_patterns(config: Path) -> dict[str, re.Pattern[str]]:
    """'<name> <regex>' lines of private-patterns.txt."""
    patterns = {}
    for line in load_list(config / PATTERNS_FILE):
        name, _, rx = line.partition(" ")
        patterns[name] = re.compile(rx.strip())
    return patterns


def _git_env() -> dict[str, str]:
    """The caller's environment without GIT_* (a hook's GIT_INDEX_FILE etc.)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=_git_env()
    ).stdout


def load_list(path: Path) -> list[str]:
    """Non-empty, non-comment lines of a list file."""
    if not path.is_file():
        return []
    lines = (ln.split("#", 1)[0].strip() for ln in path.read_text().splitlines())
    return [ln for ln in lines if ln]


def is_excluded(path: str, rules: list[str]) -> bool:
    """A rule ending in '/' drops that directory; otherwise an exact path or glob."""
    for rule in rules:
        if rule.endswith("/") and (path + "/").startswith(rule):
            return True
        if fnmatch.fnmatchcase(path, rule):
            return True
    return False


def tracked_paths(repo: Path, ref: str) -> list[tuple[str, str]]:
    """(mode, path) for every entry of `ref`, gitlinks included."""
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", ref).decode()
    entries = []
    for item in filter(None, out.split("\0")):
        meta, path = item.split("\t", 1)
        entries.append((meta.split()[0], path))
    return entries


def extract(repo: Path, ref: str, out: Path, keep: list[str]) -> None:
    """Write the kept blobs of `ref` into `out` (git archive, then tar)."""
    # :(literal) — a kept path is a path, never a pathspec glob.
    specs = [f":(literal){p}" for p in keep]
    data = git(repo, "archive", "--format=tar", ref, "--", *specs) if keep else b""
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(out, filter="data")


def replace_workflows(out: Path, public_dir: Path) -> list[str]:
    """Drop every exported workflow, copy the public ones; return their names."""
    target = out / ".github" / "workflows"
    shutil.rmtree(target, ignore_errors=True)
    names = sorted(p.name for p in public_dir.glob("*.y*ml"))
    if names:
        target.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(public_dir / name, target / name)
    return names


def apply_overlay(out: Path, overlay_dir: Path) -> list[str]:
    """Copy every file of public-overlay/ over the export tree (same relative
    paths, replacing what is there): the public README and CONTRIBUTING, while
    the development repository keeps its own. Returns the copied paths."""
    if not overlay_dir.is_dir():
        return []
    copied = []
    for src in sorted(p for p in overlay_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(overlay_dir)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.append(rel.as_posix())
    return copied


# An inline Markdown link or image: [text](target), [text](target "title"), or
# a target followed by loose text such as [plan](plan.md §11).
MD_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)(?:\s+[^)\n]*)?\)")
LINK_BASELINE = "tools/md_link_check_baseline.txt"
URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def link_target(md_rel: str, href: str) -> str | None:
    """The repository path a relative link in `md_rel` points at, or None
    (a URL, an in-page anchor, an absolute path, or a path above the root)."""
    if URL_SCHEME.match(href) or href.startswith(("#", "/")):
        return None
    path = href.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(md_rel), path))
    return None if joined.startswith("..") else joined


def _unlink_text(text: str, md_rel: str, rules: list[str]) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        target = link_target(md_rel, m.group(3))
        if target is None or not is_excluded(target, rules):
            return m.group(0)
        count += 1
        return m.group(2)

    return MD_LINK.sub(repl, text), count


def unlink_excluded(out: Path, rules: list[str]) -> int:
    """Turn every Markdown link into an excluded path into its plain text, so
    the export carries no link the export itself broke. Links that were broken
    already stay links (fix those at the source). Returns how many changed."""
    total = 0
    for md in sorted(out.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="surrogateescape")
        new, count = _unlink_text(text, md.relative_to(out).as_posix(), rules)
        if count:
            md.write_text(new, encoding="utf-8", errors="surrogateescape")
            total += count
    return total


def _baseline_line_kept(out: Path, line: str, rules: list[str]) -> bool:
    if line.startswith("#") or "\t" not in line:
        return True
    src, target = line.split("\t", 1)
    if not (out / src).is_file():
        return False
    resolved = link_target(src, target.strip())
    return resolved is None or not is_excluded(resolved, rules)


def filter_link_baseline(out: Path, rules: list[str]) -> None:
    """Keep only baseline lines about exported files and links the export
    left as links (the rest name excluded paths and no longer apply)."""
    path = out / LINK_BASELINE
    if not path.is_file():
        return
    lines = path.read_text().splitlines()
    kept = [ln for ln in lines if _baseline_line_kept(out, ln, rules)]
    path.write_text("".join(f"{ln}\n" for ln in kept))


def write_gitmodules(out: Path, pin: str | None) -> None:
    if pin is None:
        return
    (out / ".gitmodules").write_text(
        f'[submodule "umrs"]\n\tpath = umrs\n\turl = {PUBLIC_UMRS_URL}\n\tbranch = ai4s-platform\n'
    )


def build(repo: Path, ref: str, out: Path, config: Path) -> dict:
    """Materialize the export tree; return what went in."""
    rules = load_list(config / "exclude.txt")
    entries = tracked_paths(repo, ref)
    gitlinks = {path: mode for mode, path in entries if mode == "160000"}
    kept = [p for mode, p in entries if mode != "160000" and not is_excluded(p, rules)]
    out.mkdir(parents=True, exist_ok=True)
    extract(repo, ref, out, kept)
    workflows = replace_workflows(out, config / "public-workflows")
    overlay = apply_overlay(out, config / "public-overlay")
    unlinked = unlink_excluded(out, rules)
    filter_link_baseline(out, rules)
    pin = git(repo, "rev-parse", f"{ref}:umrs").decode().strip() if "umrs" in gitlinks else None
    write_gitmodules(out, pin)
    commit = git(repo, "rev-parse", f"{ref}^{{commit}}").decode().strip()
    return {
        "commit": commit,
        "files": len(kept),
        "excluded": len(entries) - len(kept) - len(gitlinks),
        "umrs_pin": pin,
        "workflows": workflows,
        "overlay": overlay,
        "unlinked": unlinked,
    }


def iter_files(out: Path):
    for path in sorted(out.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(out).parts:
            yield path, path.relative_to(out).as_posix()


def allowed(rel: str, name: str, allow: list[str]) -> bool:
    """allow-patterns.txt lines: '<glob> <pattern-name|*>'."""
    for line in allow:
        glob, _, pat = line.partition(" ")
        if fnmatch.fnmatchcase(rel, glob) and pat.strip() in ("*", name):
            return True
    return False


def scan_patterns(out: Path, allow: list[str], patterns: dict[str, re.Pattern[str]]) -> list[dict]:
    findings = []
    for path, rel in iter_files(out):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, rx in patterns.items():
            if rx.search(text) and not allowed(rel, name, allow):
                findings.append({"path": rel, "pattern": name})
    return findings


def oversized(out: Path, max_mb: float) -> list[dict]:
    limit = max_mb * 1024 * 1024
    return [
        {"path": rel, "bytes": p.stat().st_size}
        for p, rel in iter_files(out)
        if p.stat().st_size > limit
    ]


# gitleaks.toml uses [[allowlists]] (8.30.1, the CI pin); other builds read it
# differently (an unversioned local build flagged what the pin allows), so a
# scan by any other build is no scan.
MIN_GITLEAKS = (8, 30, 1)


class UnsupportedGitleaks(RuntimeError):
    """The installed gitleaks cannot apply gitleaks.toml as reviewed."""


def gitleaks_version(exe: str) -> tuple[int, ...] | None:
    """(major, minor, patch) from `gitleaks version`, or None for an unversioned build."""
    proc = subprocess.run([exe, "version"], capture_output=True, text=True, env=_git_env())
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout + proc.stderr)
    return tuple(int(x) for x in m.groups()) if m else None


def run_gitleaks(out: Path, config: Path) -> list[dict] | None:
    """Redacted gitleaks findings (rule + path), or None when gitleaks is absent.
    Raises UnsupportedGitleaks for an unversioned build or one older than the pin."""
    exe = shutil.which("gitleaks")
    if exe is None:
        return None
    version = gitleaks_version(exe)
    if version is None or version < MIN_GITLEAKS:
        found = ".".join(map(str, version)) if version else "unversioned build"
        need = ".".join(map(str, MIN_GITLEAKS))
        raise UnsupportedGitleaks(f"gitleaks {found} unsupported (need {need} or newer)")
    report = out.parent / f".{out.name}-gitleaks.json"
    cmd = [
        exe,
        "dir",
        str(out),
        "--redact",
        "--no-banner",
        "--exit-code",
        "0",
        "--config",
        str(config / "gitleaks.toml"),
        "--report-format",
        "json",
        "--report-path",
        str(report),
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=_git_env())
    raw = json.loads(report.read_text() or "[]")
    report.unlink(missing_ok=True)
    return [
        {
            "rule": f.get("RuleID"),
            "path": Path(f.get("File", "")).resolve().relative_to(out.resolve()).as_posix(),
        }
        for f in raw
    ]


def _gitleaks_or_problem(out: Path, config: Path) -> tuple[list[dict] | None, str | None]:
    try:
        return run_gitleaks(out, config), None
    except UnsupportedGitleaks as exc:
        return None, str(exc)


def _failures(
    result: dict, has_patterns: bool, require_gitleaks: bool, problem: str | None
) -> list[str]:
    failures = [] if has_patterns else [f"no patterns loaded ({PATTERNS_FILE} missing or empty)"]
    if not result["license"]:
        failures.append("LICENSE missing")
    for key in ("oversized", "patterns"):
        if result[key]:
            failures.append(f"{len(result[key])} {key} finding(s)")
    return failures + _gitleaks_failures(result["gitleaks"], require_gitleaks, problem)


def _gitleaks_failures(leaks: list | str, require_gitleaks: bool, problem: str | None) -> list[str]:
    if problem:
        return [problem]
    if leaks == "not-run":
        return ["gitleaks not installed"] if require_gitleaks else []
    return [f"{len(leaks)} gitleaks finding(s)"] if leaks else []


def gate(out: Path, config: Path, max_mb: float, require_gitleaks: bool) -> dict:
    leaks, problem = _gitleaks_or_problem(out, config)
    patterns = load_patterns(config)
    result = {
        "license": (out / "LICENSE").is_file(),
        "oversized": oversized(out, max_mb),
        "patterns": scan_patterns(out, load_list(config / "allow-patterns.txt"), patterns),
        "gitleaks": leaks if leaks is not None else "not-run",
    }
    # An unsupported build fails even when gitleaks is optional: its scan is wrong.
    result["failures"] = _failures(result, bool(patterns), require_gitleaks, problem)
    return result


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--out", type=Path, required=True, help="empty or missing directory")
    ap.add_argument("--config", type=Path, default=HERE)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--max-file-mb", type=float, default=5.0)
    ap.add_argument("--require-gitleaks", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        print(f"refusing: {args.out} is not empty", file=sys.stderr)
        return 2
    summary = build(args.repo, args.ref, args.out, args.config)
    summary["gates"] = gate(args.out, args.config, args.max_file_mb, args.require_gitleaks)
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    failures = summary["gates"]["failures"]
    print(
        f"export of {summary['commit'][:12]}: {summary['files']} files, "
        f"{summary['excluded']} excluded, umrs pin {str(summary['umrs_pin'])[:12]}"
    )
    print("gates: " + ("PASS" if not failures else "FAIL — " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
