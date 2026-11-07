"""tools/md_link_check.py on fixtures (hardening plan H-11.8; 2026-10-18)."""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.md_link_check as mlc


def _repo(tmp_path: Path, files: dict[str, str], submodules: tuple[str, ...] = ()) -> mlc.Repo:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return mlc.Repo(tmp_path, list(files), list(submodules))


# --- GitHub slugs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("1.3 Ownership (from the design)", "13-ownership-from-the-design"),
        ("H-9 — Full-App journey tests in CI", "h-9--full-app-journey-tests-in-ci"),
        (
            "Removed: `chat_with_context()` and `inject_context()`",
            "removed-chat_with_context-and-inject_context",
        ),
        ("See [the map](docs/README.md) **now**", "see-the-map-now"),
        ("設計 Notes", "設計-notes"),
    ],
)
def test_github_slug(heading, slug):
    assert mlc.github_slug(heading) == slug


def test_repeated_headings_get_numbered_and_fenced_ones_do_not_count():
    text = '# Setup\n## Setup\n```\n# Setup\n```\n<a id="Custom-Id"></a>\n### Setup\n'
    assert mlc.anchors_of(text) == {"setup", "setup-1", "setup-2", "custom-id"}


# --- link extraction ------------------------------------------------------------------


def test_link_targets_forms():
    line = (
        '[a](x.md) ![img](i.png "title") [b](<with space.md>) '
        '<a href="h.md">h</a> [c](https://e.org) [d](p_(1).md)'
    )
    # Markdown links in order, then HTML attributes.
    assert mlc.link_targets(line) == [
        "x.md",
        "i.png",
        "with space.md",
        "https://e.org",
        "p_(1).md",
        "h.md",
    ]


def test_reference_definitions_are_links():
    assert mlc.link_targets("[ref]: ../docs/x.md#part") == ["../docs/x.md#part"]


def test_code_is_not_prose():
    text = "`[a](missing.md)`\n```\n[b](missing.md)\n```\n[c](real.md)\n"
    targets = [t for _, line in mlc.prose_lines(text) for t in mlc.link_targets(line)]
    assert targets == ["real.md"]


# --- resolution -----------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    return _repo(
        tmp_path,
        {
            "README.md": "# Top\n## 1.2 Rules\n",
            "docs/README.md": "# Map\n## Reading order\n",
            "docs/guide/a b.md": "# Spaced\n",
            "tools/x.py": "print()\n",
        },
        submodules=("umrs",),
    )


@pytest.mark.parametrize(
    "target",
    [
        "../README.md",
        "../README.md#12-rules",
        "#reading-order",
        "#READING-ORDER",
        "guide/a%20b.md",
        "/tools/x.py",
        "../tools/x.py#L10-L20",
        "../tools/x.py#anything",  # anchors are checked on Markdown only
        "guide/",
        "../umrs/src/missing.py",  # submodule: not checked out in CI
        "https://example.org/x.md",
        "mailto:someone@example.org",
        "",
    ],
)
def test_resolving_targets(repo, target):
    assert mlc.check_target(repo, "docs/README.md", target) is None


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("missing.md", "no such tracked file or directory"),
        ("../README.md#nope", "no heading or anchor #nope in README.md"),
        ("#nope", "no heading or anchor #nope in docs/README.md"),
        ("../../outside.md", "points outside the repository"),
        ("docs/README.md", "no such tracked file or directory"),  # resolves to docs/docs/
    ],
)
def test_broken_targets(repo, target, reason):
    assert mlc.check_target(repo, "docs/README.md", target) == reason


def test_check_file_reports_line_numbers(tmp_path):
    repo = _repo(tmp_path, {"a.md": "# A\n\nsee [b](b.md)\n[ok](#a)\n"})
    assert [(b.line, b.target) for b in mlc.check_file(repo, "a.md")] == [(3, "b.md")]


# --- baseline ---------------------------------------------------------------------------


def _broken(*targets: str) -> list[mlc.BrokenLink]:
    return [mlc.BrokenLink("a.md", n, t, "x") for n, t in enumerate(targets, 1)]


def test_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.txt"
    mlc.write_baseline(path, _broken("b.md", "c.md", "b.md"))
    assert mlc.read_baseline(path) == {"a.md\tb.md", "a.md\tc.md"}


def test_report_fails_only_on_new_links(capsys):
    assert mlc._report(_broken("b.md"), {"a.md\tb.md"}) == 0
    assert mlc._report(_broken("b.md", "new.md"), {"a.md\tb.md"}) == 1
    out = capsys.readouterr().out
    assert "a.md:2: new.md" in out and "a.md:1: b.md" not in out.split("\n", 1)[1]


def test_report_names_stale_entries_without_failing(capsys):
    assert mlc._report([], {"a.md\tfixed.md"}) == 0
    assert "stale baseline entry" in capsys.readouterr().out


def test_report_without_baseline_fails_on_any(capsys):
    assert mlc._report(_broken("b.md"), None) == 1
    assert mlc._report([], None) == 0


def test_main_uses_the_baseline(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"a.md": "[b](b.md)\n"})
    monkeypatch.setattr(mlc.Repo, "from_git", classmethod(lambda _cls, _root: repo))
    baseline = tmp_path / "base.txt"
    assert mlc.main(["--root", str(tmp_path)]) == 1
    assert mlc.main(["--root", str(tmp_path), "--write-baseline", str(baseline)]) == 0
    assert mlc.main(["--root", str(tmp_path), "--baseline", str(baseline)]) == 0


def test_submodules_come_from_gitmodules(tmp_path):
    (tmp_path / ".gitmodules").write_text('[submodule "umrs"]\n\tpath = umrs\n\turl = x\n')
    assert mlc.submodule_paths(tmp_path) == ["umrs"]
    assert mlc.submodule_paths(tmp_path / "none") == []


def test_baseline_file_is_tracked_shape():
    path = Path(__file__).resolve().parents[1] / "tools" / "md_link_check_baseline.txt"
    entries = mlc.read_baseline(path)
    assert entries, "the CI step reads this baseline"
    assert all(len(e.split("\t")) == 2 for e in entries)


def test_export_overlay_links_resolve_from_the_repository_root():
    """public-overlay/ files land at the export root (apply_overlay)."""
    overlay = "scripts/public_export/public-overlay/"
    assert mlc.resolve(overlay + "README.md", "SECURITY.md") == "SECURITY.md"
    assert mlc.resolve(overlay + "README.md", "docs/README.md") == "docs/README.md"
    assert mlc.resolve(overlay + ".github/pull_request_template.md", "../LICENSE") == "LICENSE"
    assert (
        mlc.resolve("scripts/public_export/README.md", "SECURITY.md")
        == "scripts/public_export/SECURITY.md"
    )
