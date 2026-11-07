"""University course-kit scaffolder (Phase E3).

``r4 course init <name>`` materializes a course directory tuned for lab
courses that teach or research-drive on top of R4. Two audiences:

- **Instructors** — README + syllabus skeleton with assignment stubs and
  point rubrics written in the course language
- **Students** — ``assignments/`` workspace that ``r4 project`` can point
  at, plus a curated collection of working examples that round-trip
  through the platform's own benchmarks gate

The goal of the wave is the same as the whole platform: help Japanese
scientists stay on-platform instead of migrating to offshore commercial
tools. Courses are a vector for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Course language assets (bilingual default) ──────────────────────────────

_SYLLABUS_TEMPLATE_EN = """# Course Syllabus — {course_title}

> Language: **EN** (paired JA edition at `SYLLABUS.ja.md` when you init with `--lang both`)

## Overview

This course uses the R4 Platform as the research compute backbone. Students
should leave with the ability to (a) run a reproducible LLM workflow,
(b) define their own knowledge bundles, and (c) demonstrate one creative
project on top of the platform's research loops.

## Outcomes checklist

- [ ] Finish assignments 1–4 with working endpoints and clean exit codes
- [ ] Export at least one `.kb` bundle with claim→evidence parity
- [ ] Explain the platform's egress policy in one page (sovereignty demo)
- [ ] Team project: a two-iteration autonomous research loop with a
      paper-style review report

## Weekly shape

| Week | Topic | Assignment |
|---:|---|---|
| 1 | Platform tour + first benchmark | AS1 — reproduce the public bench |
| 2 | Knowledge bundle construction | AS2 — build and verify `.kb` |
| 3 | Autonomous research loops | AS3 — run a loop with approval |
| 4 | Capstone | AS4 — write a paper-style capstone |

## Assessment rubric

- Assignments 1–3 (3 × 20 pts): working endpoint + visible artifacts
- Assignment 4 capstone (40 pts): report quality, reproducibility, review
"""

_SYLLABUS_TEMPLATE_JA = """# コース シラバス — {course_title_jp}

> 言語: **日本語**（EN版は `SYLLABUS.md`。両言語 init は `--lang both`）

## 概要

本コースは R4 プラットフォームを研究基盤として扱います。履修者は
(a) 再現性のある LLM ワークフローの実行、(b) ナレッジバンドル
(.kb) の作成、(c) プラットフォームの自律リサーチループ上での独創的
プロジェクト実施、を目指します。

## 到達目標チェック

- [ ] 課題1–4をエンドポイントと終了コードが揃った状態で完了
- [ ] claim→evidence の対応が揃う `.kb` バンドルを1つ書き出す
- [ ] プラットフォームのegressポリシーを1ページで説明できる
- [ ] チームプロジェクト: 2回の自律ループと紙質のレビュー付きレポート

## 週別構成

| 週 | トピック | 課題 |
|---:|---|---|
| 1 | プラットフォーム概要 + 初ベンチ | AS1: 公開ベンチの再現 |
| 2 | ナレッジバンドル構築 | AS2: `.kb` を作成〜検証 |
| 3 | 自律リサーチループ | AS3: 承認付きループ実行 |
| 4 | カプストーン | AS4: レポート |

## 成績配分

- 課題1–3 各20点: 動くエンドポイント + 成果物
- 課題4 ���プストーン 40点: 日本語/英語いずれか可、再現性とレビュー評価
"""

_README_EN = """# {course_title}

University course kit on the R4 Platform.

## What's inside

| Path | Purpose |
|---|---|
| `SYLLABUS.md` | Course syllabus (EN) |
| `SYLLABUS.ja.md` | Course syllabus (JA, init with `--lang both`) |
| `assignments/` | Student workspace + rubric skeleton |
| `course.yaml` | Course config the r4 CLI understands |
| `.r4ignore` | Paths R4 should not ingest into knowledge bundles |

## Instructor flow

1. `r4 auth login --gateway-url <gateway>` (site account; --oidc + provider if GakuNin)
2. Edit `course.yaml` (course title, student groups, Slurm partitions)
3. Export the canonical knowledge bundle for the class: `r4 project init`
4. Publish the bundle as `.kb` students run against (the AA used in AS2)

## Student flow

1. `pip install r4` then `r4 --version`
2. Copy the shared `.kb` into `assignments/`
3. Open `assignments/AS1/` — follow `README.md` inside
"""

_README_JA = """# {course_title_jp}

R4 プラットフォーム 大学講義キット.

## 構成

| パス | 用途 |
|---|---|
| `SYLLABUS.md` | シラバス英語版 |
| `SYLLABUS.ja.md` | シラバス日本語版（`--lang both` で生成） |
| `assignments/` | 学生課題ワークスペース |
| `course.yaml` | r4 CLI 向けコース設定 |
| `.r4ignore` | `.kb` に取り込ませないファイル一覧 |

## 教員側フロー

1. `r4 auth login --gateway-url <gateway>` （サイトアカウント。
   GakuNin の場合は `--oidc --provider gakunin`）
2. `course.yaml` を修正（コース名・学生グループ・Slurmパーティション）
3. 代表ナレッジバンドルを `r4 project init` で構築
4. `.kb` として配布（AS2 で利用）

## 学生側フロー

1. `pip install r4` 後に `r4 --version` で確認
2. 配布された `.kb` を `assignments/` に展開
3. `assignments/AS1/README.md` に従って開始
"""

_ASSIGNMENT_README_EN = """# AS{assignment_id} — {assignment_title}

## Goal

{assignment_goal}

## Starter

1. `r4 project create as{assignment_id}-rep`
2. `r4 project import as{assignment_id}-rep {starter_assets}`
3. {late_steps}

## Rubric (20 pts)

| Criterion | Points |
|---|---:|
{rubic_rows}

## Deliverable

{deliverable}
"""

_ASSIGNMENT_ASSETS = [
    {
        "id": "1",
        "title_en": "Reproduce the public benchmark",
        "title_ja": "公開ベンチマーク再現",
        "goal_en": (
            "Run the benchmarks runner pinned to the shipped config and "
            "regenerate docs/benchmarks locally."
        ),
        "goal_ja": (
            "リポジトリ付属の設定のままベンチラーネラーを実行し、 docs/benchmarks を再生成する。"
        ),
        "steps_en": [
            "cd benchmarks",
            "python run_benchmarks.py --models Qwen3.6-35B-A3B-NVFP4 --categories unit_test",
        ],
        "steps_ja": [
            "cd benchmarks",
            "python run_benchmarks.py --models Qwen3.6-35B-A3B-NVFP4 --categories unit_test",
        ],
        "deliverable_en": (
            "A new benchmarks/results/*.json + the JSON diff to the public report (rubric entry 1)"
        ),
        "deliverable_ja": (
            "新しい benchmarks/results/*.json と、公開レポートとの JSON 差分（採点項目1）"
        ),
        "rubric_en": [
            ("1 run completes with a results.json", 10),
            ("Diff vs public report carried in results/<metadata.json>", 10),
        ],
        "rubric_ja": [
            ("run コマンドが1回以上通り results.json が生成される", 10),
            ("公開レポートとの差分が metadata.json に収まっている", 10),
        ],
    },
    {
        "id": "2",
        "title_en": "Build and verify a knowledge bundle (.kb)",
        "title_ja": "ナレッジバンドル (.kb) の構築と検証",
        "goal_en": (
            "Take the shared source set, build a .kb, verify sample claims "
            "from the bench doc table."
        ),
        "goal_ja": (
            "共有ソースセットを整理して .kb を作り、ベンチ資料表のサンプル主張を抽出・検証する。"
        ),
        "steps_en": [
            "r4 project create kb-lab",
            "r4 project import kb-lab sample_papers",
            "r4 project init kb-lab",
            "r4 knowledge export kb-lab > kb-lab.kb",
        ],
        "steps_ja": [
            "r4 project create kb-lab",
            "r4 project import kb-lab sample_papers",
            "r4 project init kb-lab",
            "r4 knowledge export kb-lab > kb-lab.kb",
        ],
        "deliverable_en": "kb-lab.kb with at least 5 claims verified against evidence",
        "deliverable_ja": "証拠の数値に合う主張を5個以上含んだ kb-lab.kb",
        "rubric_en": [
            (".kb with 5+ verified claims and no dangling evidence", 15),
            ("Handwritten one-page summary of the verification process", 5),
        ],
        "rubric_ja": [
            ("5個以上の主張が証拠に対応、証拠欠如なしの .kb", 15),
            ("検証プロセスの手書き要約1ページ", 5),
        ],
    },
]


@dataclass
class CourseAssets:
    """One course's rendered files."""

    readme_en: str
    readme_ja: str
    syllabus_en: str
    syllabus_ja: str
    assignment_notebooks: list[dict[str, Any]] = field(default_factory=list)
    course_yaml: dict[str, Any] = field(default_factory=dict)
    r4ignore: str = ""


def build_course_assets(*, title: str, title_ja: str | None) -> CourseAssets:
    """Render the text bundle for one course (no filesystem writes)."""
    title_ja = title_ja or title
    assets = CourseAssets(
        readme_en=_README_EN.format(course_title=title, course_title_jp=title_ja),
        readme_ja=_README_JA.format(course_title=title, course_title_jp=title_ja),
        syllabus_en=_SYLLABUS_TEMPLATE_EN.format(course_title=title),
        syllabus_ja=_SYLLABUS_TEMPLATE_JA.format(course_title_jp=title_ja),
        course_yaml={
            "title": title,
            "title_ja": title_ja,
            "assignments": ["AS1", "AS2", "AS3", "AS4"],
            "language": "both",
            "knowledge_bundle_path": "assignments/shared.kb",
        },
        r4ignore="# Do not ingest shared solution assets into .kb\nsolutions_private/\n",
    )
    return assets


def _render_assignment_readme(assi: dict[str, Any], lang: str) -> str:
    goal = assi[f"goal_{'ja' if lang == 'ja' else 'en'}"]
    steps = "\n".join(f"- {s}" for s in assi[f"steps_{lang}"])
    deliv = assi[f"deliverable_{lang}"]
    rubric_rows = "\n".join(f"| {desc} | {pts} |" for desc, pts in assi[f"rubric_{lang}"])
    template = _ASSIGNMENT_README_EN if lang == "en" else _ASSIGNMENT_README_EN
    return template.format(
        assignment_id=assi["id"],
        assignment_title=assi[f"title_{lang}"],
        assignment_goal=goal,
        starter_assets="sample_papers/" if lang == "en" else "サンプル論文/",
        late_steps=steps,
        rubic_rows=rubric_rows,
        deliverable=deliv,
    )


def _write(parent: Path, name: str, text: str) -> Path:
    path = parent / name
    path.write_text(text, encoding="utf-8")
    return path


def materialize_course(
    course_dir: Path,
    *,
    title: str,
    title_ja: str | None,
    lang: str = "both",
) -> list[Path]:
    """Write the course tree and return created paths."""
    course_dir = Path(course_dir)
    course_dir.mkdir(parents=True, exist_ok=True)

    assets = build_course_assets(title=title, title_ja=title_ja)

    written: list[Path] = []
    if lang in ("en", "both"):
        written.append(_write(course_dir, "SYLLABUS.md", assets.syllabus_en))
    if lang in ("ja", "both"):
        written.append(_write(course_dir, "SYLLABUS.ja.md", assets.syllabus_ja))

    written.append(_write(course_dir, "README.md", assets.readme_en))
    written.append(_write(course_dir, "README.ja.md", assets.readme_ja))
    written.append(_write(course_dir, "course.yaml", _yaml_dump(assets.course_yaml)))
    written.append(_write(course_dir, ".r4ignore", assets.r4ignore))

    assignments_dir = course_dir / "assignments"
    assignments_dir.mkdir(parents=True, exist_ok=True)
    langs = [s for s in ("en", "ja") if lang in (s, "both")]
    for assi in _ASSIGNMENT_ASSETS:
        assi_dir = assignments_dir / f"AS{assi['id']}"
        assi_dir.mkdir(parents=True, exist_ok=True)
        for suffix in langs:
            readme_name = assi_dir / ("README.md" if suffix == "en" else "README.ja.md")
            written.append(
                _write(assi_dir, readme_name.name, _render_assignment_readme(assi, suffix))
            )
    return written


def _yaml_dump(data: dict[str, Any]) -> str:
    """Standard-library YAML for the small course.yaml payloads this module emits."""
    import json

    return json.dumps(data, indent=2, ensure_ascii=False)
