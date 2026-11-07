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
import re
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


#: GitHub-hosted runner labels a public workflow may name. The release builds
#: the macOS console binaries natively (2026-11-07), so the list holds the two
#: macOS images besides ubuntu-latest; a self-hosted label or a list never fits.
GITHUB_HOSTED = ("ubuntu-latest", "macos-15", "macos-15-intel")


def test_public_workflows_never_use_a_self_hosted_runner() -> None:
    for path in sorted((CONFIG / "public-workflows").glob("*.y*ml")):
        jobs = yaml.safe_load(path.read_text())["jobs"]
        for name, job in jobs.items():
            assert job["runs-on"] in GITHUB_HOSTED, f"{path.name}:{name}"


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


# --- Tag-aware export and the public release (one-command install, 2026-11-07)
#
# The export's shell steps run here against local bare repositories, the
# public release workflow is checked for its shape. Git runs with a scratch
# HOME and no global or system config (no signing, hooks or identity leak in).


def _git_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@e",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@e",
    )
    env.update(extra)
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)
    return done.stdout.decode().strip()


def _bash(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # A workflow `run` without `shell:` is `bash -e {0}`.
    return subprocess.run(
        ["bash", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True
    )


def _outputs(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines)


@pytest.fixture()
def public(tmp_path: Path) -> dict:
    """A bare "public" origin (main + annotated tag v1.0.0) and a clone of it at
    RUNNER_TEMP/public, as the sync step leaves it."""
    env = _git_env(tmp_path)
    origin = tmp_path / "origin.git"
    _git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(origin))
    runner = tmp_path / "runner"
    clone = runner / "public"
    _git(tmp_path, env, "clone", "-q", str(origin), str(clone))
    (clone / "a.txt").write_text("a\n")
    _git(clone, env, "add", "a.txt")
    _git(clone, env, "commit", "-q", "-m", "first sync")
    _git(clone, env, "tag", "-a", "v1.0.0", "-m", "R4 v1.0.0")
    _git(clone, env, "push", "-q", "origin", "HEAD:main", "refs/tags/v1.0.0")
    return {
        "env": env,
        "origin": origin,
        "clone": clone,
        "head": _git(clone, env, "rev-parse", "HEAD"),
        "runner": runner,
    }


def _remote_tag(public: dict, tag: str) -> str:
    """The commit `tag` names in the bare origin, "" when it has none."""
    out = _git(public["origin"], public["env"], "tag", "--list", tag)
    if not out:
        return ""
    return _git(public["origin"], public["env"], "rev-parse", f"refs/tags/{tag}^{{commit}}")


def _step_env(public: dict, **extra: str) -> dict[str, str]:
    runner = public["runner"]
    values = {
        "RUNNER_TEMP": str(runner),
        "PUBLIC_REPO": "R-CCS-HPBD/r4",
        "COMMIT": "c0ffee" * 6 + "c0ff",
        "GITHUB_OUTPUT": str(runner / "out.txt"),
        "GITHUB_STEP_SUMMARY": str(runner / "summary.md"),
    }
    return _git_env(runner.parent, **{**values, **extra})


def _sync_tail(wf: dict) -> str:
    """The sync step from its tag lookup on (the clone, rsync and tree check
    before it need the real export)."""
    run = _step(wf, "Push one sync commit")["run"]
    return run[run.index("# The commit an existing public tag") :]


def test_the_tag_step_runs_only_for_a_v_tag_ref_and_a_real_push() -> None:
    wf = _export_workflow()
    tag_step = _step(wf, "Tag the public sync commit")
    assert tag_step["if"] == "inputs.push && startsWith(steps.ref.outputs.tag, 'v')"
    assert tag_step["env"]["TAG"] == "${{ steps.ref.outputs.tag }}"
    names = [s.get("name", "") for s in wf["jobs"]["export"]["steps"]]
    assert names.index("Tag the public sync commit") == len(names) - 1
    pushes = [ln.strip() for ln in tag_step["run"].splitlines() if "git push" in ln]
    assert pushes == ['git push --quiet origin "refs/tags/${TAG}:refs/tags/${TAG}"']


@pytest.mark.parametrize(
    ("ref", "tag"),
    [
        ("v1.2.0-rc1", "v1.2.0-rc1"),
        ("refs/tags/v1.2.0-rc1", "v1.2.0-rc1"),
        ("main", ""),
        ("release-1", ""),
        ("v9", ""),  # a branch, not a tag
    ],
)
def test_resolve_names_the_tag_only_when_the_ref_is_a_v_tag(
    tmp_path: Path, ref: str, tag: str
) -> None:
    env = _git_env(tmp_path)
    repo = tmp_path / "dev"
    _git(tmp_path, env, "init", "-q", "-b", "main", str(repo))
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "one")
    _git(repo, env, "tag", "-a", "v1.2.0-rc1", "-m", "rc")
    _git(repo, env, "tag", "release-1")
    _git(repo, env, "branch", "v9")
    out = tmp_path / "out.txt"
    script = _step(_export_workflow(), "Resolve the commit")["run"]
    done = _bash(script, repo, {**env, "REF": ref, "GITHUB_OUTPUT": str(out)})
    assert done.returncode == 0, done.stderr
    assert _outputs(out)["tag"] == tag


def test_the_sync_refuses_new_content_under_an_existing_public_tag(public: dict) -> None:
    clone, env = public["clone"], public["env"]
    (clone / "b.txt").write_text("b\n")
    _git(clone, env, "add", "b.txt")
    done = _bash(_sync_tail(_export_workflow()), clone, _step_env(public, TAG="v1.0.0"))
    assert done.returncode == 1
    assert "tag v1.0.0 already exists" in done.stdout
    assert _git(public["origin"], env, "rev-parse", "main") == public["head"]


def test_the_sync_pushes_main_and_reports_a_new_tag_as_absent(public: dict) -> None:
    clone, env = public["clone"], public["env"]
    (clone / "b.txt").write_text("b\n")
    _git(clone, env, "add", "b.txt")
    done = _bash(_sync_tail(_export_workflow()), clone, _step_env(public, TAG="v2.0.0"))
    assert done.returncode == 0, done.stdout + done.stderr
    out = _outputs(public["runner"] / "out.txt")
    assert out["tag_at"] == ""
    assert out["commit"] == _git(public["origin"], env, "rev-parse", "main") != public["head"]


def test_the_sync_reports_where_an_existing_tag_points(public: dict) -> None:
    done = _bash(_sync_tail(_export_workflow()), public["clone"], _step_env(public, TAG="v1.0.0"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "nothing to sync" in done.stdout
    out = _outputs(public["runner"] / "out.txt")
    assert out == {"commit": public["head"], "tag_at": public["head"]}


# Never backwards (2026-11-07): before, exporting an older ref after a newer
# one committed the older tree on top of public main.


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, list[str]]:
    """The private checkout ($GITHUB_WORKSPACE, full history): commits a-b-c."""
    env = _git_env(tmp_path)
    repo = tmp_path / "workspace"
    _git(tmp_path, env, "init", "-q", "-b", "main", str(repo))
    shas = []
    for name in ("a", "b", "c"):
        _git(repo, env, "commit", "-q", "--allow-empty", "-m", name)
        shas.append(_git(repo, env, "rev-parse", "HEAD"))
    return repo, shas


@pytest.mark.parametrize(
    ("synced", "exported", "allow_older", "allowed"),
    [
        (1, 0, "false", False),  # public main came from a descendant of the export
        (None, 0, "false", False),  # ... from a commit this repository lacks
        (1, 1, "false", True),  # the same commit (content changed by the export rules)
        (1, 2, "false", True),  # a descendant
        (1, 0, "true", True),  # a deliberate rollback
    ],
)
def test_the_sync_never_moves_public_main_backwards(
    public: dict,
    workspace: tuple[Path, list[str]],
    synced: int | None,
    exported: int,
    allow_older: str,
    allowed: bool,
) -> None:
    repo, shas = workspace
    clone, env = public["clone"], public["env"]
    last = "f" * 12 if synced is None else shas[synced][:12]
    _git(clone, env, "commit", "-q", "--allow-empty", "-m", f"Sync from r4@{last}")
    (clone / "b.txt").write_text("b\n")
    _git(clone, env, "add", "b.txt")
    step_env = _step_env(
        public, COMMIT=shas[exported], GITHUB_WORKSPACE=str(repo), ALLOW_OLDER=allow_older
    )
    done = _bash(_sync_tail(_export_workflow()), clone, step_env)
    main = _git(public["origin"], env, "rev-parse", "main")
    if allowed:
        assert done.returncode == 0, done.stdout + done.stderr
        assert main == _git(clone, env, "rev-parse", "HEAD") != public["head"]
    else:
        assert done.returncode == 1
        assert "refusing to move it backwards" in done.stdout
        assert main == public["head"]


def test_the_first_sync_into_an_empty_public_repository_passes(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    origin, clone = tmp_path / "origin.git", tmp_path / "runner" / "public"
    _git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, env, "clone", "-q", str(origin), str(clone))
    (clone / "a.txt").write_text("a\n")
    _git(clone, env, "add", "a.txt")
    public = {"runner": clone.parent}
    done = _bash(_sync_tail(_export_workflow()), clone, _step_env(public, TAG=""))
    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(origin, env, "log", "-1", "--format=%s", "main").startswith("Sync from r4@")


def test_allow_older_is_an_off_by_default_input_of_the_sync() -> None:
    wf = _export_workflow()
    allow = wf[True]["workflow_dispatch"]["inputs"]["allow_older"]
    assert allow["type"] == "boolean" and allow["default"] is False
    assert _step(wf, "Push one sync commit")["env"]["ALLOW_OLDER"] == "${{ inputs.allow_older }}"
    assert wf["jobs"]["export"]["steps"][0]["with"]["fetch-depth"] == 0


def _run_tag_step(public: dict, tag: str, tag_at: str) -> subprocess.CompletedProcess[str]:
    script = _step(_export_workflow(), "Tag the public sync commit")["run"]
    env = _step_env(public, TAG=tag, SYNC=public["head"], TAG_AT=tag_at)
    return _bash(script, public["runner"], env)


def test_the_tag_step_pushes_one_annotated_tag_on_the_sync_commit(public: dict) -> None:
    env = public["env"]
    _git(public["clone"], env, "tag", "v-private-only")  # must never leave the clone
    done = _run_tag_step(public, "v2.0.0", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _remote_tag(public, "v2.0.0") == public["head"]
    assert _git(public["origin"], env, "cat-file", "-t", "refs/tags/v2.0.0") == "tag"
    assert _remote_tag(public, "v-private-only") == ""


def test_the_tag_step_refuses_a_tag_on_another_commit(public: dict) -> None:
    done = _run_tag_step(public, "v2.0.0", "f" * 40)
    assert done.returncode == 1
    assert "already exists" in done.stdout
    assert _remote_tag(public, "v2.0.0") == ""


def test_the_tag_step_accepts_a_rerun_on_the_same_commit(public: dict) -> None:
    done = _run_tag_step(public, "v1.0.0", public["head"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "already on" in (public["runner"] / "summary.md").read_text()


RELEASE = CONFIG / "public-workflows" / "release.yaml"
CONSOLE_TARGETS = {"linux-x64", "linux-x64-baseline", "linux-arm64", "darwin-arm64", "darwin-x64"}


def _release() -> dict:
    return yaml.safe_load(RELEASE.read_text())


def _job_runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_release_runs_only_on_v_tags_and_only_publishing_writes() -> None:
    wf = _release()
    assert wf[True] == {"push": {"tags": ["v*"]}}
    assert wf["permissions"] == {"contents": "read"}
    writers = [n for n, job in wf["jobs"].items() if "permissions" in job]
    assert writers == ["release"]
    assert wf["jobs"]["release"]["permissions"] == {"contents": "write"}


def test_release_builds_the_five_wheels_into_the_client_bundle() -> None:
    wf = _release()
    projects = wf["env"]["WHEEL_PROJECTS"].split()
    assert projects == ["r4", "r4-contracts", "packages/r4-teamengine-spi", "science", "umrs/src"]
    assert wf["env"]["WHEEL_DISTS"].split() == [
        "r4",
        "r4_contracts",
        "r4_teamengine_spi",
        "r4_science",
        "umrs",
    ]
    job = wf["jobs"]["wheels"]
    assert job["steps"][0]["with"]["submodules"] is True
    runs = _job_runs(job)
    assert "for project in $WHEEL_PROJECTS; do" in runs
    assert "uv build --wheel" in runs
    assert 'r4-client-${tag}.tar.gz" -- SHA256SUMS *.whl' in runs


@pytest.mark.parametrize("project", ["r4", "r4-contracts", "packages/r4-teamengine-spi", "science"])
def test_the_released_projects_exist(project: str) -> None:
    # umrs/src lives in the submodule, which a plain checkout may leave empty.
    assert (ROOT / project / "pyproject.toml").is_file()


def test_release_builds_the_five_console_targets() -> None:
    wf = _release()
    assert set(wf["env"]["TUI_TARGETS"].split()) == CONSOLE_TARGETS
    runs = "\n".join(_job_runs(job) for job in wf["jobs"].values())
    built = set(re.findall(r"--target=([a-z0-9-]+)", runs))
    assert built == CONSOLE_TARGETS
    matrix = (ROOT / "packages" / "r4-tui-v3" / "scripts" / "build-bin.mjs").read_text()
    for target in CONSOLE_TARGETS:
        assert f'"{target}": "bun-{target}"' in matrix, target


def test_release_publishes_install_sh_sums_and_optional_signatures() -> None:
    job = _release()["jobs"]["release"]
    runs = _job_runs(job)
    assert 'cp scripts/install-r4.sh "$dist/install.sh"' in runs
    assert 'sha256sum -- * > "$RUNNER_TEMP/SHA256SUMS"' in runs
    assert 'gh release upload --clobber "$tag" -- *' in runs
    assert '"r4-tui-$t" "r4-tui-$t.sha256"' in runs
    sign = next(s for s in job["steps"] if s.get("name", "").startswith("Sign"))
    assert sign["env"]["MINISIGN_SECRET_KEY"] == "${{ secrets.R4_TUI_MINISIGN_SECRET_KEY }}"
    # The trusted comment `r4 tui install` checks: "r4-tui <tag> <asset>".
    assert '-t "r4-tui ${GITHUB_REF_NAME} $f"' in sign["run"]


@pytest.mark.skipif(not shutil.which("sha256sum"), reason="sha256sum not installed")
def test_the_client_bundle_holds_the_wheels_and_their_sums(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in ("r4-0.2.0rc2-py3-none-any.whl", "umrs-0.2.0rc2-py3-none-any.whl"):
        (wheels / name).write_bytes(name.encode())
    step = next(
        s for s in _release()["jobs"]["wheels"]["steps"] if s.get("name", "").startswith("Pack")
    )
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_REF_NAME": "v0.2.0-rc2"}
    done = _bash(step["run"], tmp_path, env)
    assert done.returncode == 0, done.stderr
    listed = sorted(done.stdout.split())
    assert listed == [
        "SHA256SUMS",
        "r4-0.2.0rc2-py3-none-any.whl",
        "umrs-0.2.0rc2-py3-none-any.whl",
    ]
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    bundle = tmp_path / "client" / "r4-client-v0.2.0-rc2.tar.gz"
    subprocess.run(["tar", "-xzf", str(bundle), "-C", str(unpacked)], check=True)
    check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=unpacked, capture_output=True)
    assert check.returncode == 0


def _release_step(job: str, prefix: str) -> dict:
    steps = _release()["jobs"][job]["steps"]
    return next(s for s in steps if s.get("name", "").startswith(prefix))


@pytest.mark.parametrize(
    ("wheel", "tag", "ok"),
    [
        ("r4-0.2.0rc2-py3-none-any.whl", "v0.2.0-rc2", True),
        ("r4-0.2.0rc2-py3-none-any.whl", "v0.2.0-rc.2", True),
        ("r4-1.0.0-py3-none-any.whl", "v1.0.0", True),
        ("r4-0.2.0rc1-py3-none-any.whl", "v0.2.0-rc2", False),
        (None, "v0.2.0-rc2", False),
    ],
)
def test_a_wheel_version_that_differs_from_the_tag_fails_the_release(
    tmp_path: Path, wheel: str | None, tag: str, ok: bool
) -> None:
    # Before 2026-11-07 a mismatch was only a ::warning:: and the release went out.
    (tmp_path / "wheels").mkdir()
    if wheel:
        (tmp_path / "wheels" / wheel).write_bytes(b"w")
    (tmp_path / "wheels" / "r4_contracts-9.9.9-py3-none-any.whl").write_bytes(b"w")
    step = _release_step("wheels", "Check the r4 wheel version")
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_REF_NAME": tag}
    done = _bash(step["run"], tmp_path, env)
    assert (done.returncode == 0) is ok, done.stdout + done.stderr
    assert ("::error title=Version mismatch::" in done.stdout) is not ok


def test_publish_needs_every_build_but_the_intel_mac_console() -> None:
    job = _release()["jobs"]["release"]
    assert set(job["needs"]) == {"wheels", "tui-linux", "tui-darwin-arm64", "tui-darwin-x64"}
    terms = [t.strip() for t in job["if"].split("&&")]
    assert terms == [
        "always()",
        "needs.wheels.result == 'success'",
        "needs.tui-linux.result == 'success'",
        "needs.tui-darwin-arm64.result == 'success'",
    ]
    assert _release()["env"]["TUI_OPTIONAL"].split() == ["darwin-x64"]
    sign = _release_step("release", "Sign")["run"]
    assert '[ -f "$f" ] || continue' in sign


def _asset_check(tmp_path: Path, skip: set[str]) -> subprocess.CompletedProcess[str]:
    wf = _release()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "r4-client-v1.0.0.tar.gz").write_bytes(b"b")
    for target in CONSOLE_TARGETS - skip:
        (dist / f"r4-tui-{target}").write_bytes(b"x")
        (dist / f"r4-tui-{target}.sha256").write_text("0" * 64)
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REF_NAME": "v1.0.0",
        "TUI_TARGETS": wf["env"]["TUI_TARGETS"],
        "TUI_OPTIONAL": wf["env"]["TUI_OPTIONAL"],
    }
    return _bash(_release_step("release", "Add install.sh")["run"], ROOT, env)


def test_the_asset_check_accepts_a_missing_intel_mac_console_with_a_warning(
    tmp_path: Path,
) -> None:
    done = _asset_check(tmp_path, {"darwin-x64"})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "::warning title=Console r4-tui-darwin-x64 missing::" in done.stdout
    assert (tmp_path / "dist" / "install.sh").is_file()


@pytest.mark.parametrize("target", sorted(CONSOLE_TARGETS - {"darwin-x64"}))
def test_the_asset_check_refuses_any_other_missing_console(tmp_path: Path, target: str) -> None:
    done = _asset_check(tmp_path, {target})
    assert done.returncode == 1
    assert f"::error::missing release assets: r4-tui-{target} r4-tui-{target}.sha256" in done.stdout


def test_the_asset_check_passes_the_full_set_silently(tmp_path: Path) -> None:
    done = _asset_check(tmp_path, set())
    assert done.returncode == 0, done.stdout + done.stderr
    assert "::warning" not in done.stdout


GH_STUB = """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "release view" ]; then
    case "$*" in *--json*) echo "${STUB_DRAFT:-false}"; exit 0 ;; esac
    exit "${STUB_VIEW_RC:-1}"
fi
exit 0
"""


def _publish(tmp_path: Path, tag: str, **extra: str) -> list[str]:
    stubs, dist = tmp_path / "stubs", tmp_path / "dist"
    stubs.mkdir()
    dist.mkdir()
    (stubs / "gh").write_text(GH_STUB)
    (stubs / "gh").chmod(0o755)
    for name in ("SHA256SUMS", "install.sh"):
        (dist / name).write_text(name)
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "LC_ALL": "C",  # the order `*` expands in
        "GITHUB_REF_NAME": tag,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "RUNNER_TEMP": str(tmp_path),
        "GH_REPO": "owner/repo",
        **extra,
    }
    done = _bash(_release_step("release", "Create or update")["run"], dist, env)
    assert done.returncode == 0, done.stdout + done.stderr
    return log.read_text().splitlines()


def test_a_new_release_is_a_draft_until_every_asset_is_uploaded(tmp_path: Path) -> None:
    # Before 2026-11-07 the release was public before its assets were uploaded.
    calls = _publish(tmp_path, "v0.2.0-rc2")
    assert calls == [
        "release view v0.2.0-rc2",
        "release create v0.2.0-rc2 --draft --verify-tag --title v0.2.0-rc2"
        f" --notes-file {tmp_path}/release-notes.md --prerelease",
        "release upload --clobber v0.2.0-rc2 -- SHA256SUMS install.sh",
        "release edit v0.2.0-rc2 --draft=false",
    ]


def test_a_final_tag_is_not_a_prerelease(tmp_path: Path) -> None:
    create = [c for c in _publish(tmp_path, "v1.0.0") if c.startswith("release create")]
    assert create == [
        f"release create v1.0.0 --draft --verify-tag --title v1.0.0 --notes-file {tmp_path}/release-notes.md"
    ]


def test_release_notes_need_no_git_checkout(tmp_path: Path) -> None:
    # v0.2.0-rc4's first publish failed: --notes-from-tag needs the tag in a
    # local clone, and this step runs in the dist directory.
    run = _release_step("release", "Create or update")["run"]
    assert "--notes-from-tag" not in run
    _publish(tmp_path, "v1.0.0")
    notes = (tmp_path / "release-notes.md").read_text()
    assert "R4 + UMRS v1.0.0." in notes
    assert "raw.githubusercontent.com/owner/repo/main/scripts/install-r4.sh" in notes


@pytest.mark.parametrize(("draft", "published"), [("false", False), ("true", True)])
def test_a_rerun_only_replaces_the_assets(tmp_path: Path, draft: str, published: bool) -> None:
    calls = _publish(tmp_path, "v1.0.0", STUB_VIEW_RC="0", STUB_DRAFT=draft)
    assert not [c for c in calls if c.startswith("release create")]
    assert "release upload --clobber v1.0.0 -- SHA256SUMS install.sh" in calls
    assert ("release edit v1.0.0 --draft=false" in calls) is published


RETIRED_RUNNER_LABELS = ("macos-12", "macos-13", "macos-14")


def test_no_workflow_uses_a_retired_macos_runner() -> None:
    # 2026-11-07: the first public release run was cancelled before any
    # step ("configuration 'macos-14-arm64-us-default' is not supported").
    files = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    files += sorted((CONFIG / "public-workflows").glob("*.y*ml"))
    hits = [
        f"{path.name}: {label}"
        for path in files
        for label in RETIRED_RUNNER_LABELS
        if f"runs-on: {label}" in path.read_text()
    ]
    assert hits == []
