"""
Japanese-language benchmark prompts.

Bilingual-parity gate (docs/development/i18n-roadmap.md): every deployed model
must hold quality in BOTH English and Japanese — a model that only scores on
English technical tasks must not ship as the recommendation default.

Each prompt carries `expected_ja` — quality markers the evaluator can check
in the generated output (keywords, pattern matches). The code is language-agnostic
where possible; linguistic surface is in JA because that is what we measure.
"""

JAPANESE_PROMPTS = [
    {
        "id": "ja_01_slurm_exp",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "Slurmスケジューラの backfill スケジューリングとは何かを、初心者の"
            "研究者にもわかるように、200文字以内の日本語で説明してください。"
        ),
        # Synonym-sets (any-hit counts): バックフィル / 先走 / 隙間 / 利用率
        # covers natural JA phrasing, not just literal loan-words
        "expected_ja": [
            ["backfill", "バックフィル", "先走", "隙間", "利用率"],
            ["スケジュール", "実行", "ジョブ"],
        ],
        "timeout_sec": 10,
    },
    {
        "id": "ja_02_translate_tech_en2ja",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "次の英文を自然で技術的に正確な日本語に翻訳してください。\n\n"
            '"Durable job/attempt separation ensures each job execution '
            "attempt has its own attempt-scoped outputs and a commit pointer "
            "marks which attempt's results are considered current.\"\n\n"
            "日本語で出力してください。英文術語 (job, attempt, commit pointer) "
            "はカタカナでの対応でも構いません。"
        ),
        "expected_ja": [
            ["ジョブ", "job", "仕事"],
            ["アテンプト", "attempt", "試行", "実行"],
            ["出力", "output", "結果"],
        ],
        "timeout_sec": 15,
    },
    {
        "id": "ja_03_translate_tech_ja2en",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "次の日本語を自然で技術的に正確な英語に翻訳してください。\n\n"
            "「HPCクラスタ上で推論ジョブを実行する際には、モデルウェイトを"
            "事前ステージングしておき、推論コンテナ内部でのインターネット"
            "ダウンロードは禁止する必要があります。」\n\n"
            "英語で出力してください。"
        ),
        "expected_ja": [
            ["HPC", "cluster", "computing"],
            ["pre-staged", "prestage", "staged", "pre-download"],
            ["inference", "job"],
            ["download", "internet"],
        ],
        "timeout_sec": 15,
    },
    {
        "id": "ja_04_hpc_term_explain_en",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "次の日本語について、英語のみでその意味を200文字以内で説明してください。\n\n"
            "「データソブリン（data sovereignty）」―― 国内研究データが国際的な"
            "商用サービスに流出しないように、すべての推論・ナレッジ処理を国内"
            "HPC基盤上で完結させる構造。"
        ),
        "expected_ja": [
            ["sovereignty", "sovereign", "domestic"],
            ["egress", "external", "data flow", "leak"],
            ["offline", "on-premise", "on-prem", "private"],
        ],
        "timeout_sec": 10,
    },
    {
        "id": "ja_05_formalize_email",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "次のカジュアルな文を、大学共同研究者に送る丁寧な日本語のメール"
            "として書き直してください。件名と冒頭の挨拶だけでよい。\n\n"
            "「この間の実験の結果が出たから、見てもらいたいんだ。今週"
            "どこかで打ち合わせできる？」"
        ),
        "expected_ja": [
            ["お世話になっており", "お疲れさま", "ご無沙汰"],
            ["相談", "依頼", "願い", "確認", "打ち合わせ"],
            ["結果", "実験", "データ"],
        ],
        "timeout_sec": 10,
    },
    {
        "id": "ja_06_error_code_hint",
        "category": "japanese",
        "language": "plain",
        "prompt": (
            "次のエラーメッセージの原因を日本語で1文ずつ説明し、"
            "対処法も添えてください。出力はJSONです。\n\n"
            "  Error: loop ID required\n"
            "  hint: e.g. r4 project loop status <loop_id>"
        ),
        "expected_ja": [
            ["ループ", "loop", "Loop"],
            ["指定", "必要", "不足", "required"],
            ["status", "ステータス", "確認", "対処"],
        ],
        "timeout_sec": 10,
    },
]
