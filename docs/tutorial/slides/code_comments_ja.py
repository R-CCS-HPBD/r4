"""Japanese text for the ``# comments`` inside the deck's code panels.

Commands stay as they are; only the comments are translated. A comment
missing here stays English in the Japanese deck. Keys are byte-identical to
the comment text in slides_*.py (check_deck.py verifies both directions).
Code panels are not run through deck_kit's punctuation normaliser, so the
values carry full-width ``：（）`` themselves.
"""

COMMENTS = {
    # Opening (slide 7)
    "# pre-work — already done at home": "# 事前準備：自宅で実施済み",
    "# installs, pulls the model, registers": "# インストール・モデル取得・登録",
    "# today — the 5-minute check, in a terminal (repository folder)": (
        "# 当日：ターミナル（リポジトリのフォルダ）で 5 分チェック"
    ),
    "# gateway, endpoints, platform": "# ゲートウェイ・エンドポイント・プラットフォームの状態",
    "# the knowledge store answers": "# ナレッジストアが応答するか",
    "# then everything inside the console": "# あとはすべてコンソールの中で",
    "# pick: standalone": "# standalone を選ぶ",
    "# the local model": "# ローカルモデル",
    # Part 1 harness (loop pseudocode, slide 16)
    "# rules, past turns, your request": "# システムプロンプト・履歴・今回の依頼",
    "# one call = one round": "# 呼び出し 1 回 = 1 ラウンド",
    "# stop 3: a round limit": "# 終了 3:ラウンドの上限",
    "# stop 1: a plain answer": "# 終了 1:普通の回答",
    "# stop 2: the same failure again": "# 終了 2:同じ失敗の繰り返し",
    "# one last answer, without tools": "# ツールなしで最後の回答",
    "# the permission gate decides": "# 許可ゲートが判断する",
    "# the model reads it next round": "# 次のラウンドでモデルが読む",
    # Part 1 labs
    "# inside the console": "# コンソールの中で",
    "# pick the chat model": "# チャットモデルを選ぶ",
    "# show every tool call": "# ツール呼び出しをすべて表示",
    "# approve each tool call": "# ツール呼び出しを 1 つずつ承認",
    "# activity tray": "# アクティビティ・トレイ（処理の動きを表示）",
    "# shell; output reaches the agent": "# シェル。出力はエージェントにも渡る",
    "# send last 40 shell lines to agent": "# シェルの直近 40 行をエージェントに送る",
    "# mode: chat → plan → autopilot": "# モード切替：chat → plan → autopilot",
    "# web_fetch / web_search only": "# web_fetch・web_search だけが対象",
    "# in the console": "# コンソールで",
    "# re-send the last prompt": "# 直前の依頼を再送",
    "# tokens so far in this session": "# このセッションのトークン合計",
    "# BEFORE step B, in a fresh session": "# 手順 B の前に、新しいセッションで",
    "# ask the agent (it ingests a whole folder):": "# エージェントに頼む（フォルダごと取り込む）：",
    "# wait until the pack is listed": "# パックが一覧に出るまで待つ",
    "# then start r4 again": "# そのあと r4 を再起動",
    "# listed, off until you write “residual”": "# 一覧に出る。依頼に「残差」と書くまで無効",
    "# a shell pane inside the console, then:": "# コンソール内のシェルペインを開き、続けて：",
    "# back in chat: what the agent can call": "# チャットに戻り、呼べるツールを確認",
    "# one line each": "# 折り返して表示しているが、各コマンドは 1 行で入力",
    "# back to an earlier turn": "# 前のターンに戻る",
    "# transcript as markdown": "# 記録を Markdown で書き出す",
    "# then --model krso, and ask: “Explain residuals.csv”": (
        "# 次に --model krso で実行し、「residuals.csv を説明して」と頼む"
    ),
    # Part 2
    "# 1  a bucket (+ its -output twin) — no slash command yet": (
        "# 1  バケットを作成（<名前>-output も自動作成）。作成用のスラッシュコマンドはまだない"
    ),
    "# note the full name": "# 正式名を控える",
    "# 2  upload the bundle and run it": "# 2  バンドル（ワークフロー一式）を登録して実行",
    "# watch it (Alt+W: graph)": "# 進行を表示（Alt+W：グラフ表示）",
    "# attempts": "# 試行の履歴",
    "# activity pane": "# アクティビティペイン",
    "# steps.py": "# steps.py",
    "# both parents": "# 両方の親ステップの出力キー",
    "# keep only search_low in my-seismic-wf/workflow.json": (
        "# my-seismic-wf/workflow.json は search_low だけ残す"
    ),
    "# step_name is filled in by UMRS (search_low, …)": "# step_name は UMRS が入れる（search_low など）",
}
