"""Public export of R4 to R-CCS-HPBD/r4 (repo-rename plan P5a).

scripts/public_export/export.py snapshots one commit without the excluded
paths, swaps the private CI for the public one, keeps the umrs pointer with
the public URL, and gates the tree. A wrong exclusion or a missing gate
publishes private material, so these tests pin the behaviour.

Hermetic: temporary git repositories built with GIT_* stripped from the
environment (a pre-commit hook's GIT_INDEX_FILE must not leak in).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.public_export import export

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scripts" / "public_export"
PIN = "0123456789abcdef0123456789abcdef01234567"


def _run(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@e",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@e",
    )
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tiny development repository with private and public paths."""
    r = tmp_path / "dev"
    r.mkdir()
    _run(r, "init", "-q", "-b", "main")
    files = {
        "LICENSE": "Apache License 2.0\n",
        "r4/a.py": "print('hi')\n",
        "weird[name].txt": "literal path, not a glob\n",
        "docs/guide.md": "data lives under INTERNAL-MARKER/project\n",
        "docs/development/ledger.md": "internal\n",
        "STATE.md": "internal\n",
        ".github/workflows/private.yaml": "runs-on: [self-hosted]\n",
    }
    for rel, text in files.items():
        _write(r, rel, text)
    _run(r, "add", "-A")
    _run(r, "update-index", "--add", "--cacheinfo", f"160000,{PIN},umrs")
    _run(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    c = tmp_path / "config"
    (c / "public-workflows").mkdir(parents=True)
    (c / "exclude.txt").write_text("# test\ndocs/development/\nSTATE.md\n.github/workflows/\n")
    (c / "allow-patterns.txt").write_text("")
    # Neutral fixture patterns: the real ones are site facts (private-patterns.txt).
    (c / "private-patterns.txt").write_text("# test\nmarker INTERNAL-MARKER\nother NEVER-SEEN\n")
    (c / "public-workflows" / "ci.yaml").write_text("name: CI\n")
    shutil.copyfile(CONFIG / "gitleaks.toml", c / "gitleaks.toml")
    return c


def _exported(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}


def test_build_drops_excluded_paths_and_swaps_the_ci(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    summary = export.build(repo, "main", out, config)
    files = _exported(out)
    assert {"LICENSE", "r4/a.py", "weird[name].txt", "docs/guide.md"} <= files
    assert not {"docs/development/ledger.md", "STATE.md", ".github/workflows/private.yaml"} & files
    assert ".github/workflows/ci.yaml" in files
    assert summary["workflows"] == ["ci.yaml"]
    assert (summary["files"], summary["excluded"]) == (4, 3)


def test_links_into_excluded_paths_become_their_text(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    _write(
        repo,
        "docs/links.md",
        "See [the ledger](development/ledger.md#x) and [state](../STATE.md).\n"
        "![chart](development/c.png) [guide](guide.md) [web](https://e.org/a.md)\n"
        "[gone](missing.md) [dir](development/)\n"
        '[plan](development/p.md §11) [t](guide.md "Guide")\n',
    )
    _run(repo, "add", "docs/links.md")
    _run(repo, "commit", "-q", "-m", "links")
    out = tmp_path / "out"
    summary = export.build(repo, "main", out, config)
    assert (out / "docs" / "links.md").read_text() == (
        "See the ledger and state.\n"
        "chart [guide](guide.md) [web](https://e.org/a.md)\n"
        "[gone](missing.md) dir\n"
        'plan [t](guide.md "Guide")\n'
    )
    assert summary["unlinked"] == 5


def test_the_link_baseline_keeps_only_exported_live_links(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    _write(
        repo,
        "tools/md_link_check_baseline.txt",
        "# header\n"
        "docs/guide.md\tmissing.md\n"
        "docs/guide.md\tdevelopment/ledger.md\n"
        "docs/development/ledger.md\tx.md\n",
    )
    _run(repo, "add", "tools/md_link_check_baseline.txt")
    _run(repo, "commit", "-q", "-m", "baseline")
    out = tmp_path / "out"
    export.build(repo, "main", out, config)
    baseline = (out / "tools" / "md_link_check_baseline.txt").read_text()
    assert baseline == "# header\ndocs/guide.md\tmissing.md\n"


def test_link_target_ignores_urls_anchors_and_paths_above_the_root() -> None:
    assert export.link_target("docs/a.md", "https://e.org/x.md") is None
    assert export.link_target("docs/a.md", "mailto:x@e.org") is None
    assert export.link_target("docs/a.md", "#local") is None
    assert export.link_target("docs/a.md", "../../outside.md") is None
    assert export.link_target("docs/a.md", "../STATE.md?plain=1") == "STATE.md"


def test_umrs_stays_a_pointer_with_the_public_url(repo: Path, config: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = export.build(repo, "main", out, config)
    assert summary["umrs_pin"] == PIN
    assert not (out / "umrs").exists()
    gitmodules = (out / ".gitmodules").read_text()
    assert "url = https://github.com/UMRS-org/umrs.git" in gitmodules
    assert "git@" not in gitmodules


def test_pattern_gate_reports_paths_and_honours_the_allowlist(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    export.build(repo, "main", out, config)
    patterns = export.load_patterns(config)
    found = export.scan_patterns(out, [], patterns)
    assert found == [{"path": "docs/guide.md", "pattern": "marker"}]
    assert export.scan_patterns(out, ["docs/*.md marker"], patterns) == []
    assert export.scan_patterns(out, ["docs/*.md other"], patterns) == found


def test_gate_fails_without_licence_or_on_oversized_files(tmp_path: Path, config: Path) -> None:
    out = tmp_path / "tree"
    out.mkdir()
    (out / "big.bin").write_bytes(b"x" * 2048)
    result = export.gate(out, config, max_mb=0.001, require_gitleaks=False)
    assert "LICENSE missing" in result["failures"]
    assert result["oversized"] == [{"path": "big.bin", "bytes": 2048}]


def test_missing_gitleaks_fails_only_when_required(
    tmp_path: Path, config: Path, monkeypatch
) -> None:
    out = tmp_path / "tree"
    out.mkdir()
    (out / "LICENSE").write_text("x")
    monkeypatch.setattr(export.shutil, "which", lambda _name: None)
    assert export.gate(out, config, 5, require_gitleaks=False)["failures"] == []
    assert export.gate(out, config, 5, require_gitleaks=True)["failures"] == [
        "gitleaks not installed"
    ]


def _fake_gitleaks(tmp_path: Path, monkeypatch, version_line: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "gitleaks"
    exe.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.mark.parametrize("line", ["version is set by build process", "8.18.4"])
def test_an_unsupported_gitleaks_fails_the_gate_even_when_optional(
    tmp_path: Path, config: Path, monkeypatch, line: str
) -> None:
    _fake_gitleaks(tmp_path, monkeypatch, line)
    out = tmp_path / "tree"
    out.mkdir()
    (out / "LICENSE").write_text("x")
    failures = export.gate(out, config, 5, require_gitleaks=False)["failures"]
    assert len(failures) == 1 and "unsupported (need 8.30.1 or newer)" in failures[0]


def _supported_gitleaks() -> bool:
    exe = shutil.which("gitleaks")
    version = export.gitleaks_version(exe) if exe else None
    return version is not None and version >= export.MIN_GITLEAKS


@pytest.mark.skipif(not _supported_gitleaks(), reason="gitleaks 8.30.1+ not installed")
def test_gitleaks_finds_nothing_in_a_clean_tree(repo: Path, config: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    export.build(repo, "main", out, config)
    assert export.run_gitleaks(out, config) == []


def test_main_refuses_a_non_empty_output_dir(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep").write_text("x")
    assert export.main(["--repo", str(repo), "--ref", "main", "--out", str(out)]) == 2


def test_the_gate_fails_closed_without_patterns(tmp_path: Path, config: Path, monkeypatch) -> None:
    out = tmp_path / "tree"
    out.mkdir()
    (out / "LICENSE").write_text("x")
    (config / "private-patterns.txt").unlink()
    monkeypatch.setattr(export.shutil, "which", lambda _name: None)
    failures = export.gate(out, config, 5, require_gitleaks=False)["failures"]
    assert failures == ["no patterns loaded (private-patterns.txt missing or empty)"]


def test_the_real_patterns_never_leave_this_repository() -> None:
    rules = export.load_list(CONFIG / "exclude.txt")
    assert export.is_excluded("scripts/public_export/private-patterns.txt", rules)
    real = CONFIG / "private-patterns.txt"
    if not real.is_file():
        pytest.skip("private-patterns.txt is absent (a public export)")
    patterns = export.load_patterns(CONFIG)
    assert patterns, "private-patterns.txt has no patterns"
    for tool in (CONFIG / "export.py", Path(__file__)):
        text = tool.read_text()
        hits = [name for name, rx in patterns.items() if rx.search(text)]
        assert not hits, (tool.name, hits)


def _pattern_samples(real: Path) -> list[tuple[str, str]]:
    lines = (ln[2:].strip() for ln in real.read_text().splitlines() if ln.startswith("#>"))
    return [(name, sample) for name, _, sample in (ln.partition(" ") for ln in lines)]


def test_the_real_patterns_catch_their_samples() -> None:
    # The samples live in the never-exported file: they are the site facts.
    real = CONFIG / "private-patterns.txt"
    if not real.is_file():
        pytest.skip("private-patterns.txt is absent (a public export)")
    patterns = export.load_patterns(CONFIG)
    samples = _pattern_samples(real)
    assert {"person-name", "site-account", "site-path"} <= {name for name, _ in samples}
    missed = [(name, s) for name, s in samples if not patterns[name].search(s)]
    assert not missed


def test_the_first_push_findings_stay_excluded() -> None:
    rules = export.load_list(CONFIG / "exclude.txt")
    for private in (
        "docs/user-manual/25-umrs-front-deployment.md",
        "deploy/ai4s/finetune-adapter.slurm",
        "deploy/singularity/mini-rivault.def",
        "docs/audits/a.md",
        ".github/instructions/deployment-operations.instructions.md",
        "bug.memo",
        "loop-run-log.md",
        "ROADMAP.md",
        "DEVELOPMENT_OBJECTIVES.md",
        # Second verification: credential-naming tooling, site profiles,
        # R-CCS deployment tests and tools, personas, working notes.
        "tools/config_drift_validator.py",
        "tests/dr_rehearsal.sh",
        "scripts/e2e-canary.sh",
        "deploy/r4-client-env/rccs.env",
        "tests/test_umrs_front_podman_boot.py",
        "tools/service_status.py",
        ".github/agents/reviewer.agent.md",
        "docs/AGENT_TASK_QUEUE.md",
        "packages/r4-tui-v3/r4-session-0123abcd.md",
        "vscode-extension/resources/icon.svg.bak",
    ):
        assert export.is_excluded(private, rules), private
    assert not export.is_excluded("docs/user-manual/17-docker-deployment.md", rules)


def test_real_exclude_list_keeps_the_private_records_out() -> None:
    rules = export.load_list(CONFIG / "exclude.txt")
    for private in (
        "docs/development/ledger.md",
        "docs/history/x.md",
        "STATE.md",
        "memories/m.md",
        "ops/watchdog/watchdog.py",
        "deploy/ansible/inventory/umrs-front/hosts.yaml",
        ".github/workflows/ci.yaml",
        "reports/r.md",
        ".claude/settings.json",
    ):
        assert export.is_excluded(private, rules), private
    for public in ("r4/src/r4/cli.py", "README.md", "LICENSE", "deploy/standalone/compose.yaml"):
        assert not export.is_excluded(public, rules), public


def test_public_workflows_never_use_a_self_hosted_runner() -> None:
    for path in sorted((CONFIG / "public-workflows").glob("*.y*ml")):
        jobs = yaml.safe_load(path.read_text())["jobs"]
        for name, job in jobs.items():
            assert job["runs-on"] == "ubuntu-latest", f"{path.name}:{name}"


def _export_workflow() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "public-export.yaml").read_text())


def _step(wf: dict, prefix: str) -> dict:
    return next(s for s in wf["jobs"]["export"]["steps"] if s.get("name", "").startswith(prefix))


def test_export_workflow_is_a_dry_run_on_a_hosted_runner_by_default() -> None:
    wf = _export_workflow()
    # PyYAML reads the bare key `on` as True.
    assert wf[True]["workflow_dispatch"]["inputs"]["push"]["default"] is False
    assert wf["jobs"]["export"]["runs-on"] == "ubuntu-latest"


def test_export_workflow_guards_the_push() -> None:
    wf = _export_workflow()
    guard = _step(wf, "Refuse an unsafe push")["run"]
    assert 'CONFIRM" = "$PUBLIC_REPO"' in guard
    assert "RELEASABLE" in guard
    # No status function in `if`: the step runs only after every gate passed.
    assert _step(wf, "Push one sync commit")["if"] == "inputs.push"


def test_the_sync_commit_keeps_gitignored_exported_files() -> None:
    # Plain `git add -A` dropped tracked files the export's .gitignore matches.
    push = _step(_export_workflow(), "Push one sync commit")["run"]
    assert "git add -A -f" in push
    assert "sync commit differs from the exported tree" in push


def test_the_public_overlay_replaces_and_adds_files(
    repo: Path, config: Path, tmp_path: Path
) -> None:
    overlay = config / "public-overlay"
    (overlay / "docs").mkdir(parents=True)
    (overlay / "LICENSE").write_text("public licence text\n")
    (overlay / "docs" / "public.md").write_text("for strangers\n")
    out = tmp_path / "out"
    summary = export.build(repo, "main", out, config)
    assert (out / "LICENSE").read_text() == "public licence text\n"
    assert (out / "docs" / "public.md").read_text() == "for strangers\n"
    assert summary["overlay"] == ["LICENSE", "docs/public.md"]


def test_no_overlay_directory_means_no_overlay(repo: Path, config: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert export.build(repo, "main", out, config)["overlay"] == []
    assert (out / "LICENSE").read_text() == "Apache License 2.0\n"


def test_the_otel_overlay_differs_from_the_original_only_in_the_endpoint() -> None:
    # The sites' deployment mounts the original (its Prometheus is
    # umrs-front-prometheus); the public compose names it rivault-prometheus.
    original = (ROOT / "observability" / "otel-collector-config.yaml").read_text().splitlines()
    overlay = (
        CONFIG / "public-overlay" / "observability" / "otel-collector-config.yaml"
    ).read_text()
    changed = [(a, b) for a, b in zip(original, overlay.splitlines(), strict=True) if a != b]
    assert len(original) == len(overlay.splitlines())
    assert [b.strip() for _, b in changed] == [
        "endpoint: http://rivault-prometheus:9090/api/v1/write"
    ]


def test_the_overlay_sources_are_not_exported_twice() -> None:
    rules = export.load_list(CONFIG / "exclude.txt")
    assert export.is_excluded("scripts/public_export/public-overlay/README.md", rules)
