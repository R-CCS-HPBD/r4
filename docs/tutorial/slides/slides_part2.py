"""Part 2 (~3 h): a fan-out workflow on the local UMRS.

The worked example is docs/tutorial/workflows/seismic-wf (run locally with
run_local.py in 54 s on the synthetic picks, ``r4 workflow lint`` OK, 2026-10-27).
2 search steps × 81 candidate models + the ak135 baseline = 163 solves
(steps.py:36-37).

Scope pills (deck_kit.scope_tag): UMRS workflows and the agent's hand-off to
them are R4-specific (no mainstream agent harness has them); the agent's
editing and testing is the general agentic-coding pattern. Facts checked
against r4/src/r4/companion/{workflow_tools,permissions,skills,async_agents}.py,
builtin/workflow_ops/SKILL.md and umrs/src/umrs/server/workflow_runner.py at
d1569ef38 (2026-10-29).
"""

from __future__ import annotations

from deck_kit import (
    BODY_TOP,
    CONTENT_W,
    INK,
    LINE,
    MARGIN,
    MUTED,
    OCEAN,
    SLIDE_W,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    arrow,
    badge,
    card,
    chip,
    code_panel,
    content,
    divider,
    notes,
    rect,
    scope_tag,
    table,
    text_box,
)
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

KICK = ("Part 2 · A fan-out workflow", "第 2 部 · 分岐するワークフロー")
BUCKET = "<you>.default.seismic"


def _scope_pill(slide, kind: str):
    """Slide-level scope pill on the kicker line, right-aligned before the minutes chip."""
    pill = scope_tag(slide, 0, Inches(0.43), kind)
    pill.left = SLIDE_W - MARGIN - Inches(1.65) - pill.width
    return pill


def part2_divider(deck: Deck) -> None:
    slide = divider(
        deck,
        ("Part 2 · ~3 h", "第 2 部 · 約 3 時間"),
        ("A workflow that searches", "探索するワークフロー"),
        (
            'Turn "which 1-D model fits?" into a UMRS workflow: many solves fan out, one step gathers.',
            "「どの 1 次元モデルが合う?」を UMRS のワークフローにする。多数の計算を分岐させ、"
            "1 つのステップで集約する。",
        ),
        [
            (
                "Part 1: one solve — one event, one model, seconds on your laptop",
                "第 1 部:1 回の計算 — 1 つの地震・1 つのモデル・PC で数秒",
            ),
            (
                "Part 2: 163 solves — fan out, gather, rank as a UMRS workflow",
                "第 2 部:163 回の計算 — 分岐(ファンアウト)・集約・順位付け",
            ),
            (
                "Step, workflow.json, buckets, jobs, results",
                "ステップ・workflow.json・バケット・ジョブ・結果",
            ),
            ("Lab 2.4 the agent as co-author", "Lab 2.4 共著者としてのエージェント"),
            (
                "Lab 2.5 failure clinic (fix planted faults)",
                "Lab 2.5 失敗クリニック(仕込んだ不具合を直す)",
            ),
        ],
    )
    notes(
        slide,
        (
            "Built on docs/tutorial/workflows/seismic-wf. Everything runs on the laptop's "
            "stand-alone UMRS (dev profile: steps run inside the umrs container, NumPy available, "
            "nothing installed from 'requirements'). What changes from Part 1: there the agent ran "
            "each solve itself, inside its own loop (python_exec / shell_exec on the laptop), as "
            "Claude Code or opencode would. Here the computation leaves the agent: the workflow is "
            "registered with UMRS, UMRS runs it as jobs, and the agent writes it, launches it and "
            "reads the results. 163 solves = 2 search steps × 81 candidate models + the ak135 "
            "baseline (steps.py:36-37). Hyogo cut: this is the bridge slide. Slack order lives in "
            "the facilitator guide.",
            "docs/tutorial/workflows/seismic-wf を使う。すべて PC 上の単体版(stand-alone)UMRS で"
            "動く(開発用の dev プロファイル:ステップは umrs コンテナ内で実行され、NumPy は使えるが "
            "'requirements' の内容は導入されない)。第 1 部との違い:第 1 部ではエージェントが自分の"
            "ループの中で計算を実行した(PC 上の python_exec・shell_exec。Claude Code や opencode と"
            "同じやり方)。第 2 部では計算をエージェントの外に出す。ワークフローを UMRS に登録し、"
            "UMRS がジョブとして実行する。エージェントはワークフローを書き、起動し、結果を読む。"
            "163 回 = 探索ステップ 2 つ × 候補 81 モデル + ak135 の 1 回(steps.py:36-37)。"
            "兵庫講義版ではこれが"
            "橋渡しのスライド。余裕時間の使い方は講師ガイド(ハンドブック 03)にある。",
        ),
    )


def _node(slide, x, y, w, h, title, sub, fill=TINT) -> None:
    """One box of the workflow graph: mono title over a short caption."""
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    text_box(
        slide,
        x + Inches(0.08),
        y + Inches(0.07),
        w - Inches(0.16),
        Inches(0.3),
        title,
        Style(14, OCEAN, True, mono=True),
        align=PP_ALIGN.CENTER,
    )
    text_box(
        slide,
        x + Inches(0.08),
        y + Inches(0.38),
        w - Inches(0.16),
        h - Inches(0.42),
        sub,
        Style(12, INK),
        align=PP_ALIGN.CENTER,
    )


def _workflow_graph(slide, y) -> None:
    """bucket → (search_low ‖ search_high) → rank → report.md, 1.5 in tall."""
    box_w, mid_w, arrow_w, gap = Inches(2.35), Inches(2.75), Inches(0.55), Inches(0.12)
    half = Inches(0.69)
    x = MARGIN
    _node(
        slide,
        x,
        y,
        box_w,
        Inches(1.5),
        ("bucket", "バケット"),
        ("event.json, p_picks.csv", "event.json と p_picks.csv"),
    )
    x += box_w + gap
    for i, (name, sub) in enumerate(
        [
            ("search_low", ("81 models + ak135", "81 モデル+ak135")),
            ("search_high", ("81 models, with search_low", "81 モデル・search_low と同時")),
        ]
    ):
        row_y = y + i * (half + Inches(0.12))
        arrow(slide, x, row_y + half / 2 - Inches(0.13), arrow_w, Inches(0.26))
        _node(slide, x + arrow_w + gap, row_y, mid_w, half, name, sub)
        arrow(
            slide,
            x + arrow_w + mid_w + 2 * gap,
            row_y + half / 2 - Inches(0.13),
            arrow_w,
            Inches(0.26),
        )
    x += 2 * arrow_w + mid_w + 3 * gap
    _node(
        slide,
        x,
        y,
        box_w,
        Inches(1.5),
        "rank",
        ("waits for both, gathers", "両方を待って集約"),
        fill=TINT_AMBER,
    )
    x += box_w + gap
    arrow(slide, x, y + Inches(0.6), arrow_w, Inches(0.3))
    x += arrow_w + gap
    _node(slide, x, y, MARGIN + CONTENT_W - x, Inches(1.5), "report.md", ("the result", "結果"))


def concepts_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Five words: step, workflow, bucket, job, result", "ワークフローの 5 つの基本用語"),
        KICK,
        ("0–20 min", "0–20 分"),
    )
    _scope_pill(slide, "r4")
    _workflow_graph(slide, BODY_TOP)
    rows = [
        [("Word", "用語"), ("What it is here", "ここでの意味")],
        [
            ("Step", "ステップ"),
            (
                "a Python class: run(ctx) returns StepResult(output_keys)",
                "run(ctx) が StepResult(output_keys) を返す Python クラス",
            ),
        ],
        [
            ("workflow.json", "workflow.json"),
            (
                "names each step, its entrypoint (the class to run), config, depends_on",
                "各ステップのエントリポイント(実行するクラス)・設定・depends_on"
                "(先に終わるべきステップ)の定義",
            ),
        ],
        [
            ("Bucket", "バケット"),
            (
                "object storage on your disk: inputs go in; outputs go to <bucket>-output",
                "PC 上の保存場所(オブジェクトストレージ)。入力を置き、出力は <bucket>-output に入る",
            ),
        ],
        [
            ("Job / execution", "ジョブ/実行"),
            (
                "one run, kept by UMRS: status and results survive a laptop or UMRS restart "
                "and outlive your chat",
                "1 回の実行。UMRS が記録するので、PC や UMRS を再起動しても、チャットを閉じても"
                "状態と結果が残る",
            ),
        ],
        [
            ("Result", "結果"),
            (
                "the files a step lists in output_keys, kept in <bucket>-output; "
                "rank writes report.md and best_model.json",
                "ステップが output_keys に挙げたファイル。<bucket>-output に入る。"
                "rank は report.md と best_model.json を書く",
            ),
        ],
    ]
    table(
        slide,
        MARGIN,
        BODY_TOP + Inches(1.75),
        CONTENT_W,
        rows,
        [2.0, 10.1],
        size=13,
        row_h=Inches(0.44),
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.5),
        CONTENT_W,
        Inches(0.55),
        (
            "**R4-specific:** the agent hands this graph to UMRS, which runs it as jobs that "
            "outlive the chat. The graph itself (steps + depends_on) has the same shape as in "
            "Snakemake or Nextflow.",
            "**R4 独自なのは、**エージェントがこのグラフを UMRS に渡し、UMRS がチャットを閉じても"
            "残るジョブとして実行する点。グラフの形(ステップ + depends_on)自体は Snakemake や "
            "Nextflow と同じ。",
        ),
        Style(12, MUTED),
    )
    notes(
        slide,
        (
            "20 min with Q&A. The two search steps have no depends_on, so UMRS may run them at "
            "the same time; rank depends on both and receives both output keys as ctx.input_keys. "
            "entrypoint = 'module:Class' (e.g. steps:ModelSearchStep), the step class UMRS loads "
            "from the bundle (the folder with workflow.json and the step code); ctx = the step's "
            "handle on its inputs and outputs. routing_hints (where a step should run: GPU, "
            "container image) is a sixth field; it matters in Part 3. Badge: steps plus "
            "depends_on is the shape of every workflow engine "
            "(Snakemake, Nextflow, Airflow; UMRS can import Snakemake, CWL and Nextflow "
            "definitions, umrs/src/umrs/workflow_import/). It is marked R4-specific because no "
            "agent harness has it: Claude Code, opencode and the OpenAI Agents SDK run commands "
            "on your machine; handing a dependency graph to a scheduler as durable jobs (the "
            "agent's workflow_* tools, r4/src/r4/companion/workflow_tools.py) is the platform's "
            "job (R4 + UMRS).",
            "質疑込み 20 分。2 つの探索ステップは depends_on を持たないので、UMRS は同時に実行できる。"
            "rank は両方に依存し、両方の出力キーを ctx.input_keys として受け取る。エントリポイントは、"
            "UMRS がバンドル(workflow.json とステップのコードをまとめたフォルダ)から読み込む"
            "ステップのクラスを、'module:Class' の形(例 steps:ModelSearchStep)で指定したもの。"
            "ctx = ステップが入力と出力を扱う窓口。routing_hints(ステップの実行先の指定。GPU・"
            "コンテナイメージ)は 6 つ目の項目で、第 3 部で使う。"
            "印について:ステップと depends_on の組み合わせはワークフローエンジンに共通の形"
            "(Snakemake・Nextflow・Airflow。UMRS は Snakemake・CWL・Nextflow の定義を取り込める。"
            "umrs/src/umrs/workflow_import/)。それでも「R4 独自」とするのは、エージェントの"
            "ハーネスにはこの仕組みがないため。Claude Code・opencode・OpenAI Agents SDK は手元の PC で"
            "コマンドを動かすだけ。依存関係のグラフを、再起動後も残るジョブとしてスケジューラに渡すのは、"
            "プラットフォーム(R4 + UMRS)の役割(エージェントの workflow_* ツール。"
            "r4/src/r4/companion/workflow_tools.py)。",
        ),
    )


def example_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("The worked example: which crust fits the picks?", "例題:どの地殻構造が読み取りに合うか"),
        KICK,
    )
    rows = [
        [
            ("Model (layer top km: velocity km/s)", "モデル(層上端の深さ km:速度 km/s)"),
            ("RMS", "RMS"),
            ("Mean", "平均"),
        ],
        [
            ("ak135 — 0: 5.8 · 20: 6.5 · 35: 8.04", "ak135 — 0: 5.8 · 20: 6.5 · 35: 8.04"),
            "1.48 s",
            "−1.30 s",
        ],
        ["0: 6.1 · 18: 6.9 · 26: 7.9", "0.21 s", "−0.01 s"],
        ["0: 6.1 · 18: 6.9 · 28: 7.9", "0.21 s", "−0.01 s"],
        ["0: 5.9 · 18: 6.7 · 26: 8.1", "0.24 s", "−0.06 s"],
    ]
    table(slide, MARGIN, BODY_TOP, Inches(8.0), rows, [4.6, 1.5, 1.5], size=14, row_h=Inches(0.6))
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.2),
        Inches(8.0),
        Inches(1.2),
        (
            "The top results the workflow prints for the offline synthetic picks (4 km grid, "
            "163 solves: 162 candidate models + ak135, 54 s on a laptop). Lab 2.2 runs it on the "
            "real ISC picks, so your numbers will differ.",
            "オフライン用の合成データに対してワークフローが出力した上位の結果(4 km 格子、163 回の"
            "計算 = 候補 162 モデル+ak135、PC で 54 秒)。Lab 2.2 では実際の ISC の読み取りを使う"
            "ので、数値は異なる。",
        ),
        Style(13, MUTED, italic=True),
    )
    card(
        slide,
        MARGIN + Inches(8.4),
        BODY_TOP,
        CONTENT_W - Inches(8.4),
        Inches(4.6),
        ("What the numbers say", "数値から分かること"),
        body_size=14,
        items=[
            ("ak135 is ~1.3 s too slow for these paths.", "この経路では ak135 は約 1.3 秒遅い。"),
            (
                "Many models fit almost equally: a faster crust over a deeper Moho gives nearly "
                "the same travel times (a trade-off).",
                "多くのモデルがほぼ同じくらい合う。速度を上げてもモホ面を深くすれば、走時はほぼ"
                "変わらない(トレードオフ)。",
            ),
            (
                "Moho 26 and 28 km have the same RMS: a 4 km grid cannot tell them apart.",
                "モホ面 26 km と 28 km の RMS が同じ:4 km 格子では区別できない。",
            ),
        ],
    )
    notes(
        slide,
        (
            "Show the destination first so the labs have a purpose. On the real ISC picks with a 2 km "
            "grid: ak135 1.32 s, best 0.44 s, KRSO-1D v2 0.46 s (docs/tutorial/data/README.md).",
            "先にゴールを見せ、ラボの目的をはっきりさせる。実際の ISC 読み取りと 2 km 格子では:"
            "ak135 1.32 秒、最良 0.44 秒、KRSO-1D v2 0.46 秒(docs/tutorial/data/README.md)。",
        ),
    )


def lab21_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 2.1 — A one-step workflow", "Lab 2.1 — 1 ステップのワークフロー"),
        KICK,
        ("20–50 min", "20–50 分"),
    )
    _scope_pill(slide, "r4")
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(6.5),
        Inches(4.65),
        "# steps.py\n"
        "class ModelSearchStep(Step):\n"
        '    name = "search"\n'
        "    def setup(self, config):\n"
        "        self.config = config or {}\n"
        "    def run(self, ctx):\n"
        "        event, picks = _inputs(ctx, self.config)\n"
        "        scores = [core.score(event, picks, m, h)\n"
        "                  for m in core.candidates(self.config)]\n"
        "        # step_name is filled in by UMRS (search_low, …)\n"
        "        key = f\"{self.config['step_name']}.json\"\n"
        "        ctx.upload_bytes(key, json.dumps(scores).encode())\n"
        "        return StepResult(output_keys=[key])",
        size=12,
        title=("steps.py (shortened)", "steps.py(抜粋)"),
    )
    x = MARGIN + Inches(6.8)
    w = CONTENT_W - Inches(6.8)
    code_panel(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.45),
        '"steps": [{\n'
        '  "name": "search_low",\n'
        '  "entrypoint": "steps:ModelSearchStep",\n'
        '  "config": {"moho_km": [24, 26, 28], ...},\n'
        '  "description": "...", "depends_on": []\n'
        "}]",
        size=12,
        title=("workflow.json", "workflow.json"),
    )
    code_panel(
        slide,
        x,
        BODY_TOP + Inches(2.65),
        w,
        Inches(2.0),
        "!cp -r docs/tutorial/workflows/seismic-wf my-seismic-wf\n"
        "# keep only search_low in my-seismic-wf/workflow.json\n"
        "/code run my-seismic-wf/run_local.py --timeout 300\n"
        "   -- --data <picks>\n"
        "   --workflow my-seismic-wf/workflow.json\n"
        "!r4 workflow lint my-seismic-wf",
        size=10,
        title=("console", "コンソール"),
    )
    notes(
        slide,
        (
            "30 min. bundle = the folder with workflow.json and the step code. There is no "
            "server-side dry run: lint checks the bundle's shape (every step needs description and "
            "depends_on; an upload without them is refused), and run_local.py runs the steps on the "
            "laptop with folders as buckets. Copy the bundle to my-seismic-wf and cut its workflow.json "
            "to search_low first: run_local.py runs every step of the workflow.json it is given (by "
            "default the original three-step bundle). The /code run command is one line; the slide "
            "wraps it. config['step_name'] is not in workflow.json: the runner (UMRS, and run_local.py "
            "locally) sets it to the step's name, so one class serves several steps. Steps may "
            "import the standard library, NumPy and the umrs SDK only, so the bundle carries its own copy of the NumPy solver (eikonal.py). "
            "The step API (umrs.pipeline_sdk): ctx.download_bytes / ctx.upload_bytes read and write "
            "the bucket; StepResult(output_keys) tells UMRS the keys of the files the step wrote. "
            "Badge: the step "
            "SDK and the bundle format are UMRS's own; no agent harness has an equivalent.",
            "30 分。バンドルは workflow.json とステップのコードをまとめたフォルダ。サーバ側の試し実行"
            "(ドライラン)はない。lint はバンドルの形式を検査し(各ステップに description と "
            "depends_on が必須。欠けていると登録も拒否される)、run_local.py はフォルダをバケットに"
            "見立てて PC 上でステップを実行する。先にバンドルを my-seismic-wf にコピーし、その "
            "workflow.json を search_low だけに絞っておくこと。run_local.py は渡された workflow.json の"
            "ステップをすべて実行する(既定は元の 3 ステップのバンドル)。/code run のコマンドは 1 行で"
            "入力する(スライドでは折り返して表示)。config['step_name'] は workflow.json には書かない。"
            "実行する側(UMRS、手元では run_local.py)がステップ名を入れるので、1 つのクラスを複数の"
            "ステップで使い回せる。ステップが import できるのは標準ライブラリ・NumPy・"
            "umrs SDK だけなので、バンドルは NumPy 版ソルバのコピー(eikonal.py)を同梱している。"
            "ステップ用の API は umrs.pipeline_sdk にある。ctx.download_bytes・ctx.upload_bytes で"
            "バケットを読み書きし、StepResult(output_keys) で出力したファイルのキーを UMRS に伝える。"
            "印について:ステップの SDK とバンドルの形式は UMRS 独自のもので、一般的なハーネスには"
            "相当するものがない。",
        ),
    )


def _lab22_steps(slide, x, w) -> None:
    for i, (head, sub_) in enumerate(
        [
            (("bucket", "バケット"), ("inputs: you upload", "入力:自分で置く")),
            (("run", "実行"), ("UMRS schedules the steps", "UMRS がステップを実行")),
            (("results", "結果"), ("status, output keys, errors", "状態・出力キー・エラー")),
        ]
    ):
        y = BODY_TOP + i * Inches(1.12)
        badge(slide, x, y, str(i + 1))
        text_box(
            slide, x + Inches(0.65), y, w - Inches(0.65), Inches(0.4), head, Style(17, INK, True)
        )
        text_box(
            slide,
            x + Inches(0.65),
            y + Inches(0.42),
            w - Inches(0.65),
            Inches(0.62),
            sub_,
            Style(14, MUTED),
        )


def lab22_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 2.2 — Upload the picks, run, read the results",
            "Lab 2.2 — データをアップロードし、実行し、結果を読む",
        ),
        KICK,
        ("50–70 min", "50–70 分"),
    )
    _scope_pill(slide, "r4")
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(8.3),
        Inches(4.65),
        "# 1  a bucket (+ its -output twin) — no slash command yet\n"
        "!r4 bucket create default seismic\n"
        "!r4 bucket list                    # note the full name\n"
        "!r4 files upload <picks>/event.json  --bucket " + BUCKET + "\n"
        "!r4 files upload <picks>/p_picks.csv --bucket " + BUCKET + "\n"
        "\n"
        "# 2  upload the bundle and run it\n"
        "/workflow upload docs/tutorial/workflows/seismic-wf\n"
        "/workflow run seismic-model-search --param bucket=" + BUCKET + "\n"
        "/work                      # watch it (Alt+W: graph)\n"
        "/workflow results <execution-id>",
        size=12,
        title=("console", "コンソール"),
    )
    x = MARGIN + Inches(8.7)
    w = CONTENT_W - Inches(8.7)
    _lab22_steps(slide, x, w)
    chip(slide, x, BODY_TOP + Inches(3.4), ("then", "次に"), fill=OCEAN, color=WHITE)
    text_box(
        slide,
        x,
        BODY_TOP + Inches(3.82),
        w,
        Inches(1.25),
        (
            "10-min break (70–80). A run still red? Leave it open — staff fix it after the break. "
            "Stretch: ask the agent to fetch report.md.",
            "10 分休憩(70–80 分)。実行がまだ失敗(赤)のままなら、画面をそのままにしてください。"
            "休憩後にスタッフが直します。発展:report.md の取得をエージェントに頼んでみてください。",
        ),
        Style(12, INK),
    )
    notes(
        slide,
        (
            "20 min. Placeholders: <you> = your user name, <picks> = the picks folder "
            "(docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03, or synthetic-kii), "
            "<execution-id> = the run ID that /workflow run prints. /files upload has no bucket "
            "option and the console has no bucket create, so step 1 uses !r4 …. 'bucket' is a "
            "workflow parameter: the runner reads inputs from it and writes to '<bucket>-output'. "
            "The /workflow commands go straight to UMRS: no model call, no approval prompt. "
            "/workflow run --wait is capped at 20 s (r4/src/r4/tui/commands.py:2039); watch the "
            "Work pane instead. The agent reaches the same UMRS API through its workflow_* tools "
            "(Lab 2.4). The 10-min break follows (70–80).",
            "20 分。プレースホルダ:<you> = 自分のユーザ名、<picks> = 読み取りデータのフォルダ"
            "(docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 または synthetic-kii)、"
            "<execution-id> = 実行 ID(/workflow run が表示する)。/files upload にはバケットの指定が"
            "なく、コンソールにはバケット作成のコマンドもないので、手順 1 は !r4 … を使う。'bucket' は"
            "ワークフローのパラメータで、入力をそこから読み、'<bucket>-output' に書き出す。/workflow "
            "の各コマンドは UMRS を直接呼ぶ(モデルは介在せず、承認の確認もない)。/workflow run の "
            "--wait は 20 秒が上限(r4/src/r4/tui/commands.py:2039)なので、代わりに Work ペインで"
            "進行を見る。エージェントは workflow_* ツールで同じ UMRS の API を使う(Lab 2.4)。"
            "続いて 10 分休憩(70–80 分)。",
        ),
    )


def _fanout_diagram(slide) -> None:
    y0 = BODY_TOP + Inches(0.1)
    for i, name in enumerate(("search_low", "search_high")):
        y = y0 + i * Inches(1.5)
        rect(slide, MARGIN, y, Inches(2.6), Inches(1.1), fill=TINT, rounded=True)
        text_box(
            slide,
            MARGIN,
            y + Inches(0.15),
            Inches(2.6),
            Inches(0.4),
            name,
            Style(15, OCEAN, True, mono=True),
            align=PP_ALIGN.CENTER,
        )
        text_box(
            slide,
            MARGIN,
            y + Inches(0.6),
            Inches(2.6),
            Inches(0.4),
            ("depends_on: []", "depends_on: []"),
            Style(12, INK, mono=True),
            align=PP_ALIGN.CENTER,
        )
        arrow(slide, MARGIN + Inches(2.7), y + Inches(0.38), Inches(0.9), Inches(0.34))
    rect(
        slide,
        MARGIN + Inches(3.7),
        y0 + Inches(0.6),
        Inches(2.6),
        Inches(1.4),
        fill=TINT_AMBER,
        rounded=True,
    )
    text_box(
        slide,
        MARGIN + Inches(3.7),
        y0 + Inches(0.75),
        Inches(2.6),
        Inches(0.4),
        "rank",
        Style(15, INK, True, mono=True),
        align=PP_ALIGN.CENTER,
    )
    text_box(
        slide,
        MARGIN + Inches(3.7),
        y0 + Inches(1.2),
        Inches(2.6),
        Inches(0.7),
        "depends_on:\n[search_low, search_high]",
        Style(11, INK, mono=True),
        align=PP_ALIGN.CENTER,
    )


def _subagent_note(slide, x, y, w) -> None:
    """Lab 2.3 side box: the agent's own fan-out (sub-agents) vs UMRS's, with its own chip."""
    rect(slide, x, y, w, Inches(1.65), fill=WHITE, line=LINE, rounded=True)
    pad = Inches(0.2)
    scope_tag(slide, x + pad, y + Inches(0.14), "r4tuned")
    text_box(
        slide,
        x + pad,
        y + Inches(0.48),
        w - 2 * pad,
        Inches(1.1),
        (
            "Not the agent's own fan-out: R4's sub-agents share language-model work inside one "
            "session (≤ 4 at once, like Claude Code's background subagents); UMRS runs the steps "
            "of one run side by side (≤ 8 at once).",
            "エージェント自身の分岐とは別物:R4 のサブエージェント(作業を任される子のエージェント)は"
            "1 つのセッション内で言語モデルの作業を分担する(同時に 4 まで。Claude Code の"
            "バックグラウンドのサブエージェントと同じ考え方)。UMRS は 1 回の実行の中でステップを"
            "並べて動かす(同時に 8 まで)。",
        ),
        Style(12, INK),
    )


def lab23_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 2.3 — Fan out, then gather", "Lab 2.3 — 分岐(ファンアウト)と集約"),
        KICK,
        ("80–120 min", "80–120 分"),
    )
    _scope_pill(slide, "r4")
    _fanout_diagram(slide)
    code_panel(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.0),
        Inches(6.3),
        Inches(1.65),
        "for key in ctx.input_keys:      # both parents\n"
        "    scores += json.loads(ctx.download_parent_bytes(key))\n"
        "ranked = sorted(scores, key=rms)",
        size=12,
        title=("RankStep.run (shortened)", "RankStep.run(抜粋)"),
    )
    card(
        slide,
        MARGIN + Inches(6.7),
        BODY_TOP,
        CONTENT_W - Inches(6.7),
        Inches(3.3),
        ("What to notice", "注目点"),
        body_size=13,
        items=[
            (
                "Independent steps may run at the same time. The speed-up comes on a cluster, "
                "where each step is its own job; on the laptop (dev profile) they share one "
                "Python process and save little time.",
                "互いに依存しないステップは同時に実行できる。速くなるのはクラスタで、各ステップが"
                "別のジョブになるとき。PC の開発用設定(dev プロファイル)では 1 つの Python "
                "プロセスを分け合うので、ほとんど速くならない。",
            ),
            (
                "rank waits for both and receives both output keys in ctx.input_keys.",
                "rank は両方の完了を待ち、ctx.input_keys で両方の出力キーを受け取る。",
            ),
            (
                "More candidate velocity models? Add a third search step; rank does not change.",
                "試す速度モデルを増やすには、3 つ目の探索ステップを足すだけ。rank は変更不要。",
            ),
        ],
    )
    _subagent_note(slide, MARGIN + Inches(6.7), BODY_TOP + Inches(3.45), CONTENT_W - Inches(6.7))
    notes(
        slide,
        (
            "40 min. With an empty depends_on, rank starts at once and finds nothing to rank "
            "(fault f4); with no depends_on key at all, the upload is refused "
            "(r4/src/r4/workflow_lint.py:10-14). "
            "Re-upload with /workflow upload, re-run with /workflow run, watch /work. How UMRS does "
            "it: it sorts the steps into levels by depends_on and runs each level on a thread pool, "
            "up to 8 steps at once (UMRS_RUNNER_MAX_PARALLEL_STEPS; "
            "umrs/src/umrs/server/workflow_runner.py:78-97, 356-385); a step's ctx.input_keys is "
            "its parents' output_keys joined in depends_on order (workflow_runner.py:738-747). Where the speed-up "
            "is: the laptop's dev profile sets UMRS_STEP_EXECUTOR=inproc "
            "(deploy/standalone/env/profile.dev.env:23), so every step runs inside the runner's "
            "own process (umrs/src/umrs/server/step_executor.py:282-290) and the NumPy sweeps "
            "share one interpreter lock (GIL). Measured 2026-10-29: two halves of the search in a "
            "2-thread pool ran no faster than one after the other (0.7-1.0x). On a cluster "
            "(slurm executor) each step is its own job, and the fan-out pays. The agent's own "
            "fan-out is a different thing: R4's background sub-agents (async_agent_spawn, at most "
            "4 running, r4/src/r4/companion/async_agents.py:24) share out model work inside one "
            "session, like Claude Code's background subagents (badge: general pattern, R4's "
            "setting; the cap of 4 is R4's). Neither runs your computation as jobs.",
            "40 分。depends_on が空だと rank はすぐに始まり、順位付けするものがない(不具合 f4)。"
            "depends_on の項目そのものがないと登録が拒否される(r4/src/r4/workflow_lint.py:10-14)。"
            "/workflow upload で再登録し、/workflow run で再実行し、/work で見守る。UMRS の動き:"
            "depends_on からステップを段に分け、各段をスレッドで並列に実行する(同時に最大 8 ステップ。"
            "UMRS_RUNNER_MAX_PARALLEL_STEPS。umrs/src/umrs/server/workflow_runner.py:78-97, "
            "356-385)。ステップの ctx.input_keys は、親ステップの output_keys を depends_on の順に"
            "つなげたもの(workflow_runner.py:738-747)。高速化が効く場所:PC の dev プロファイルは "
            "UMRS_STEP_EXECUTOR=inproc に設定する(deploy/standalone/env/profile.dev.env:23)ので、各ステップは"
            "実行役と同じプロセスの中で動き(umrs/src/umrs/server/step_executor.py:282-290)、NumPy の"
            "計算は 1 つのインタプリタロック(GIL)を分け合う。2026-10-29 の実測では、探索を半分ずつ "
            "2 スレッドで動かしても、順に動かすのと比べて速くならなかった(0.7〜1.0 倍)。クラスタ"
            "(slurm で実行)では各ステップが別のジョブになり、分岐の効果が出る。"
            "エージェント自身の分岐はこれとは別物。R4 のバックグラウンドの"
            "サブエージェント(async_agent_spawn、同時実行は最大 4。"
            "r4/src/r4/companion/async_agents.py:24)は、1 つのセッションの中で言語モデルの作業を"
            "分担する。Claude Code のバックグラウンドのサブエージェントと同じ考え方(印は「一般的な"
            "仕組み・R4 の設定」。上限 4 は R4 の値)。どちらも計算をジョブとしては実行しない。",
        ),
    )


LAB24_TASKS = [
    (
        ("Reuse existing code", "既存コードの再利用"),
        (
            "“Turn workflows/legacy/plot_travel_times.py into a fourth step 'plot' after 'rank'. "
            "No matplotlib in steps (only the standard library, NumPy, the umrs SDK): write the "
            "SVG by hand.”",
            "「workflows/legacy/plot_travel_times.py を、'rank' の後の 4 つ目のステップ 'plot' に"
            "して。matplotlib は使えない(標準ライブラリ・NumPy・umrs SDK のみ)ので SVG を自分で"
            "書いて。」",
        ),
    ),
    (
        ("Pull in an outside skill", "外部スキルの取り込み"),
        (
            "r4 skill github install <owner>/<repo>/skills/<name>",
            "r4 skill github install <owner>/<repo>/skills/<name>",
        ),
    ),
    (
        ("Debug from the error", "エラーから直す"),
        (
            "“The run failed. Read 'r4 workflow results <id>', find the cause, fix it, re-run.”",
            "「実行が失敗した。'r4 workflow results <id>' を読んで原因を見つけ、直して再実行して。」",
        ),
    ),
]

# What happens inside R4 when the agent co-authors a workflow (top to bottom).
AGENT_ROWS = [
    (
        "r4",
        (
            "Skill by keyword: the English word “workflow” in your request turns on workflow_ops "
            "(plus platform_ops, its dependency); its authoring loop applies from this turn for "
            "the rest of the session.",
            "キーワードでスキルが有効に:依頼文に英単語 workflow があると workflow_ops(と依存先の "
            "platform_ops)が有効になり、以後セッション中は作成手順が加わる。「ワークフロー」では"
            "有効にならない。",
        ),
    ),
    (
        "general",
        (
            "Writes steps.py and workflow.json, runs run_local.py and lint in the shell, reads "
            "the output, fixes: ordinary agentic coding.",
            "steps.py と workflow.json を書き、シェルで run_local.py と lint を実行し、出力を読んで"
            "直す。通常のエージェントによるコーディング。",
        ),
    ),
    (
        "r4",
        (
            "Hands off to UMRS: workflow_upload → describe (check the step graph) → run → wait "
            "(≤ 300 s per call) → results. The run is a UMRS job; it outlives the chat.",
            "UMRS への引き渡し:workflow_upload → describe(ステップのつながりを確認)→ run → "
            "wait(1 回 300 秒まで)→ results。実行は UMRS のジョブで、チャットを閉じても続く。",
        ),
    ),
    (
        "r4tuned",
        (
            "Approval: list, describe, status, wait and results only read, so chat mode runs "
            "them without asking; upload, run and cancel ask first (y / a / n), unless you "
            "are in autopilot.",
            "承認:list・describe・status・wait・results は読むだけなので chat モードでは確認"
            "なし。upload・run・cancel は実行前に承認を求める(y / a / n)。autopilot では"
            "確認しない。",
        ),
    ),
    (
        "r4tuned",
        (
            "Errors come back as JSON (code, message); the skill says: read it as is, fix one "
            "thing, re-upload under a new version, re-run.",
            "エラーは JSON(code・message)で返る。スキルの指示は「エラーをそのまま読み、1 か所だけ"
            "直し、版を上げて再登録し、再実行」。",
        ),
    ),
]

# Vertical step per AGENT_ROWS entry (inches); rows 1, 3 and 4 wrap to three lines in JA.
AGENT_ROW_STEPS = (0.98, 0.8, 0.98, 0.98, 0.84)


def _lab24_tasks(slide, w) -> None:
    for i, (head, text) in enumerate(LAB24_TASKS):
        y = BODY_TOP + i * Inches(1.18)
        badge(slide, MARGIN, y + Inches(0.02), str(i + 1), size=Inches(0.42), font=14)
        text_box(
            slide,
            MARGIN + Inches(0.58),
            y,
            w - Inches(0.58),
            Inches(0.36),
            head,
            Style(15, INK, True),
        )
        text_box(
            slide,
            MARGIN + Inches(0.58),
            y + Inches(0.38),
            w - Inches(0.58),
            Inches(0.78),
            text,
            Style(12, INK, italic=i != 1, mono=i == 1),
        )


def _lab24_compare(slide, y, w) -> None:
    rect(slide, MARGIN, y, w, Inches(1.05), fill=TINT, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.2),
        y + Inches(0.14),
        w - Inches(0.4),
        Inches(1.0),
        (
            "**Compare the agent's step with yours:** no matplotlib import? The step added to "
            "workflow.json with depends_on? run_local.py and lint passed before the upload?",
            "**エージェントの成果を自分の版と比べる:**matplotlib を import していないか? "
            "workflow.json に depends_on 付きで追加したか? アップロード前に run_local.py と lint を"
            "通したか?",
        ),
        Style(13, INK),
    )


def _agent_panel(slide, x, w) -> None:
    rect(slide, x, BODY_TOP, w, Inches(5.1), fill=WHITE, line=LINE, rounded=True)
    pad = Inches(0.2)
    text_box(
        slide,
        x + pad,
        BODY_TOP + Inches(0.12),
        w - 2 * pad,
        Inches(0.36),
        ("Inside R4: how the agent drives UMRS", "R4 の中で起きること:エージェントと UMRS"),
        Style(15, INK, True),
    )
    y = BODY_TOP + Inches(0.52)
    for (kind, text), step in zip(AGENT_ROWS, AGENT_ROW_STEPS, strict=True):
        scope_tag(slide, x + pad, y, kind)
        box_h = Inches(step - 0.32)
        text_box(slide, x + pad, y + Inches(0.3), w - 2 * pad, box_h, text, Style(11, INK))
        y += Inches(step)


def lab24_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 2.4 — The agent as co-author", "Lab 2.4 — 共著者としてのエージェント"),
        KICK,
        ("120–150 min", "120–150 分"),
    )
    left_w = Inches(6.25)
    _lab24_tasks(slide, left_w)
    _lab24_compare(slide, BODY_TOP + Inches(3.62), left_w)
    _agent_panel(slide, MARGIN + left_w + Inches(0.3), CONTENT_W - left_w - Inches(0.3))
    notes(
        slide,
        (
            "30 min. Skills install from a GitHub owner/repo path (r4 skill github install); "
            "installing by a full URL is not supported yet, so name the shared catalogue repo on "
            "the day. Right panel, top to bottom: (1) workflow_ops activates when your message "
            "holds 'workflow(s)', 'pipeline', 'dag' … as a whole English word "
            "(builtin/workflow_ops/SKILL.md:6; skills.py:159-177). Japanese words do not match; "
            "the path workflows/… in task 1 does. Its allowed-tools refuse other domain tools; "
            "file and shell tools stay. Claude Code instead lets the model pick a skill from its "
            "description. Once on, the skill (and platform_ops, its x-r4-dependencies) stays "
            "active for the rest of the session (skills.py:312-342), so its allowed-tools "
            "narrowing lasts too. (2) Editing and testing is ordinary agentic coding, as in "
            "Claude Code or opencode. (3) The hand-off is R4's: workflow_upload / describe / run / "
            "wait / results (r4/src/r4/companion/workflow_tools.py:16; wait bounded at 300 s per "
            "call); the skill has the agent call workflow_describe after the upload to check the "
            "step graph (SKILL.md:26-43). With no "
            "live UMRS the workflow_* tools are not offered at all (tool_availability.py:92-120). "
            "Claude Code has no job service of its own: it would run the steps in its shell "
            "(background shells end with the session) unless you have it call a scheduler such "
            "as sbatch yourself. R4 hands the run to UMRS as a durable job. (4) The read-only "
            "workflow tools are in SAFE_TOOLS (permissions.py:92-96), run / cancel / upload in "
            "RISKY_TOOLS (permissions.py:133, 147-148); none of them is never-auto, so the "
            "card offers y / a / n (ADR-104: the companion decides the offer and keeps a, "
            "never the console; amendment 1 removed p). A per-project workflow policy "
            "can veto any operation; upload, run and cancel fail closed when that policy file is "
            "unreadable (workflow_policy.py). (5) Errors return as JSON {code, message, details}; "
            "the skill's loop is: read it verbatim, fix ONE thing, re-upload under a bumped "
            "version, re-run "
            "(SKILL.md:26-43). Returning a tool error to the model as a result is general (Claude "
            "Code's is_error results, the OpenAI Agents SDK's failure_error_function).",
            "30 分。スキルは GitHub の owner/repo 形式で導入する(r4 skill github install)。完全な URL "
            "での導入はまだできないので、当日は共有カタログのリポジトリ名を示す。右の枠(上から順に)。"
            "(1)workflow_ops は、依頼文に 'workflow(s)'・'pipeline'・'dag' などの英単語が単語として"
            "含まれると有効になる(builtin/workflow_ops/SKILL.md:6、skills.py:159-177)。日本語の語は"
            "一致しないが、課題 1 の workflows/… というパスは一致する。スキルの allowed-tools は他の"
            "分野のツールを拒否するが、ファイルとシェルのツールは残る。Claude Code では、説明文を見て"
            "モデル自身がスキルを選ぶ。一度有効になったスキル(と x-r4-dependencies の platform_ops)は"
            "セッションの間ずっと有効なので(skills.py:312-342)、allowed-tools による絞り込みも続く。"
            "(2)編集とテストは通常のエージェントによるコーディングで、"
            "Claude Code や opencode と同じ。(3)引き渡しは R4 独自:workflow_upload・describe・run・"
            "wait・results(r4/src/r4/companion/workflow_tools.py:16。wait は 1 回 300 秒まで)。"
            "スキルはアップロード後に workflow_describe でステップのつながりを確かめさせる"
            "(SKILL.md:26-43)。UMRS に"
            "つながらないときは workflow_* ツール自体が提示されない(tool_availability.py:92-120)。"
            "Claude Code には独自のジョブ管理がなく、ステップは自分のシェルで動かす(バックグラウンドの"
            "シェルはセッションとともに終わる)。sbatch などのスケジューラを呼ぶよう指示すれば別だが、"
            "それは利用者の仕事。R4 は実行を UMRS に渡し、再起動後も残るジョブにする。"
            "(4)読み取り専用の workflow ツールは SAFE_TOOLS に入り(permissions.py:92-96)、run・"
            "cancel・upload は RISKY_TOOLS に入る(permissions.py:133、147-148)。どれも常に人が"
            "承認する対象(never-auto)ではないので、カードは y / a / n を出す(ADR-104:出す答え"
            "はコンパニオンが決め、a の記憶もコンパニオンが持つ。コンソールではない。p は改訂 1 で"
            "廃止)。"
            "プロジェクトごとのワークフロー"
            "方針でどの操作も禁止でき、方針ファイルが読めないときは upload・run・cancel は安全側に"
            "倒して拒否される(workflow_policy.py)。(5)エラーは JSON {code, message, details} で返る。"
            "スキルの手順は「エラーをそのまま読み、1 か所だけ直し、版を上げて再登録し、再実行」"
            "(SKILL.md:26-43)。ツールのエラーを"
            "結果としてモデルに返すのは一般的な仕組み(Claude Code の is_error、OpenAI Agents SDK の "
            "failure_error_function)。",
        ),
    )


def clinic_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 2.5 — Failure clinic: four planted faults",
            "Lab 2.5 — 失敗クリニック:仕込まれた 4 つの不具合",
        ),
        KICK,
        ("150–170 min", "150–170 分"),
    )
    _scope_pill(slide, "r4")
    rows = [
        [("Bundle", "バンドル"), ("Symptom", "症状"), ("Fix", "修正")],
        [
            "f1-bad-entrypoint",
            ("search_low fails before it runs", "search_low が実行前に失敗する"),
            ("entrypoint names a class that exists", "entrypoint を実在するクラス名に直す"),
        ],
        [
            "f2-missing-key",
            ("search_high cannot read its input", "search_high が入力を読めない"),
            ("picks_key = the uploaded file", "picks_key をアップロード済みのファイル名に直す"),
        ],
        [
            "f3-timeout",
            ("search_low runs past its 5 s budget", "search_low が 5 秒の制限を超える"),
            ("drop the delay or raise timeout_seconds", "遅延を外すか timeout_seconds を増やす"),
        ],
        [
            "f4-lost-parents",
            ("rank gets nothing to rank", "rank に順位付けする入力が届かない"),
            (
                "rank.depends_on = [search_low, search_high]",
                "rank の depends_on に両方の探索ステップを書く",
            ),
        ],
    ]
    table(slide, MARGIN, BODY_TOP, Inches(8.4), rows, [2.2, 2.9, 3.3], size=13, row_h=Inches(0.62))
    code_panel(
        slide,
        MARGIN + Inches(8.7),
        BODY_TOP,
        CONTENT_W - Inches(8.7),
        Inches(3.1),
        "/workflow results <id>\n"
        "/job <id>  # attempts\n"
        "/logs  # activity pane\n"
        "!./r4-local logs umrs",
        size=12,
        title=("logs", "ログ"),
    )
    rect(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.55),
        CONTENT_W,
        Inches(1.15),
        fill=TINT_AMBER,
        rounded=True,
    )
    text_box(
        slide,
        MARGIN + Inches(0.3),
        BODY_TOP + Inches(3.7),
        CONTENT_W - Inches(0.6),
        Inches(0.9),
        (
            "**All four pass 'r4 workflow lint'.** Lint checks the shape of the bundle, not whether it "
            "works — only a run (local or on UMRS) tells you that.",
            "**4 つとも 'r4 workflow lint' は通る。**lint(形式の検査)はバンドルの形を確かめるだけで、"
            "動くかどうかは"
            "実行(ローカルか UMRS)でしか分からない。",
        ),
        Style(15, INK),
    )
    notes(
        slide,
        (
            "20 min. Generate the bundles with docs/tutorial/workflows/make_fault_set.py before the "
            "session. Hand out one fault per pair; first pair to fix explains it. With the agent: "
            "workflow_results is read-only, so it runs without a prompt; the agent reads the step "
            "error, edits workflow.json, and asks before workflow_upload and workflow_run. Lint "
            "sees none of the four (entrypoint, keys and timeouts are only checked by a run; "
            "r4-contracts/src/r4_contracts/workflow_package.py:171-182 checks only that an "
            "entrypoint is present). f4 is the subtle one: an empty depends_on is valid and only "
            "means 'start at once'.",
            "20 分。事前に docs/tutorial/workflows/make_fault_set.py でバンドルを生成しておく。ペアごとに"
            " 1 つずつ配り、最初に直したペアが説明する。エージェントに任せる場合:workflow_results は"
            "読み取り専用なので確認なしで動き、エージェントはステップのエラーを読んで workflow.json を"
            "直し、workflow_upload と workflow_run の前に承認を求める。lint は 4 つとも見つけられない"
            "(entrypoint・キー・時間制限は実行して初めて分かる。"
            "r4-contracts/src/r4_contracts/workflow_package.py:171-182 は entrypoint が書かれているか"
            "だけを確かめる)。f4 は特に気づきにくい。空の depends_on は正しい書き方で、「すぐに開始"
            "する」という意味にすぎない。",
        ),
    )


def add(deck: Deck) -> None:
    part2_divider(deck)
    concepts_slide(deck)
    example_slide(deck)
    lab21_slide(deck)
    lab22_slide(deck)
    lab23_slide(deck)
    lab24_slide(deck)
    clinic_slide(deck)
