# 第 2 部の演習 — 小さなワークフロー（約 3 時間）

2026-10-29 · R4 + UMRS チュートリアル · 日本語 ([English](../en/02-part2-labs.md)) ·
`slides/tutorial-r4-umrs.ja.pptx` の第 2 部（スライド 44〜51）

第 1 部では、速くしたソルバで 1 回の計算が 1 秒もかかりませんでした。
**モデル探索**では、候補のモデル 1 つごとに 1 回の計算が必要です。ここでは
163 回（候補 81 個 × 2 と、比較用の ak135）です。この探索を UMRS の
ワークフロー（複数のステップを依存関係どおりに実行する処理の定義）として
まとめます。ワークフローは 2 つの探索ステップに**分岐（ファンアウト）**し
（それぞれが候補モデルの半分を採点。採点とは、観測との残差を計算してモデルの
合い具合を評価することです）、**rank** ステップが両方の結果を**集約**して残差レポートを
書きます。実行するのは自分の手元の UMRS です。完成版のバンドル（ワークフロー
一式）は `docs/tutorial/workflows/seismic-wf/` にあります。皆さんは自分用の
コピー（リポジトリのフォルダの `my-seismic-wf/`）を一歩ずつ組み立てます。

第 1 部と同じく、**作業はすべてコンソールの中で行います**（リポジトリのフォルダで
起動した `r4`。早見表は [01-part1-labs.md](01-part1-labs.md) の冒頭）。`!` で
始まるコマンドは、コンソールからワークスペースで実行されます。長い出力には
`/term` を使います。

入力データ：第 1 部のフォルダ
`docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/`（ISC の読み取り値。
利用するときは International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830 を出典として明記してください）、または**合成**データ
`docs/tutorial/tasks/eikonal/data/synthetic-kii/`。どちらにも `event.json` と
`p_picks.csv` があります。

## 用語

- **ステップ**：Python のクラスです。`setup(config)`、`run(ctx) -> StepResult`、
  `teardown()` を持ち、`from umrs.pipeline_sdk import Step, StepContext, StepResult`
  でインポートします。
- **`workflow.json`**：バンドルの定義ファイル（マニフェスト）です。`name`、
  `source`、`steps` を持ちます。各ステップは `name`、`description`、
  `entrypoint`（`"module:Class"`）、`config`、`depends_on` を持ちます
  （アップロード時の決まりで、すべてのステップに `description` と `depends_on`
  が必須です）。
- **バケット**：ディスク上のオブジェクトストレージ（ファイルを名前で出し入れする
  保存場所）です。実行（run）は、`bucket` パラメータで指定したバケットから入力を
  読み、`<bucket>-output` に出力を書きます。
- **キーは下流へ流れる**：キーとは、バケットの中のファイル（オブジェクト）の
  名前です（例：`search_low.json`）。ステップは書き出したファイルのキーを
  `StepResult(output_keys=[…])` で返します。
  次のステップ（`depends_on` で指定したもの）は、それを `ctx.input_keys` として
  受け取り、`ctx.download_parent_bytes(key)` で読みます。`workflow.json` に
  `inputs` / `outputs` の欄はありません。
- **分岐（ファンアウト）と集約**：互いに依存しないステップは同時に実行できます。
  複数の親を `depends_on` に書いたステップは、親**すべて**の出力キーを
  `ctx.input_keys` で受け取ります。
- **routing_hints**（`gpu`、`image`、`prefer` など）は、クラスタ上でステップを
  どこで実行するかを指定するものです（第 3 部で扱います）。ノート PC の dev
  プロファイル（開発用の設定）では、すべてのステップが umrs コンテナの中で、
  UMRS サーバと同じプロセスの中で（in-process）実行されます。そのため、ステップの
  print などの出力は UMRS のログに出ます。

**この演習での制約：** 単体版の dev プロファイルでは、ステップは umrs コンテナの
中で実行され、`requirements` に書いたものは何もインストールされません。
ステップがインポートできるのは、標準ライブラリ、NumPy（コンテナに入っている）、
umrs の SDK だけです。Numba と matplotlib は**ありません**。バンドルが自前の
NumPy のソルバ（`eikonal.py`、対角の平面ごとに更新する版）を持ち、
`seismic_core.py` が `csv`、`json`、`math`、`datetime` と NumPy だけを使って
いるのはこのためです。

## Lab 2.1 — 1 ステップのワークフロー（30 分、スライド 47）

完成版のバンドルをコピーし、ステップを 1 つに減らします：

```text
!cp -r docs/tutorial/workflows/seismic-wf my-seismic-wf
```

> my-seismic-wf/workflow.json で search_low のステップだけを残し、
> ワークフローの名前を my-seismic に変えて。steps.py は変更しないこと。

結果は次のようになるはずです：

```json
{
  "name": "my-seismic",
  "version": "1.0",
  "description": "Tutorial Part 2: fit a 1-D P-velocity model to first-P picks with 3-D eikonal solves (fan-out search, gather and rank).",
  "source": {"type": "local_path", "path": "."},
  "execution_mode": "in_process",
  "requirements": [],
  "resources": {"gpu_count": 0, "cpu_limit": "2", "memory_limit": "2Gi", "timeout_seconds": 1800},
  "steps": [
    {
      "name": "search_low",
      "description": "Score candidate models with a shallow Moho (24-28 km), plus the ak135 baseline.",
      "entrypoint": "steps:ModelSearchStep",
      "config": {
        "event_key": "event.json",
        "picks_key": "p_picks.csv",
        "grid_km": 4.0,
        "upper": [5.7, 5.9, 6.1],
        "lower": [6.5, 6.7, 6.9],
        "moho_km": [24.0, 26.0, 28.0],
        "mantle": [7.7, 7.9, 8.1],
        "include_ak135": true
      },
      "depends_on": [],
      "timeout_seconds": 900
    }
  ]
}
```

4 つの値のリストから、3 × 3 × 3 × 3 = 81 個の 3 層モデル（上部地殻、深さ 18 km
からの下部地殻、モホ面の深さ、マントル）ができ、これに比較用の ak135 を加えます。
`entrypoint` が指しているステップのクラス（`my-seismic-wf/steps.py` 内）：

```python
class ModelSearchStep(Step):
    """Fan-out: score one block of candidate models."""

    name = "search"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        time.sleep(float(self.config.get("debug_delay_seconds", 0)))
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        scores = [core.score(event, picks, m, h) for m in core.candidates(self.config)]
        if self.config.get("include_ak135"):
            scores.append({**core.score(event, picks, core.AK135_CRUST, h), "baseline": True})
        key = f"{self.config['step_name']}.json"
        ctx.upload_bytes(key, json.dumps(scores).encode(), content_type="application/json")
        return StepResult(output_keys=[key], extra={"models": len(scores)})
```

`_inputs()` は、入力バケットから `event.json` と `p_picks.csv` を読みます。
`config["step_name"]` にはランナー（ステップを実行する側。UMRS と
`run_local.py` のどちらも）がそのステップの名前を自動で入れるので、同じクラスを
複数のステップで使えます。`debug_delay_seconds` は、Lab 2.5 の失敗クリニック
（わざと壊したバンドルを直す演習）のためだけにあります。

アップロードする前に、手元で確認します（サーバ側の試し実行（ドライラン）はありません）：

```text
/code run my-seismic-wf/run_local.py --timeout 300 -- --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --workflow my-seismic-wf/workflow.json
!r4 workflow lint my-seismic-wf
```

（合成データなら `--data docs/tutorial/tasks/eikonal/data/synthetic-kii`。）
`run_local.py` はノート PC の上でランナーの代わりを務め（フォルダをバケットとして
扱う）、**リポジトリのフォルダに** `local-run/local-output/search_low.json` を
書き出します。82 個の採点結果（モデルごとの残差の評価）で、それぞれにモデルの
各層の値、`rms_s`（残差の RMS＝二乗平均平方根、秒）、`mean_s`（平均残差、秒）が
入っています。ワークステーションでは約 30 秒かかりました。`lint`（形式の検査）が
確認するのはバンドルの形式だけです。それでは足りない理由は Lab 2.5 で分かります。
（`my-seismic-wf/` と `local-run/` は自分用のもので、git の管理対象外です。）

## Lab 2.2 — データをアップロードし、実行し、結果を読む（20 分、スライド 48）

コンソールにはバケットのコマンドがなく（`/files upload` にもバケットの指定が
ありません）、バケットの操作は `!` で行います：

```text
!r4 bucket create default seismic
!r4 bucket list
```

`bucket create` は `<you>.default.seismic`（`<you>` は自分のユーザ名）と、
対になる `<you>.default.seismic-output` を作ります。
表示された正式な名前を控え、以下の `<you>.default.seismic` をその名前に置き
換えてください。

```text
!r4 files upload docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/event.json --bucket <you>.default.seismic
!r4 files upload docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/p_picks.csv --bucket <you>.default.seismic
```

次に、**完成版**の 3 ステップのバンドルをアップロードして実行します。残りを
自分で組み立てる前に、実行全体とそのレポートを一度見ておくためです：

```text
/workflow upload docs/tutorial/workflows/seismic-wf
/workflow run seismic-model-search --param bucket=<you>.default.seismic
```

Work ペイン（`/work`、または引数なしの `/workflow`）で進み具合を見ます。
**Alt+W** で実行中のワークフローのグラフ（ステップのつながりの図）が開きます。
Work ペインでは、ワークフローの行で `t` を押すとそのワークフローを実行し、
ジョブの行で `t`（実行の行では Shift+T）を押すとトレース（実行経過の記録）を
表示します。実行には 1 分以上かかります（`--wait` は 20 秒が上限なので、
頼らないでください）。そのあと：

```text
/workflow status <execution-id>
/workflow results <execution-id>
!r4 files download report.md --bucket <you>.default.seismic-output -o report.md
```

`report.md` をエージェントと一緒に読みましょう。`/workflow list` で自分の
実行の一覧が、`/workflow show seismic-model-search` でアップロードしたバンドルが
表示されます。自分の 1 ステップのコピーも同じように実行でき
（`/workflow upload my-seismic-wf`、`/workflow run my-seismic --param bucket=…`）、
出力バケットに `search_low.json` が残ります。

*コンソールの外で*（代わりの方法）：`r4 workflow run docs/tutorial/workflows/seismic-wf -p bucket=<b> --wait`、
`r4 workflow results <id>`、`r4 files download …`。

続いて 10 分休憩です（第 2 部の開始から 70〜80 分の時間帯）。実行がまだ失敗（赤）のままなら、画面を
そのままにしておいてください。休憩後にスタッフが直します。

## Lab 2.3 — 分岐（ファンアウト）と集約（40 分、スライド 49）

自分のコピーに分岐を戻します。エージェントに依頼します：

> my-seismic-wf/workflow.json に、search_high と rank のステップを
> docs/tutorial/workflows/seismic-wf/workflow.json とまったく同じ形で戻して。
> そのうえで、RankStep の ctx.input_keys に何が入るかを説明して。

戻す 2 つの項目：

```json
{
  "name": "search_high",
  "description": "Score candidate models with a deeper Moho (30-35 km).",
  "entrypoint": "steps:ModelSearchStep",
  "config": {
    "event_key": "event.json",
    "picks_key": "p_picks.csv",
    "grid_km": 4.0,
    "upper": [5.7, 5.9, 6.1],
    "lower": [6.5, 6.7, 6.9],
    "moho_km": [30.0, 32.0, 35.0],
    "mantle": [7.7, 7.9, 8.1]
  },
  "depends_on": [],
  "timeout_seconds": 900
},
{
  "name": "rank",
  "description": "Gather both searches, rank all models, write the residual report.",
  "entrypoint": "steps:RankStep",
  "config": {
    "event_key": "event.json",
    "picks_key": "p_picks.csv",
    "grid_km": 4.0
  },
  "depends_on": ["search_low", "search_high"],
  "timeout_seconds": 300
}
```

`search_high` は値を変えて**同じクラス**を使います。集約のステップは、
`steps.py` にすでにあります：

```python
class RankStep(Step):
    """Gather: rank every scored model and write the report."""

    name = "rank"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        scores = []
        for key in ctx.input_keys:
            scores.extend(json.loads(ctx.download_parent_bytes(key)))
        baseline = next(s for s in scores if s.get("baseline"))
        ranked = sorted((s for s in scores if not s.get("baseline")), key=lambda s: s["rms_s"])
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        report = core.report_markdown(event, ranked, baseline, len(picks), h)
        ctx.upload_bytes("report.md", report.encode(), content_type="text/markdown")
        ctx.upload_bytes(
            "best_model.json",
            json.dumps(ranked[0], indent=2).encode(),
            content_type="application/json",
        )
        return StepResult(
            output_keys=["report.md", "best_model.json"],
            extra={"best_rms_s": ranked[0]["rms_s"], "ak135_rms_s": baseline["rms_s"]},
        )
```

`search_low` と `search_high` は互いに依存しないので、同時に実行できます。
`rank` は両方の終了を待ち、その `ctx.input_keys` には**両方**の親のキー
`["search_low.json", "search_high.json"]` が入ります。

手元での確認、lint、アップロード、実行、レポートの取得：

```text
/code run my-seismic-wf/run_local.py --timeout 300 -- --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --workflow my-seismic-wf/workflow.json
!r4 workflow lint my-seismic-wf
/workflow upload my-seismic-wf
/workflow run my-seismic --param bucket=<you>.default.seismic
/workflow results <execution-id>
!r4 files download report.md --bucket <you>.default.seismic-output -o report.md
```

合成データでの期待値（4 km 格子、計算 163 回、手元の PC で NumPy を使って 54 秒）：
ak135 の RMS は **1.48 秒**（平均残差 −1.30 秒）、最良のモデルの RMS は
**0.21 秒**（平均残差 −0.01 秒）。実際の読み取り値の場合は、レポートを 2 km 格子の
解答例（`docs/tutorial/data/README.md`。ak135 1.32 秒、162 モデル中の最良 0.44 秒、KRSO-1D v2 0.46 秒）と比べて
ください。4 km 格子のほうが粗いので、数値はいくらか違います。

ランキングの上位を読みましょう。モホ面 **26 km と 28 km のモデルが同点**です。
4 km 格子では、2 km しか違わないモホ面の深さは区別できず、レポートにもそう
書かれています。いくつものモデルがほぼ同じくらいよく合うのは、モホ面の深さと
速度がトレードオフ（一方を変えても他方で補える関係）になるからです。これは
バグではなく、科学的な結果です。

## Lab 2.4 — 共著者としてのエージェント（30 分、スライド 50）

1. **既存のコードを再利用する。** 次のように依頼します：
   *「docs/tutorial/workflows/legacy/plot_travel_times.py を、my-seismic-wf の
   'rank' の後に動く 4 つ目のステップ 'plot' にして。ステップでは matplotlib は
   使えないので、標準ライブラリだけで（NumPy は可）travel_times.svg を書き出す
   こと。終える前に run_local.py で試しに実行し、r4 workflow lint も実行して。」*
   自分のスクリプトを持ち込んでもかまいません。
2. **外部のスキルを取り込む。** スキルは GitHub 上のパスを指定してインストール
   します：`!r4 skill github install <owner>/<repo>/skills/<name>`（完全な URL
   でのインストールにはまだ対応していません）。インストールすると `/skills` に
   表示されます。共有のスキル集（GitHub のリポジトリ）は当日、講師が案内します。
3. **エラーから直す。** *「実行が失敗した。`/workflow results <id>` を
   読んで原因を見つけ、修正し、アップロードして再実行して。」*

自分で作ったステップと比べてみましょう。エージェントは標準ライブラリだけを
使ったか、読み取り値を入力バケットから読んでいるか（元のスクリプトの `DATA`
のような固定のパスではなく）、`depends_on: ["rank"]` を加えたか、先に手元での
確認と `lint` を通したか。

## Lab 2.5 — 失敗クリニック（20 分、スライド 51）

講師が各ペアに壊れたバンドルを 1 つ渡します（`docs/tutorial/workflows/make_fault_set.py`
で `docs/tutorial/workflows/faults/` に生成したもの。どれも `seismic-wf` の
コピーで、`workflow.json` に誤りが 1 つだけあり、名前は `seismic-<fault>` です）。
アップロードして実行し、エラーを読み、`workflow.json` を（自分で、または
エージェントと）直して、もう一度アップロードして実行してください：

```text
/workflow upload docs/tutorial/workflows/faults/f1-bad-entrypoint
/workflow run seismic-f1-bad-entrypoint --param bucket=<you>.default.seismic
```

確認する場所：

| 見たいもの | コンソールでの操作 |
|---|---|
| ステップごとの状態とエラー | `/workflow results <id>`、`/workflow status <id>` |
| ジョブの試行（再試行を含む各回の実行）の履歴 | `/job <id>` |
| アクティビティ（操作とイベントの記録） | `/logs`（アクティビティペイン）、**F9** |
| UMRS のプロセス内で動いたステップの出力（print など） | `/term` を開いて `./r4-local logs umrs` |

注意：**壊れた 4 つのバンドルは、どれも `r4 workflow lint` を通過します。**

## 宿題（スライド 53）

ISC Bulletin から別の地震を選び（エージェントにダウンロードを頼むか、
`!cd docs/tutorial/tasks/eikonal && python fetch_isc_picks.py --event <id> --out data/<name>`。
`<id>` は ISC のイベント ID、`<name>` は保存するフォルダ名）、自分のワークフローで
実行してみてください。データの出典（ISC Bulletin の DOI）も一緒に記録して
おきましょう。さらに進むなら、
速度モデルを固定して地震の**震源を決め直し**ます（リロケーション）。速度モデルの
代わりに、震源と発震時刻を探索します（解答例ではどちらも ISC の解に固定して
いますが、実際の研究ではそうしません）。`plot` ステップが終わっていなければ、
仕上げてください。
