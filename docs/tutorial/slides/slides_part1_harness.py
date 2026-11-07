"""Part 1 background, second half: agent, harness and knowledge, as R4 implements them.

Facts come from the code at d1569ef38 (checked 2026-10-29; file:line in the
speaker notes) and from the measured probes in docs/tutorial/background/.
Every mechanism carries a scope pill (``deck_kit.scope_tag``): a general
agent-harness pattern, a general pattern with R4's own settings, or
R4-specific. The notes name comparable harnesses for the general ones.
"""

from __future__ import annotations

import math

import context_bar
import harness_map
import slides_part1_overview
from deck_kit import (
    ALERT,
    AMBER,
    BODY_TOP,
    CONTENT_W,
    INK,
    MARGIN,
    MUTED,
    NAVY,
    OCEAN,
    SCOPE_H,
    SCOPE_KINDS,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    badge,
    bar_chart,
    bullets,
    card,
    chip,
    code_panel,
    content,
    down_arrow,
    flow,
    lang,
    notes,
    rect,
    scope_tag,
    table,
    text_box,
    text_width,
    tr,
)
from lxml import etree
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

KICK_A = (
    "Part 1 · Under the hood — agent and harness",
    "第 1 部 · 中身を見る — エージェントとハーネス",
)
KICK_K = ("Part 1 · Under the hood — knowledge", "第 1 部 · 中身を見る — ナレッジ")

REQUEST = """POST /v1/chat/completions
{"model": "qwen2.5:7b", "temperature": 0.4,
 "messages": [{"role": "system", ...},
   {"role": "user",
    "content": "Which station is farthest from the epicentre?"}],
 "tools": [{"type": "function", "function": {
   "name": "python_exec",
   "parameters": {"properties": {"code": {"type": "string"},
     "timeout": {"type": "integer", "default": 180}},
     "required": ["code"]}}}, ...]}"""

REPLY = """{"role": "assistant", "tool_calls": [{"id": "call_1",
  "function": {"name": "python_exec",
   "arguments": "{\\"code\\": \\"df = pd.read_csv(...); ...\\"}"}}]}"""

RESULT = """{"role": "tool", "tool_call_id": "call_1",
 "content": "25 stations; farthest JHT 267.6 km"}"""

# Keep every '# comment' byte-identical: code_comments_ja.py keys on them.
LOOP = """messages = [system, *history, user]  # rules, past turns, your request
for round in range(MAX_ROUNDS):      # every harness sets a limit
    reply = model(messages, tools)   # one call = one round
    if not reply.tool_calls:
        return reply.text            # plain answer: done
    for call in reply.tool_calls:
        result = gate_then_run(call) # the permission gate decides
        messages += [call, result]   # the model reads it next round"""


# ── Layout helpers ───────────────────────────────────────────────────────────


def _pill_w(kind: str) -> int:
    """Width of the scope pill ``kind`` in the current language (as deck_kit draws it)."""
    return int(text_width(tr(SCOPE_KINDS[kind][0]), 9) + Inches(0.24))


def _tab(slide, x, y, w, kind: str) -> None:
    """Scope pill straddling the top edge of the box at (x, y, w), right-aligned."""
    scope_tag(slide, int(x + w - Inches(0.2) - _pill_w(kind)), int(y - SCOPE_H // 2), kind)


def _text_h(text, size: float, width) -> int:
    """Rough height of ``text`` (an (en, ja) pair) wrapped into ``width`` at ``size`` pt."""
    lines = 0
    for para in tr(text).replace("**", "").split("\n"):
        lines += max(1, math.ceil(text_width(para, size, bold=False) * 1.05 / width))
    return int(lines * Pt(size) * 1.22)


def _box(slide, x, y, w, h, title, body, kind=None, fill=TINT, size=12) -> None:
    """Compact card: 15 pt title, then a paragraph (pair) or bullets (list)."""
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    pad = Inches(0.2)
    text_box(
        slide, x + pad, y + Inches(0.14), w - 2 * pad, Inches(0.34), title, Style(15, INK, True)
    )
    top, inner = y + Inches(0.52), h - Inches(0.6)
    if isinstance(body, list):
        bullets(slide, x + pad, top, w - 2 * pad, inner, body, Style(size, INK), gap=4)
    else:
        text_box(slide, x + pad, top, w - 2 * pad, inner, body, Style(size, INK))
    if kind:
        _tab(slide, x, y, w, kind)


def _scope_group(slide, x, y, w, kind: str, items, size=12) -> int:
    """A scope pill, then the bullets it covers; returns the y below the group."""
    scope_tag(slide, x, y, kind)
    top = y + SCOPE_H + Inches(0.06)
    width = w - Inches(0.28)
    h = sum(_text_h(item, size, width) + Pt(4) for item in items)
    bullets(slide, x, top, w, h, items, Style(size, INK), gap=4)
    return int(top + h + Inches(0.12))


def _steps(slide, x, y, w, steps, h=Inches(0.62), gap=Inches(0.1), size=12) -> int:
    """Numbered boxes, one per (en, ja) step; returns the y below the last."""
    for i, step in enumerate(steps):
        rect(slide, x, y, w, h, fill=TINT, rounded=True)
        badge(
            slide,
            x + Inches(0.12),
            y + (h - Inches(0.34)) // 2,
            str(i + 1),
            size=Inches(0.34),
            font=12,
        )
        text_box(
            slide,
            x + Inches(0.58),
            y,
            w - Inches(0.7),
            h,
            step,
            Style(size, INK),
            anchor=MSO_ANCHOR.MIDDLE,
        )
        y += h + gap
    return int(y)


# ── 15 · Tool calling ────────────────────────────────────────────────────────

_PANEL_W = Inches(6.7)
_PANELS = (
    (
        REQUEST,
        Inches(2.25),
        (
            "①  harness → model: TEXT — one JSON with messages + tool schemas",
            "①  ハーネス → モデル:テキスト(メッセージ+ツール定義の JSON 1 通)",
        ),
    ),
    (
        REPLY,
        Inches(1.05),
        (
            "②  model → harness: still TEXT — a request to use a tool, nothing run yet",
            "②  モデル → ハーネス:これもテキスト。ツール使用の要求で、まだ何も実行されない",
        ),
    ),
    (
        RESULT,
        Inches(0.92),
        (
            "③  harness EXECUTES it (the only action), turns the result back into text",
            "③  ハーネスが実行する(現実に何かが起きるのはここだけ)。結果はテキストで返す",
        ),
    ),
)

_KEY_IDEA = [
    (
        "The model **never runs anything**: it only writes JSON naming a tool "
        "(a tool call, also “function calling”).",
        "モデルは**何も実行しない**。ツール名を書いた JSON(ツール呼び出し、英語では "
        "function calling)を出すだけ。",
    ),
    (
        "The harness decides whether to run it, runs it, and shows the model the result.",
        "実行するかを決め、実行し、結果をモデルに見せるのはハーネス。",
    ),
    (
        "Tool “definitions” are just more text in the request.",
        "ツールの「定義」もリクエスト内のテキストにすぎない。",
    ),
]

_TEXT_FALLBACK = (
    "The harness reads tool-call tags out of the reply text (<tool_call>{json}</tool_call>, "
    "<invoke name=…>, Kimi's dialects) and turns them into the same JSON. The idea goes back "
    "to ReAct (prompting a model to write thought and action as text): common across agent "
    "implementations, not an R4 invention.",
    "ハーネスが返答本文からツール呼び出しのタグ(<tool_call>{json}</tool_call>、"
    "<invoke name=…> など)を読み取り、同じ JSON に変換する。考え方は ReAct(思考と行動を"
    "テキストで交互に書かせる手法)にさかのぼる一般的な方式で、R4 独自ではない。",
)

_SMALL_MODEL = (
    "Repairing broken JSON before rejecting it is a known pattern (OpenHands, opencode). "
    "R4's own addition: it drops the nulls small models put in optional arguments, so the "
    "tool's default applies. Still unreadable, or an unknown tool → an error to the model "
    "before any approval.",
    "壊れた JSON を拒否の前に直すのは既にある方式(OpenHands、opencode)。R4 独自なのは、"
    "小さなモデルが省略してよい引数に入れる null(値なし)を取り除き、ツールの既定値を"
    "使うこと。読めない呼び出しや存在しないツールは、実行の許可を確かめる前にエラーとして"
    "モデルに返す。",
)


def _tool_call_panels(slide) -> None:
    y = BODY_TOP
    for code, h, title in _PANELS:
        code_panel(slide, MARGIN, y, _PANEL_W, h, code, size=10, title=title)
        y += h + Inches(0.12)
    _tab(slide, MARGIN, BODY_TOP, _PANEL_W, "general")


_TOOL_CARDS = (
    (("The key idea", "要点"), _KEY_IDEA, "general", Inches(1.7)),
    (
        ("Models without native tool calling", "専用のツール呼び出し形式を持たないモデル"),
        _TEXT_FALLBACK,
        "r4tuned",
        Inches(1.6),
    ),
    (
        ("Safety nets for small models", "小さなモデルへの備え"),
        _SMALL_MODEL,
        "r4tuned",
        Inches(1.6),
    ),
)


def _tool_call_cards(slide) -> None:
    x = MARGIN + _PANEL_W + Inches(0.25)
    w = CONTENT_W - _PANEL_W - Inches(0.25)
    y = BODY_TOP
    for title, body, kind, h in _TOOL_CARDS:
        _box(slide, x, y, w, h, title, body, kind)
        y += h + Inches(0.15)


def tool_call_slide(deck: Deck) -> None:
    slide = content(
        deck, ("From text to action: tool calling", "文章から操作へ:ツール呼び出し"), KICK_A
    )
    harness_map.mini(slide, "exchange")
    _tool_call_panels(slide)
    _tool_call_cards(slide)
    notes(
        slide,
        (
            "GENERAL PATTERN: tool calling works the same everywhere — Claude's "
            "tool_use / tool_result blocks, OpenAI function calling (Agents SDK "
            "@function_tool), opencode tools, LangChain bind_tools; in each the harness alone "
            "executes. R4's request: OpenAI-compatible /v1/chat/completions; agent rounds go "
            "upstream as a stream and are assembled (r4/src/r4/client.py:1428-1450). "
            "python_exec schema: companion/tools.py:1731-1746. One assistant message with "
            "tool_calls, then one role:tool message per call: agent.py:3075 "
            "(_record_tool_round). Default temperature 0.4: agent.py:248. "
            "TEXT FALLBACK (general idea, R4 settings): parse order native tool_calls → "
            "<tool_calls_section_begin> sections → <|open|> (Kimi) → <tool_call>/<invoke> "
            "XML, agent.py:4991-5013. vLLM and Ollama usually do this server-side with their "
            "tool parsers; R4 does it client-side so any served model works. "
            "GENERAL · R4 SETTINGS: repair-before-reject, the OpenHands pattern that "
            "_repair_tool_arguments cites (agent.py:1721-1751: control characters, GLM "
            "stringified containers, trailing text, balanced truncation); opencode repairs "
            "tool calls through the AI SDK's repair hook. R4-SPECIFIC: null optionals "
            "dropped (agent.py:980-990; llama3.2:3b broke file_read that way); "
            "unrepairable JSON or an unknown tool → error before the gate "
            "(agent.py:5206-5222). Claude Code and the OpenAI Agents SDK validate against "
            "the schema and return the error instead. The example result is the real event "
            "of Lab 1.1 (25 stations, farthest JHT 267.6 km).",
            "一般的な仕組み:ツール呼び出しはどの実装でも同じ形。Claude の tool_use / "
            "tool_result、OpenAI の function calling(Agents SDK の @function_tool)、opencode "
            "のツール、LangChain の bind_tools。どれも実行するのはハーネスだけ。R4 のリクエスト"
            "は OpenAI 互換の /v1/chat/completions。エージェントのラウンドはストリームで送り、"
            "受け取りながら組み立てる(r4/src/r4/client.py:1428-1450)。python_exec の定義は "
            "companion/tools.py:1731-1746。tool_calls を持つ assistant メッセージの後に、呼び"
            "出しごとに role:tool メッセージ 1 件(agent.py:3075、_record_tool_round)。既定"
            "温度 0.4(agent.py:248)。本文からの読み取り(一般的な仕組み・R4 の設定):"
            "ネイティブの tool_calls → <tool_calls_section_begin> → <|open|>(Kimi)→ "
            "<tool_call>/<invoke> の XML の順(agent.py:4991-5013)。vLLM や Ollama は通常"
            "サーバ側のパーサで同じことをするが、R4 はクライアント側で行うので、どのモデル"
            "サーバでも動く。一般的な仕組み・R4 の設定:拒否の前に直す(OpenHands の方式で、"
            "_repair_tool_arguments にもそう書かれている。agent.py:1721-1751:制御文字、GLM の"
            "文字列化された配列、末尾の余計な文、括弧の対応での切り詰め)。opencode は AI SDK "
            "の修復機能で直す。R4 独自:省略してよい引数の null を取り除く(agent.py:980-990。"
            "llama3.2:3b で file_read が壊れた)。直せない JSON や存在しないツールは許可ゲートの前にエラー(agent.py:5206-5222)。"
            "Claude Code や OpenAI Agents SDK はスキーマで検証してエラーを返す方式。結果の例は "
            "Lab 1.1 の実際の地震(25 観測点、最遠は JHT の 267.6 km)。",
        ),
    )


# ── 16 · The agent loop ──────────────────────────────────────────────────────

_LOOP_GROUPS = (
    (
        "general",
        [
            (
                "Call the model → run the tools it asks for → add the results → call again.",
                "モデルを呼ぶ → 要求されたツールを実行 → 結果を追加 → もう一度呼ぶ、の繰り返し。",
            ),
            (
                "Done when a reply asks for no tool: that text is the answer.",
                "ツールを求めない返答が来たら終わり。その文章が回答。",
            ),
            (
                "Every harness also stops runaway loops: a maximum number of rounds, and "
                "checks for the same failing call repeated.",
                "どのハーネスも暴走を止める仕組みを持つ:ラウンド数の上限と、同じ失敗の繰り返しの"
                "検出。",
            ),
            (
                "The model never sees this loop; it only sees the growing list of messages.",
                "モデルはこのループを知らない。見えるのは、増えていくメッセージの一覧だけ。",
            ),
        ],
    ),
)

_LOOP_NOTES = (
    "Review 2026-11-05: the slide shows only the loop every harness shares; R4's own "
    "numbers and extras are here. "
    "GENERAL PATTERN: the same core loop runs in Claude Code / the Claude Agent SDK (query "
    "loop), the OpenAI Agents SDK (Runner.run), opencode (session loop) and LangGraph "
    "(create_react_agent). R4: _run_agentic_turn, r4/src/r4/companion/agent.py:3131-3240; "
    "finished background agents' results join the messages at each round start (3162, "
    "3242-3253; G9, ≤ 4 "
    "running, async_agents.py:24). GENERAL · R4 SETTINGS: every harness bounds the loop "
    "(OpenAI Agents SDK max_turns 10, LangGraph recursion_limit 25, both hard stops); R4: "
    "DEFAULT_TOTAL_TOOL_ROUNDS 1000 and DEFAULT_SOFT_BLOCK_ROUNDS 25 (agent.py:79-80), then "
    "one no-tools wrap-up round, opencode's MAX_STEPS pattern (_wrap_up_summary, "
    "3699-3722). Output limit (G19a): one retry with max(2×, +8,192) up to 131,072 "
    "(3343-3372; Claude Code escalates the same way), then ≤ 4 continuation nudges carrying "
    "the last 12,000 chars of the cut reasoning (118-128, 196-205; 12 when unattended). "
    "Guards: TOOL_FAILURE_STREAK_TRIP 4 (agent.py:86), exact replay or failure drift 3× "
    "(1281-1340); nudge then answer mode (3606-3646). opencode's 'doom loop' check is the "
    "nearest relative. Unattended runs (R4_AUTONOMOUS=1): a trip becomes a bounded nudge, "
    "and a same-call guard is on (same name + arguments 6× or an A/B pair; 868-935, "
    "3649-3664). Stalled-reply nudges (GENERAL · R4 SETTINGS): OpenHands' headless runs "
    "auto-reply 'please continue' when the agent answers without a tool call; R4's own "
    "parts are the limits (empty ≤ 2, ≤ 4 unattended; announced-but-no-call ≤ 3) and the "
    "'Let me …' detector (agent.py:136-160, 3513-3545). R4-SPECIFIC: the block checkpoint "
    "is injected only without new evidence (_block_boundary_control 3666-3697, message "
    "1046-1062); none of the harnesses named first has one. Nearest relative: VS Code "
    "Copilot agent mode pauses after chat.agent.maxRequests and asks the USER 'Continue to "
    "iterate?'; R4's code calls its checkpoint 'copilot-style' (agent.py:1047). The "
    "difference: R4 asks the model for a status and keeps going, and only when the block "
    "found nothing new. "
    "Thinking budget 16,000 tokens or 600 s, closed client-side (237, 277). Opt-in "
    "R4_COMPANION_FALLBACK_MODEL flips the model once per turn (G19b, 4904-4918; cf. "
    "Claude Code --fallback-model). Read-only / concurrency-safe calls run in parallel, "
    "8 workers (5097-5117, R4_COMPANION_TOOL_CONCURRENCY), as Claude Code does with "
    "isConcurrencySafe.",
    "2026-11-05 のレビュー:スライドにはどのハーネスにも共通のループだけを載せ、R4 独自の数値や"
    "仕組みはここに書く。"
    "一般的な仕組み:同じ中核ループは Claude Code / Claude Agent SDK(query ループ)、"
    "OpenAI Agents SDK(Runner.run)、opencode(セッションループ)、LangGraph"
    "(create_react_agent)にもある。R4 では _run_agentic_turn(r4/src/r4/companion/"
    "agent.py:3131-3240)。終わったバックグラウンドのエージェントの結果は、各ラウンドの最初に"
    "メッセージへ追加される"
    "(3162、3242-3253。G9、同時に最大 4 件、async_agents.py:24)。一般的な仕組み・R4 の"
    "設定:どのハーネスもループに上限を設ける(OpenAI Agents SDK の max_turns は 10、"
    "LangGraph の recursion_limit は 25、どちらも打ち切り)。R4 は "
    "DEFAULT_TOTAL_TOOL_ROUNDS 1000、DEFAULT_SOFT_BLOCK_ROUNDS 25(agent.py:79-80)。上限"
    "ではツールなしのまとめを 1 回(opencode の MAX_STEPS と同じ形、_wrap_up_summary "
    "3699-3722)。出力上限(G19a):max(2 倍、+8,192)、最大 131,072 で 1 回再試行"
    "(3343-3372。Claude Code も同じ形)。その後、切れた推論の末尾 12,000 字を添えて最大 "
    "4 回促す(118-128、196-205。無人実行では 12 回)。空回り対策:"
    "TOOL_FAILURE_STREAK_TRIP 4(agent.py:86)、同一の繰り返しや失敗のままの引数変更が 3 回"
    "(1281-1340)。注意の後に回答モード(3606-3646)。近いのは opencode の doom loop 検査。"
    "無人実行(R4_AUTONOMOUS=1)では発動しても回数を限って促すだけになり、同一呼び出しの"
    "検査(同じ名前と引数が 6 回、または A/B の交互)が有効になる(868-935、3649-3664)。"
    "作業が止まった返答への促し(一般的な仕組み・R4 の設定):OpenHands の無人実行も、"
    "ツールを呼ばない返答には「please continue」と自動で返す。R4 独自なのは回数の上限"
    "(空の返答は最大 2 回、無人実行では 4 回。予告だけは最大 3 回)と「Let me …」の検出"
    "(agent.py:136-160、3513-3545)。R4 独自:25 ラウンドの確認は新しい根拠がないときだけ"
    "差し込む(_block_boundary_control 3666-3697、メッセージ 1046-1062)。冒頭に挙げた "
    "Claude Code などのハーネスにはない仕組み。最も近いのは VS Code の Copilot エージェント"
    "モードで、chat.agent.maxRequests 回に達すると止まり、続けるかを利用者に尋ねる。R4 の"
    "コードもこの確認を「copilot-style」と呼ぶ(agent.py:1047)。違いは、R4 は利用者ではなく"
    "モデルに状況を書かせて続行すること、そして新しい根拠がなかったときだけ差し込むこと。"
    "思考の予算は "
    "16,000 トークンか 600 秒で、クライアント側で打ち切る(237、277)。"
    "R4_COMPANION_FALLBACK_MODEL を設定すると、1 ターンに 1 回だけ代替モデルに切り替える"
    "(G19b、4904-4918。Claude Code の --fallback-model に相当)。読み取り専用・並列可の"
    "呼び出しは既定 8 並列(5097-5117、R4_COMPANION_TOOL_CONCURRENCY)。Claude Code の "
    "isConcurrencySafe と同じ考え方。",
)


def loop_code_slide(deck: Deck) -> None:
    slide = content(deck, ("The agent loop", "エージェントループ"), KICK_A)
    harness_map.mini(slide, "loop")
    code_w = Inches(7.6)
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        code_w,
        Inches(3.1),
        LOOP,
        size=11.5,
        title=("the loop every harness runs (simplified)", "どのハーネスにもあるループ(簡略)"),
    )
    _tab(slide, MARGIN, BODY_TOP, code_w, "general")
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.3),
        code_w,
        Inches(0.7),
        (
            "**turn** = one request of yours · **round** = one model call inside it. "
            "One turn can take many rounds.",
            "**ターン** = 自分の依頼 1 回、**ラウンド** = その中のモデル呼び出し 1 回。"
            "1 ターンに多数のラウンドがありうる。",
        ),
        Style(13, INK),
    )
    x = MARGIN + code_w + Inches(0.3)
    w = CONTENT_W - code_w - Inches(0.3)
    y = BODY_TOP
    for kind, items in _LOOP_GROUPS:
        y = _scope_group(slide, x, y, w, kind, items)
    notes(slide, _LOOP_NOTES)


# ── 17 · Keeping the window from overflowing ─────────────────────────────────

# Review 2026-11-05: the same bar as the context-window slide, before and after
# each step (illustrative sizes on the 32,768-token window).
_BASE = [("system", 2_500, None), ("tools", 10_500, ("tools", "ツール"))]
_OVERFLOW_STEPS = (
    (
        ("A tool prints a lot", "ツールの出力が大量"),
        (
            "keep its beginning and end; the full text goes to a file",
            "先頭と末尾だけ残し、全文はファイルへ",
        ),
        "r4tuned",
        _BASE
        + [
            ("history", 4_000, None),
            ("message", 1_500, None),
            ("over", 14_268, ("one huge result", "巨大な結果 1 件")),
        ],
        _BASE
        + [
            ("history", 4_000, None),
            ("message", 1_500, None),
            ("cut", 3_000, ("head+tail", "先頭末尾")),
            ("free", 11_268, None),
        ],
    ),
    (
        ("Many rounds in one turn", "何ラウンドも続く"),
        (
            "older results shrink to beginning + end; the latest stays whole",
            "古い結果は先頭と末尾に縮め、直近の結果は全文のまま",
        ),
        "r4tuned",
        _BASE
        + [("history", 4_000, None), ("message", 1_500, None)]
        + [("rounds", 3_000, None)] * 4
        + [("rounds", 2_268, None)],
        _BASE
        + [("history", 4_000, None), ("message", 1_500, None)]
        + [("cut", 700, None)] * 4
        + [("rounds", 2_268, ("latest", "直近")), ("free", 9_200, None)],
    ),
    (
        ("A long session", "長いセッション"),
        (
            "near the limit, older turns become **one summary note** (compaction); the latest "
            "turns stay as they are",
            "上限に近づくと、古いターンを**1 つの要約メモ**にまとめる(コンパクション)。直近の"
            "ターンはそのまま",
        ),
        "r4tuned",
        _BASE
        + [
            ("history", 12_000, ("many earlier turns", "多数の過去のターン")),
            ("message", 1_500, None),
            ("free", 6_268, None),
        ],
        _BASE
        + [
            ("summary", 1_500, ("summary", "要約")),
            ("history", 3_000, ("latest", "直近")),
            ("message", 1_500, None),
            ("free", 13_768, None),
        ],
    ),
    (
        ("Still too big", "それでも入らない"),
        (
            "drop extra context, then tool definitions that do not fit, then the oldest history",
            "追加のコンテキスト → 入りきらないツール定義 → 古い履歴、の順に外す",
        ),
        "r4",
        _BASE
        + [
            ("history", 9_000, None),
            ("message", 6_000, ("big message", "大きな依頼")),
            ("over", 4_768, ("too big", "超過")),
        ],
        [
            ("system", 2_500, None),
            ("tools", 6_000, ("fewer tools", "一部のツール")),
            ("history", 4_000, None),
            ("message", 6_000, ("big message", "大きな依頼")),
            ("free", 14_268, None),
        ],
    ),
)

_STEP_LABEL_W = Inches(2.35)
_STEP_PITCH = Inches(1.2)
_MINI_BAR_H = Inches(0.24)


def _overflow_step(slide, y, i: int, step, w) -> None:
    label, body, _kind, before, after = step
    badge(slide, MARGIN, y, str(i + 1), size=Inches(0.3), font=11)
    text_box(
        slide,
        MARGIN + Inches(0.38),
        y - Inches(0.02),
        _STEP_LABEL_W - Inches(0.38),
        Inches(0.3),
        label,
        Style(12, INK, True),
    )
    text_box(
        slide,
        MARGIN,
        y + Inches(0.3),
        _STEP_LABEL_W - Inches(0.1),
        Inches(0.62),
        body,
        Style(10, INK),
    )
    bx = MARGIN + _STEP_LABEL_W
    bw = w - _STEP_LABEL_W
    for j, (segs, tag) in enumerate(((before, ("before", "前")), (after, ("after", "後")))):
        by = y + Inches(0.08) + j * (_MINI_BAR_H + Inches(0.18))
        text_box(
            slide, bx, by - Inches(0.17), Inches(1.0), Inches(0.17), tag, Style(8, MUTED, True)
        )
        context_bar.bar(slide, bx, by, bw, _MINI_BAR_H, segs, size=8)


def _overflow_table(slide, w) -> None:
    text_box(
        slide,
        MARGIN,
        BODY_TOP - Inches(0.05),
        w,
        Inches(0.3),
        (
            "The window bar again (32,768 tokens; sizes illustrative). Steps 1–3: general pattern, "
            "R4's settings; step 4: R4-specific.",
            "ウィンドウの棒(32,768 トークン、量は例)。1〜3 は一般的な仕組み・R4 の設定、4 は R4 独自",
        ),
        Style(12, MUTED),
    )
    for i, step in enumerate(_OVERFLOW_STEPS):
        _overflow_step(slide, BODY_TOP + Inches(0.42) + i * _STEP_PITCH, i, step, w)


_WORKED_EXAMPLE = (
    "qwen2.5:7b, window 32,768 tokens: budget = 32,768 × 0.90 − 4,096 = 25,395. "
    "Compaction starts when prompt + the 4,096 reply reserve reaches 80 % of it (20,316), "
    "i.e. once the prompt passes ≈ 16,200 tokens, about half the window. The tool "
    "definitions alone are ≈ 10,500, so about 5,000 tokens of conversation fit before "
    "compaction (estimates).",
    "qwen2.5:7b(ウィンドウ 32,768 トークン):予算 = 32,768 × 0.90 − 4,096 = 25,395。"
    "プロンプトと応答用の予約 4,096 トークンの合計が予算の 80 %(20,316 トークン)に"
    "達すると要約が始まる。つまり"
    "プロンプトが約 16,200 トークン(ウィンドウの約半分)を超えたとき。ツール定義だけで約 "
    "10,500 トークンあるので、要約までに入る会話は約 5,000 トークン(推定値)。",
)

_FOR_RESEARCH = [
    (
        "Long sessions fill the window: older turns get summarised or dropped — restate "
        "key numbers.",
        "長いセッションではウィンドウが満杯になり、古いターンは要約か削除される。重要な数値は"
        "依頼文で改めて書く。",
    ),
    (
        "A 50 MB CSV does not fit: ask for computation, not “read the whole file”.",
        "50 MB の CSV は入らない。「全部読んで」ではなく計算を頼む。",
    ),
    ("Watch it: /context shows the counts.", "/context で現在のトークン数を確認できる。"),
]


def overflow_slide(deck: Deck) -> None:
    slide = content(
        deck, ("Keeping the window from overflowing", "ウィンドウをあふれさせない工夫"), KICK_A
    )
    harness_map.mini(slide, "context")
    left_w = Inches(7.9)
    _overflow_table(slide, left_w)
    x = MARGIN + left_w + Inches(0.3)
    w = CONTENT_W - left_w - Inches(0.3)
    _box(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(3.05),
        ("Worked example", "計算例"),
        _WORKED_EXAMPLE,
        "r4tuned",
    )
    _box(
        slide,
        x,
        BODY_TOP + Inches(3.2),
        w,
        Inches(2.0),
        ("For your research", "研究での注意"),
        _FOR_RESEARCH,
        fill=TINT_AMBER,
    )
    notes(
        slide,
        (
            "GENERAL · R4 SETTINGS throughout, except the last-resort fit (the 'Still too big' "
            "row). Token estimate (G10): "
            "context.py:44-57 (CHARS_PER_TOKEN 3.5; EMA weight 0.4; clamp 1.5–8), "
            "observe_prompt_usage 268-300; R4_COMPANION_CHARS_PER_TOKEN pins it. Claude Code "
            "anchors on the last reply's API usage the same way; opencode reads provider "
            "usage. Japanese ≈ 1.3 chars/token (slide 12: 22 chars → 17 tokens) stays "
            "under-counted at the 1.5 floor (context.py:54). Exec caps: tools.py:132-138 "
            "(32,000 / 20,000 / 8,000; timeout 180 s, max 1,800 s); Claude Code cuts Bash "
            "output near 30,000 chars. 40k truncate: agent.py:73. Spill 120,000 → "
            ".r4/outputs, preview 2,000 + 1,000: r4/src/r4/agent/large_response.py:38-50; "
            "opencode also keeps the full text in a file. Within a turn: agent.py:2044-2101 "
            "(1,024 + 1,024, last round protected), 2155-2168 (40 rounds / 400 chars, moved in "
            "blocks of 10 for the prefix cache), 5484-5489 (25 % of the window); Claude Code's "
            "microcompact and the Anthropic API's context editing clear old tool results "
            "similarly. Between turns: session_turns.py:119-152 (the 80 % check first, then "
            "microcompact; if the bounded history fits, no summary), microcompact "
            "context.py:211-245 (G11), compact_turn 533-560 (default; legacy compact "
            "496-531), breaker 186-206 (G12; Claude Code's breaker is also 3). Trigger: "
            "prompt + 4,096 reserve ≥ 80 % (context.py:131, 395-398). Budget: "
            "context_budget.py:16-17, 82-87. R4-SPECIFIC: the hard fit drops tool definitions "
            "that do not fit and keeps the newest history that fits, no minimum "
            "(context.py:403, 738-780); other harnesses fail the request instead. Tool "
            "schemas ≈ 10.5k tokens: deploy/standalone/env/base.env:32-34. G12 pressure "
            "levels exist (context.py:177) but nothing reads them yet, so they are not on "
            "the slide. Review 2026-11-01: the slide now names mechanisms only; the counts "
            "(32,000 / 20,000 / 8,000, 40 rounds / 400 chars, 25 %, the breaker's 3 failures) "
            "stay in these notes. Two different 'retries' exist and were easy to mix up: a "
            "provider error is retried up to 3 attempts in all (agent.py:4847-4852, "
            "R4_COMPANION_TRANSIENT_RETRIES), and a context-overflow error trims the oldest "
            "quarter and retries ONCE (agent.py:696-718, 4786-4794).",
            "最後の手段の切り詰め(「それでも入りきらない」の行)を除き、すべて「一般的な仕組み・"
            "R4 の設定」。トークン数の推定(G10):"
            "context.py:44-57(CHARS_PER_TOKEN 3.5、移動平均の重み 0.4、範囲 1.5〜8)、"
            "observe_prompt_usage 268-300。R4_COMPANION_CHARS_PER_TOKEN で固定できる。Claude "
            "Code も直前の応答の実使用量を基準にし、opencode も事業者が返す使用量(usage)を読む。"
            "日本語は"
            "約 1.3 字/トークン(スライド 12:22 字 → 17 トークン)なので、下限 1.5 では少なめに"
            "数える(context.py:54)。実行出力の上限:tools.py:132-138(32,000 / 20,000 / "
            "8,000。タイムアウト 180 秒、最大 1,800 秒)。Claude Code も Bash の出力を約 3 万字"
            "で切る。4 万字の切り詰め:agent.py:73。12 万字超の退避と 2,000 + 1,000 字の抜粋:"
            "r4/src/r4/agent/large_response.py:38-50。opencode も全文をファイルに残す。"
            "1 ターン内:agent.py:2044-2101(1,024 + 1,024 字、直前のラウンドは保護)、"
            "2155-2168(40 ラウンド/400 字、プレフィックスキャッシュのため 10 ラウンド単位で"
            "動かす)、5484-5489(ウィンドウの 25 %)。Claude Code の microcompact や Anthropic "
            "API の context editing も古いツール結果を消す。ターンの間:session_turns.py:"
            "119-152(まず 80 % を判定し、次にマイクロコンパクト。収まれば要約しない)、マイクロ"
            "コンパクト context.py:211-245(G11)、compact_turn 533-560(既定。旧 compact は "
            "496-531)、連続失敗の遮断 186-206(G12。Claude Code も 3 回)。発動条件:"
            "プロンプト + 予約 4,096 ≥ 80 %(context.py:131、395-398)。予算:"
            "context_budget.py:16-17、82-87。R4 独自:最後の手段では入りきらないツール定義を"
            "外し、入る範囲で新しい履歴を残す。下限はない(context.py:403、738-780)。他の実装は"
            "リクエストを失敗させる。ツール定義は約 10.5k トークン:"
            "deploy/standalone/env/base.env:32-34。G12 の圧力レベル(context.py:177)はまだ"
            "どこからも使われていないので、スライドには載せない。2026-11-01 のレビュー:スライドは"
            "仕組みだけを示し、数値(32,000 / 20,000 / 8,000、40 ラウンド/400 字、25 %、遮断までの"
            "連続 3 回の失敗)はこのノートに残す。混同しやすい「再試行」は 2 種類ある:事業者側の"
            "一時的なエラーは合計 3 回まで試す(agent.py:4847-4852、R4_COMPANION_TRANSIENT_RETRIES)。"
            "コンテキスト超過のエラーでは古い 4 分の 1 を削って 1 回だけ再試行する"
            "(agent.py:696-718、4786-4794)。",
        ),
    )


# ── 18 · Guardrails ──────────────────────────────────────────────────────────

# ADR-104 (2026-10-31): PermissionManager.decide walks 13 rungs (_LADDER).
# Review 2026-11-05 ("complex entirely"): four rows — never, always ask (the
# floor), runs without asking, otherwise ask. The rung mapping is in the notes.
_ASK_ONCE = ("ask (this call)", "確認(今回のみ)")
_RUNGS = (
    (
        (
            "**Never:** deny mode, or a deny rule you wrote",
            "**実行しない:**拒否モード、または自分で書いた拒否ルール",
        ),
        ("block", "拒否"),
        AMBER,
        None,
    ),
    (
        (
            "**Always asks:** credential files (~/.ssh …) · force-push · knowledge "
            "decisions · your ask rules; in chat / plan also arguments copied from text "
            "someone else wrote (documents, web pages, MCP results) and protected paths",
            "**必ず確認:**認証情報のファイル(~/.ssh など)・force-push・知識の判断・"
            "自分で書いた ask ルール。chat・plan ではさらに、他人が書いた文字列(文書・"
            "Web ページ・MCP の結果)から写した引数と保護パス",
        ),
        _ASK_ONCE,
        AMBER,
        "r4",
    ),
    (
        (
            "**Runs without asking:** you already said “always”, an allow rule, a read-only "
            "tool, or autopilot (a delete it cannot back up still asks)",
            "**確認なしで実行:**以前に「常に」と答えた・allow ルール・読み取り専用のツール・"
            "autopilot(バックアップできない削除は確認する)",
        ),
        ("run", "自動実行"),
        OCEAN,
        None,
    ),
    (
        (
            "**Otherwise:** a write or a command not yet approved",
            "**それ以外:**まだ承認していない書き込みやコマンド",
        ),
        ("ask you", "確認"),
        AMBER,
        None,
    ),
)
_FLOOR_ROWS = 2

_RUNG_TOP = BODY_TOP + Inches(0.45)
_RUNG_PITCH = Inches(0.92)
_RUNG_H = Inches(0.82)
_RUNG_W = Inches(4.5)
_BAND_H = Inches(0.34)


def _rung_y(i: int) -> int:
    """Top of row ``i``; rows below the floor sit under the floor band."""
    return int(_RUNG_TOP + i * _RUNG_PITCH + (_BAND_H if i >= _FLOOR_ROWS else 0))


def _rung(slide, i: int, rung) -> None:
    q, verdict, color, kind = rung
    y = _rung_y(i)
    badge(slide, MARGIN, y + (_RUNG_H - Inches(0.34)) // 2, str(i + 1), size=Inches(0.34), font=11)
    rect(slide, MARGIN + Inches(0.45), y, _RUNG_W, _RUNG_H, fill=TINT, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.58),
        y,
        _RUNG_W - Inches(0.2),
        _RUNG_H,
        q,
        Style(12, INK),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    chip_x = MARGIN + Inches(0.55) + _RUNG_W
    chip(
        slide,
        chip_x,
        y + (_RUNG_H - Inches(0.32)) // 2,
        verdict,
        fill=color,
        color=WHITE if color == OCEAN else INK,
        w=Inches(1.4),
        h=Inches(0.32),
        size=11,
    )
    if kind:
        scope_tag(slide, chip_x + Inches(1.47), y + (_RUNG_H - Inches(0.28)) // 2, kind)


def _ladder(slide) -> None:
    scope_tag(slide, MARGIN, BODY_TOP + Inches(0.02), "r4tuned")
    text_box(
        slide,
        MARGIN + _pill_w("r4tuned") + Inches(0.12),
        BODY_TOP,
        Inches(5.0),
        Inches(0.32),
        ("Permission ladder, every tool call", "許可の判定(ツール呼び出しのたびに)"),
        Style(14, INK, True),
    )
    for i, rung in enumerate(_RUNGS):
        _rung(slide, i, rung)
    band_y = _RUNG_TOP + _FLOOR_ROWS * _RUNG_PITCH - Inches(0.04)
    rect(slide, MARGIN, band_y + Inches(0.02), _RUNG_W + Inches(1.95), Inches(0.02), fill=ALERT)
    text_box(
        slide,
        MARGIN + Inches(0.45),
        band_y + Inches(0.04),
        _RUNG_W + Inches(1.5),
        _BAND_H,
        (
            "↑ checked first: no rule or earlier “always” gets past rows 1–2 (autopilot: see †)",
            "↑ ここまでを先に判定:ルールや以前の「常に」でも 1〜2 は越えられない(autopilot は † を参照)",
        ),
        Style(10, ALERT, True),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    text_box(
        slide,
        MARGIN,
        _rung_y(len(_RUNGS)) + Inches(0.02),
        Inches(7.4),
        Inches(0.62),
        (
            "Top to bottom; the first row that answers decides. Claude Code orders its rules "
            "the same way (deny → ask → allow; a saved “always” cannot override an ask).",
            "上から順に判定し、最初に答えが出た行で決まる。Claude Code も同じ順(拒否 → 確認 → "
            "許可。保存した「常に」は確認のルールを越えない)。",
        ),
        Style(11, MUTED),
    )


def _guard_side(slide) -> None:
    x = MARGIN + Inches(7.75)
    w = CONTENT_W - Inches(7.75)
    mode_rows = [
        [("Mode", "モード"), ("read-only", "読み取り専用"), ("other tools", "その他のツール")],
        [("chat (default)", "chat(既定)"), ("run", "自動実行"), ("ask", "確認")],
        [("plan", "plan"), ("run", "自動実行"), ("refused*", "実行しない*")],
        [("autopilot", "autopilot"), ("run", "自動実行"), ("run†", "自動実行†")],
    ]
    table(slide, x, BODY_TOP, w, mode_rows, [1.9, 1.3, 1.2], size=11, row_h=Inches(0.36))
    scope_tag(slide, x + w - _pill_w("r4tuned"), BODY_TOP + Inches(1.56), "r4tuned")
    text_box(
        slide,
        x,
        BODY_TOP + Inches(1.54),
        w,
        Inches(0.3),
        ("one mode: Shift+Tab or /mode", "モード:Shift+Tab・/mode"),
        Style(12, OCEAN, True),
    )
    text_box(
        slide,
        x,
        BODY_TOP + Inches(1.84),
        w,
        Inches(0.66),
        (
            "* a plan first; accepting it switches to autopilot (a) or chat (m)\n"
            "† asks only for credentials, knowledge decisions, your ask rules and a delete "
            "it cannot back up (copies go to ~/.local/share/r4/trash)",
            "* まず計画。承認すると autopilot(a)か chat(m)に切り替わる\n"
            "† 確認するのは認証情報、知識の判断、自分の ask ルール、バックアップできない削除"
            "だけ(コピーは ~/.local/share/r4/trash)",
        ),
        Style(10, MUTED),
    )
    _box(
        slide,
        x,
        BODY_TOP + Inches(2.62),
        w,
        Inches(1.36),
        ("Web, documents, MCP: the same concern", "Web・文書・MCP:同じ種類の問題"),
        (
            "All three are text someone else wrote, which can hide instructions (prompt "
            "injection) — so a call copying from them asks. Web tools are off until you pick "
            "/web: none · allowlist · public.",
            "どれも他人が書いた文字列で、隠れた指示(プロンプトインジェクション)を含みうる。"
            "写した呼び出しは確認する。Web のツールは /web を選ぶまでオフ。",
        ),
        "r4",
    )
    _box(
        slide,
        x,
        BODY_TOP + Inches(4.12),
        w,
        Inches(1.2),
        ("OS sandbox: off unless you turn it on", "OS サンドボックス:自分で有効にする"),
        (
            "/sandbox on confines shell, Python and MCP (bubblewrap / Seatbelt). Off by "
            "default: approving then runs the call **with your own user permissions**.",
            "/sandbox on でシェル・Python・MCP を隔離する(bubblewrap・Seatbelt)。既定は"
            "オフで、承認すると**自分の権限でそのまま実行**される。",
        ),
        "r4tuned",
        fill=TINT_AMBER,
    )


def guardrail_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Guardrails in R4: one permission gate, sandbox opt-in",
            "R4 の安全装置:許可ゲートとサンドボックス",
        ),
        KICK_A,
    )
    harness_map.mini(slide, "gate")
    _ladder(slide)
    _guard_side(slide)
    notes(
        slide,
        (
            "GENERAL PATTERN: every harness gates tool calls (Claude Code: one permission "
            "mode cycled with Shift+Tab, deny/ask/allow rules, safety checks no mode "
            "bypasses; opencode: build/plan agents and allow/ask/deny per tool, the client "
            "answers once/always/reject; OpenAI Agents SDK tool approval; LangChain "
            "HumanInTheLoopMiddleware). R4 follows the same shape since ADR-104 (2026-10-31). "
            "Each call goes skill/plan refusal → PreToolUse hook → permission gate → run "
            "(agent.py:5165-5229; refusal 5207, hook 5231, gate 5259). ONE MODE: chat / plan "
            "/ autopilot (companion/agent_mode.py:58-140); the permission mode is derived "
            "(chat and plan auto_safe, autopilot trust) and never stored; /trust = /mode "
            "autopilot; accepting a plan switches to autopilot (a) or chat (m, or typing "
            "yes). ONE DECISION: PermissionManager.decide walks _LADDER (permissions.py:622, "
            "823-830; the 13 _rung_* methods 882-972) and returns a Decision; check = decide "
            "+ resolve (832-846). Review 2026-11-05 ('complex entirely'): four rows. Slide rows → "
            "code rungs: row 1 = rungs 1-2 (deny mode, deny rule); row 2 = 3-7 (never-auto "
            "tools, 185; taint fence; credential fence, 307-534; ask rule; protected path); "
            "row 3 = 9-12 (allow rule; read-only SAFE_TOOLS, 75; session 'always'; autopilot); "
            "row 4 = 13 (ask); rung 8 (a template crew's plan card) is left out. Web: web_fetch "
            "/ web_search results taint as 'web' (r4_contracts/taint.py:45-47); the tools are "
            "off until /web picks allowlist (web_fetch) or public (both) (companion/"
            "web_access.py:29, 58-80; tool_availability.py:249-268); in chat a web call asks "
            "(not read-only), in autopilot it runs, and prose copied from a web result asks in "
            "every mode (permissions.py:982-995). Claude Code checks deny → ask → allow and a "
            "saved 'always' cannot override an ask rule (code.claude.com/docs/en/permissions, "
            "2026-11-05). Code order of the allowances (ADR-104 amendment 1, "
            "2026-11-03): allow rule → read-only → session 'always' → autopilot. Before it, "
            "autopilot came first and, signed in, consulted the server trust allowlist, which "
            "nothing seeds: autopilot(server) asked for every tool, even file_read, and a "
            "session 'always' did not stop the next ask. Now autopilot runs what the floor lets "
            "through; an organisation can opt in to narrowing it to its server allowlist "
            "(R4_AUTOPILOT_SERVER_POLICY=1, ADR-035; badge autopilot(policy)). Review "
            "2026-11-01 asked whether past approvals should be checked before the confirmation "
            "rows. They are checked before the final ask, but deliberately after the floor "
            "(docstring 613-619): a remembered 'always' must never bypass the taint or "
            "credential fence. The old per-call approval cache (1,800 s) had no caller and was "
            "removed (_remembered_approval_ttl, 58-71). Rule files "
            "(permission_rules.py:42-44): ~/.config/r4/permissions.json and "
            "~/.config/r4/projects/<id>/permissions.json allow/ask/deny; a checked-in "
            ".r4/permissions.json only ask/deny; names match with wildcards (mcp.*), "
            "shell_exec(prefix:pytest) matches a command prefix; protected paths 60-80, "
            "apply_patch read from its patch (308-347); file tools only, so echo … >> "
            "~/.bashrc in a shell is not covered. APPROVALS: the companion decides what a "
            "card offers (approval_answers.py; bridge_server.py _on_tool_approval): y once, a "
            "this tool for the rest of the session (kept by the companion), n reject with "
            "feedback, r/Esc reject. 'Always for this project' (p) was removed in amendment 1: "
            "one keypress wrote a durable, argument-blind allow rule for every later session "
            "(p on shell_exec = any command); standing rules are deliberate configuration in "
            "permissions.json, managed with /settings rules. A never-auto or fenced ask (row 2) offers "
            "only y and n; an 'always' sent for it counts as once. The console renders and "
            "queues cards and never answers by itself (packages/r4-tui-v3/src/bridge/"
            "approval-bridge.ts:178-260). n no longer hides the tool: only a deny that holds "
            "for the whole tool does (agent.py:5299-5335, 2955). Before ADR-104 the console "
            "auto-approved every later call of a tool after one y (ls then rm -rf) and a "
            "plan accepted with auto-approve stayed in TRUST after leaving plan. Still open: "
            "an ask from the ADR-076 decision gate (permissions.py:865-880) is not fenced. "
            "Hooks: user_hooks.py (exit 2 blocks; project hooks only with "
            "R4_COMPANION_PROJECT_HOOKS=1), the same contract as Claude Code's hooks. "
            "R4-SPECIFIC: always-ask tools, the taint fence (r4-contracts/src/r4_contracts/"
            "taint.py: search_docs, rag_query, open_passage, knowledge_*, context_pack, "
            "web_*, mcp_invoke, mcp.*, lab_*, team_plan_*; min excerpt 24; a shell_exec / "
            "python_exec call that reads the network is web, and so is a later read of a "
            "file it saved: companion/taint.py, taint_exec.py) and the credential fence. No "
            "mainstream harness tracks argument provenance. SANDBOX (ADR-101, 2026-10-30): "
            "opt-in, off by default and announced at every session open; /sandbox on or r4 "
            "--sandbox on runs shell, Python, code tools and local MCP servers under "
            "bubblewrap (Linux) or Seatbelt (macOS) (companion/sandbox/). Off, an approved "
            "python_exec is a host subprocess with your rights (tools.py:372). Claude Code "
            "also offers an optional OS sandbox; Codex CLI sandboxes by default. AMENDMENT 2 "
            "(2026-11-05, maintainer: 'true autopilot mode (except file delete if no full "
            "backup exists)'): in autopilot the taint fence, protected paths, plain git push, "
            "plan gates, team-plan budget cards, job_commit (an auto commit: UMRS's threshold "
            "decides) and decision-model doubts no longer ask; each shows as an [autopilot] "
            "line. Still asking: credential access and force-push, knowledge decisions "
            "(CONSTITUTION §2), your ask rules, and a delete R4 cannot back up. A delete "
            "nobody approved (autopilot, allow rule, session 'always') is copied to "
            "~/.local/share/r4/trash first unless git holds the file committed "
            "(companion/delete_guard.py); globs, variables, git clean, find -delete and "
            "computed Python paths ask.",
            "一般的な仕組み:どのハーネスもツール呼び出しに許可ゲートを置く(Claude Code は "
            "Shift+Tab で切り替える一つのモード、deny/ask/allow ルール、どのモードでも越えられ"
            "ない安全確認。opencode は build/plan のエージェントとツールごとの allow/ask/deny で、"
            "クライアントは once/always/reject と答える。OpenAI Agents SDK の承認、LangChain の "
            "HumanInTheLoopMiddleware)。R4 も ADR-104(2026-10-31)から同じ形。呼び出しは、"
            "スキルや plan による拒否 → PreToolUse フック → 許可ゲート → 実行 の順"
            "(agent.py:5165-5229。拒否 5207、フック 5231、ゲート 5259)。モードは一つ:chat・"
            "plan・autopilot(companion/agent_mode.py:58-140)。許可モードはモードから決まり"
            "(chat と plan は auto_safe、autopilot は trust)、単独では保存しない。/trust は "
            "/mode autopilot と同じ。計画を承認すると autopilot(a)か chat(m、または yes と"
            "入力)に切り替わる。判定は一つ:PermissionManager.decide が _LADDER をたどり"
            "(permissions.py:622、823-830。13 の _rung_* メソッドは 882-972)、Decision を返す。"
            "check は decide と resolve(832-846)。2026-11-05 のレビュー(「全体に複雑」):4 行に"
            "まとめた。スライドの行とコードの段:1 行 = 1〜2 段(拒否モード、deny ルール)、"
            "2 行 = 3〜7 段(常に確認が必要なツール 185、汚染フェンス、認証情報フェンス 307-534、"
            "ask ルール、保護パス)、3 行 = 9〜12 段(allow ルール、読み取り専用の SAFE_TOOLS 75、"
            "セッションの「常に」、autopilot)、4 行 = 13 段(確認)。8 段(テンプレートのチームの"
            "計画カード)は省略。Web:web_fetch / web_search の結果は「web」として汚染に記録される"
            "(r4_contracts/taint.py:45-47)。ツールは /web で allowlist(web_fetch)か public"
            "(両方)を選ぶまでオフ(companion/web_access.py:29、58-80、tool_availability.py:"
            "249-268)。chat では Web の呼び出しは確認になり(読み取り専用ではない)、autopilot では"
            "実行される。Web の結果から写した文章はどのモードでも確認(permissions.py:982-995)。"
            "Claude Code も deny → ask → allow の順で判定し、保存した「常に」は ask のルールを"
            "越えない(code.claude.com/docs/en/permissions、2026-11-05 確認)。許可の段のコード上の順は "
            "allow ルール → 読み取り専用 → セッションの「常に」→ autopilot(ADR-104 の改訂 1、"
            "2026-11-03)。以前は autopilot が先で、サインイン中はサーバの信頼許可リストを見ていた"
            "が、そのリストは誰も用意しないので、autopilot(server) は file_read まですべて確認し、"
            "セッションの「常に」でも次の確認は止まらなかった。今は autopilot は下限が通したものを"
            "実行する。組織がサーバの許可リストで絞ることもできる(R4_AUTOPILOT_SERVER_POLICY=1、"
            "ADR-035。表示は autopilot(policy))。"
            "2026-11-01 のレビューで「過去の許可を確認の行より先に見るべきでは」と議論した。コード"
            "では最後の確認より前に見るが、下限より後に置いている(docstring 613-619):覚えている"
            "「常に」が汚染フェンスや認証情報フェンスを越えてはならないため。以前の呼び出し単位の"
            "承認キャッシュ(1,800 秒)は呼び出し元がなく削除済み(_remembered_approval_ttl、"
            "58-71)。ルール"
            "ファイル(permission_rules.py:42-44):~/.config/r4/permissions.json と "
            "~/.config/r4/projects/<id>/permissions.json は allow/ask/deny、リポジトリの "
            ".r4/permissions.json は ask/deny だけ。ツール名はワイルドカード(mcp.*)で、"
            "shell_exec(prefix:pytest) はコマンドの先頭で照合。保護パスは 60-80、apply_patch は"
            "パッチの中身から読む(308-347)。ファイル操作ツールだけが対象なので、シェルでの "
            "echo … >> ~/.bashrc は対象外。承認:カードに出す答えはコンパニオンが決める"
            "(approval_answers.py、bridge_server.py の _on_tool_approval)。y は 1 回、a はこの"
            "ツールをセッションの残りの間(コンパニオンが覚える)、n は理由つきで拒否、r・Esc は"
            "拒否。「このプロジェクトで常に」(p)は改訂 1 で廃止:1 回のキー操作で、引数を見ない"
            "永続の allow ルールが以後のすべてのセッションに残っていた(shell_exec に p = どんな"
            "コマンドでも)。常設のルールは permissions.json に意図して書き、/settings rules で"
            "管理する。常に確認が必要なツールとフェンスによる確認"
            "(2 行目)は y と n だけで、「常に」と送っても 1 回だけの許可として扱う。コンソール"
            "はカードを表示して順に並べるだけで、自分では答えない(packages/r4-tui-v3/src/bridge/"
            "approval-bridge.ts:178-260)。n でツールは隠れない。ツール全体を拒否するときだけ"
            "隠す(agent.py:5299-5335、2955)。ADR-104 以前は、コンソールが一度 y と答えた"
            "ツールをその後すべて自動承認し(ls のあと rm -rf も)、自動承認で受け入れた計画は "
            "plan を抜けても TRUST のままだった。未対応:ADR-076 の判定ゲートによる確認"
            "(permissions.py:865-880)はフェンスの対象外。フック:user_hooks.py(終了コード 2 で"
            "止める。プロジェクトのフックは R4_COMPANION_PROJECT_HOOKS=1 のときだけ)。Claude "
            "Code のフックと同じ約束。R4 独自:常に確認が必要なツール、汚染フェンス"
            "(r4-contracts/src/r4_contracts/taint.py:search_docs・rag_query・open_passage・"
            "knowledge_*・context_pack・web_*・mcp_invoke・mcp.*・lab_*・team_plan_*、最小 24 "
            "字。ネットワークから取得する shell_exec・python_exec の結果と、その呼び出しが保存"
            "したファイルをあとで読んだ結果は Web 由来:companion/taint.py、taint_exec.py)、"
            "認証情報フェンス。引数の由来を追跡する主要なハーネスはほかにない。サンドボックス"
            "(ADR-101、2026-10-30):選択式で既定はオフ、セッションを開くたびに知らせる。"
            "/sandbox on か r4 --sandbox on で、シェル・Python・コード系ツール・ローカルの MCP "
            "サーバを bubblewrap(Linux)か Seatbelt(macOS)の中で動かす(companion/sandbox/)。"
            "オフのとき、承認した python_exec はホストの子プロセスとして自分の権限で動く"
            "(tools.py:372)。Claude Code も OS サンドボックスを選べ、Codex CLI は既定で使う。"
            "改訂 2(2026-11-05、メンテナ「バックアップのない削除以外は本当の autopilot に」):"
            "autopilot では汚染フェンス、保護パス、通常の git push、計画のゲート、チームの計画の"
            "予算カード、job_commit(自動コミット。UMRS のしきい値が判断)、判断モデルの疑いは"
            "確認しなくなり、それぞれ [autopilot] の行として残る。確認が残るのは、認証情報への"
            "アクセスと強制 push、知識の判断(CONSTITUTION §2)、自分の ask ルール、"
            "バックアップできない削除。誰も承認していない削除(autopilot、allow ルール、"
            "セッションの「常に」)は、git にコミット済みでない限り ~/.local/share/r4/trash に"
            "先にコピーする(companion/delete_guard.py)。glob、変数、git clean、find -delete、"
            "Python で計算したパスは確認になる。",
        ),
    )


# ── 19 · Skills and MCP ──────────────────────────────────────────────────────

_STAGES = (
    (
        ("Always in the prompt", "常にプロンプトに"),
        (
            "one line per skill in the system prompt (≤ 40 lines × 120 chars): "
            "“residual-report — Writes a P-wave residual report …”",
            "システムプロンプトにスキルごとに 1 行(最大 40 行 × 120 字):"
            "「residual-report — P 波の残差レポートを書く…」",
        ),
        None,
    ),
    (
        ("Switched on — two ways", "有効化 — 2 つの方法"),
        (
            "**Usual way (Claude Code, opencode, Codex):** the model reads the catalog line and "
            "decides to load the skill, or you call it by name (/skill-name). **R4 adds:** a "
            "scan of your message for the skill's keywords (x-r4-keywords, whole words) — a "
            "hit loads it without the model choosing. R4's model can also call activate_skill.",
            "**一般的な方法(Claude Code・opencode・Codex):**モデルが一覧の 1 行を読んで読み込みを"
            "決める。または利用者が名前で呼ぶ(/スキル名)。**R4 が加える方法:**依頼文の中から"
            "スキルのキーワード(x-r4-keywords、単語単位)を探し、一致すればモデルの判断なしで"
            "読み込む。R4 でもモデルが activate_skill を呼べる。",
        ),
        "r4tuned",
    ),
    (
        ("Then", "その後"),
        (
            "a keyword hit puts the full SKILL.md body into this turn's context "
            "(activate_skill: from the next turn); tools outside its allowed-tools are "
            "refused before any approval — your shell, python and file tools never are.",
            "キーワードで有効になれば、このターンから SKILL.md の本文がコンテキストに入る"
            "(activate_skill なら次のターンから)。allowed-tools(使ってよいツールの一覧)にない"
            "ツールは承認の前に拒否される。ただしシェル・python・ファイル操作のツールは常に使える。",
        ),
        "r4",
    ),
)

_SKILL_EXAMPLE = [
    (
        "You type: “Write the **residual** report for these picks.”",
        "依頼:「この読み取りの**残差**レポートを書いて」",
    ),
    (
        "The harness finds “residual” among residual-report's keywords and puts its SKILL.md "
        "body into **this turn's** context.",
        "ハーネスが residual-report のキーワードに「残差」を見つけ、SKILL.md の本文を"
        "**このターンの**コンテキストに入れる。",
    ),
    (
        "The model reads the steps (pick rules, report format) and calls the tools the skill "
        "allows — as tool calls, through the gate.",
        "モデルは手順(読み取り規則・レポートの書式)を読み、スキルが許すツールを呼ぶ。"
        "呼び出しはいつもどおり許可ゲートを通る。",
    ),
    (
        "The answer follows the observatory's report format; next turn the body is gone unless "
        "a keyword hits again.",
        "回答は観測所のレポート書式に従う。次のターンでは、キーワードが再び一致しない限り本文は"
        "入らない。",
    ),
]


_SKILL_W = Inches(6.25)


def _stage_h(body) -> int:
    return int(max(Inches(0.8), _text_h(body, 12, _SKILL_W - Inches(0.4)) + Inches(0.44)))


def _stage(slide, y, i: int, stage) -> int:
    """One disclosure stage box; returns its height."""
    head, body, kind = stage
    h = _stage_h(body)
    rect(slide, MARGIN, y, _SKILL_W, h, fill=TINT_AMBER if kind else TINT, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.2),
        y + Inches(0.08),
        Inches(3.8),
        Inches(0.32),
        head,
        Style(14, OCEAN, True),
    )
    text_box(
        slide,
        MARGIN + Inches(0.2),
        y + Inches(0.42),
        _SKILL_W - Inches(0.4),
        h - Inches(0.45),
        body,
        Style(12, INK),
    )
    if kind:
        scope_tag(slide, MARGIN + _SKILL_W - Inches(0.15) - _pill_w(kind), y + Inches(0.08), kind)
    if i < len(_STAGES) - 1:
        down_arrow(slide, MARGIN + Inches(2.9), y + h + Inches(0.01), h=Inches(0.12))
    return h


def _skills_column(slide) -> None:
    text_box(
        slide,
        MARGIN,
        BODY_TOP - Inches(0.05),
        _SKILL_W,
        Inches(0.4),
        ("Skills — progressive disclosure", "スキル — 段階的な読み込み(progressive disclosure)"),
        Style(17, OCEAN, True),
    )
    y = first = BODY_TOP + Inches(0.45)
    for i, stage in enumerate(_STAGES):
        y += _stage(slide, y, i, stage) + Inches(0.12)
    _tab(slide, MARGIN, first, _SKILL_W, "general")
    scope_tag(slide, MARGIN, y - Inches(0.04), "r4tuned")
    shift = _pill_w("r4tuned") + Inches(0.1)
    text_box(
        slide,
        MARGIN + shift,
        y - Inches(0.04),
        _SKILL_W - shift,
        BODY_TOP + Inches(5.2) - y,
        (
            "A skill limits the tools and the model needs one it does not list? It calls "
            "tool_search; the top 5 matches become usable from the next turn. Placement: the "
            "catalog sits in the system prompt, the body in the <turn_context> block after "
            "your message (see the context-window slide).",
            "スキルでツールが制限されていて一覧にないツールが必要なら、モデルが tool_search "
            "を呼ぶ。上位 5 件が次のターンから使える。置き場所:スキル一覧はシステムプロンプト、"
            "本文は依頼文の後ろの <turn_context> ブロック(コンテキストウィンドウのスライドを"
            "参照)。",
        ),
        Style(11, MUTED, italic=True),
    )


def _skill_example(slide) -> None:
    x = MARGIN + _SKILL_W + Inches(0.35)
    w = CONTENT_W - _SKILL_W - Inches(0.35)
    text_box(
        slide,
        x,
        BODY_TOP - Inches(0.05),
        w,
        Inches(0.4),
        ("Example: one request, one skill (Lab 1.4 D)", "例:1 つの依頼と 1 つのスキル(Lab 1.4 D)"),
        Style(17, OCEAN, True),
    )
    y = _steps(
        slide,
        x,
        BODY_TOP + Inches(0.45),
        w,
        _SKILL_EXAMPLE,
        h=Inches(0.66),
        size=11,
        gap=Inches(0.07),
    )
    _tab(slide, x, BODY_TOP + Inches(0.45), w, "r4")
    _box(
        slide,
        x,
        y + Inches(0.08),
        w,
        BODY_TOP + Inches(5.25) - y - Inches(0.08),
        ("Which works better? Not measured yet", "どちらが有効か? まだ測っていない"),
        [
            (
                "**Keywords:** predictable, but literal — a synonym or another language misses "
                "(write Japanese keywords too).",
                "**キーワード:**動きが予測できるが、字面どおり。言い換えや別の言語では一致しない"
                "(日本語のキーワードも書く)。",
            ),
            (
                "**Model's choice:** understands paraphrases, but only works if the model "
                "notices — write a description that says when to use the skill.",
                "**モデルの判断:**言い換えも分かるが、モデルが気づいたときだけ。いつ使うかを説明文"
                "に書いておく。",
            ),
        ],
        fill=TINT_AMBER,
        size=11,
    )


def skills_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Skills: how-to notes loaded on demand", "スキル:必要なときだけ読み込む手順書"),
        KICK_A,
    )
    harness_map.mini(slide, "skills")
    _skills_column(slide)
    _skill_example(slide)
    notes(
        slide,
        (
            "GENERAL PATTERN: progressive disclosure is how Claude Code / the Claude Agent "
            "SDK load Agent Skills (name + description in the prompt, SKILL.md when relevant); "
            "opencode reads SKILL.md too. Catalog: skills.py:376-395 (40 × 120). "
            "R4-SPECIFIC: in Claude Code the MODEL picks a skill from its description; R4 adds "
            "a deterministic whole-word keyword scan of your message at turn start "
            "(skills.py:159-177, optional s/es; auto_activate + dependencies 312-344; "
            "session_flow.py:30-53). Review 2026-11-05 asked whether this is really R4's own and which "
            "works better: Claude Code, opencode and Codex document only model choice from the "
            "description or explicit invocation (Claude Code adds path- and import-based "
            "auto-loading for a few skills); no keyword scan (docs checked 2026-11-05). Which is "
            "more effective has NOT been measured in R4; the one observation on record is glm-5.3 "
            "not calling activate_skill('code') on a Terminal-Bench hello-world task "
            "(benchmarks/harness/terminal/README.md:243-248). activate_skill "
            "→ body from the next turn (session.py:375-394). Claude Code's allowed-tools "
            "WIDENS (those tools run without asking); R4's narrows: tools outside it are "
            "refused before any approval, never the workspace or always-on tools "
            "(skills.py:187-221; session.py:980-996). GENERAL · R4 SETTINGS: tool_search "
            "(tool_search.py:28-35, top 5, stopwords; hits leave the refusal set, "
            "session.py:993-996; matters only under a skill limit). Claude Code's ToolSearch "
            "defers MCP schemas instead. The example on the right is Lab 1.4 D "
            "(docs/tutorial/data/skills/residual-report; its x-r4-keywords include 残差, so "
            "the Japanese request matches too). MCP moved to its own slide (review "
            "2026-11-01).",
            "一般的な仕組み:段階的な読み込みは Claude Code / Claude Agent SDK の Agent Skills "
            "と同じ(名前と説明だけをプロンプトに置き、必要なときに SKILL.md を読む)。opencode "
            "も SKILL.md を読む。一覧:skills.py:376-395(40 行 × 120 字)。R4 独自:Claude "
            "Code ではモデルが説明を見てスキルを選ぶ。R4 はさらに、ターンの最初に依頼文を単語単位"
            "でキーワード検索する(skills.py:159-177、複数形 s/es も可。auto_activate と依存スキル "
            "312-344、session_flow.py:30-53)。2026-11-05 のレビューで「本当に R4 独自か、どちらが有効か」"
            "と問われた:Claude Code・opencode・Codex の文書にあるのは、説明文をもとにしたモデルの判断と"
            "明示的な呼び出しだけ(Claude Code は一部のスキルをパスや import で自動読み込み)。キーワード"
            "による検索はない(2026-11-05 確認)。どちらが有効かは R4 では測っていない。記録にある観察は、"
            "glm-5.3 が Terminal-Bench の hello-world で activate_skill('code') を呼ばなかった 1 件だけ"
            "(benchmarks/harness/terminal/README.md:243-248)。"
            "activate_skill は次のターンから本文が入る(session.py:375-394)。Claude Code の "
            "allowed-tools は許可を広げる(確認なしで使える)が、R4 では狭める。一覧にないツール"
            "は承認の前に拒否され、作業用ツールと常時有効のツールは拒否されない(skills.py:"
            "187-221、session.py:980-996)。一般的な仕組み・R4 の設定:tool_search"
            "(tool_search.py:28-35、上位 5 件、ストップワード除外。見つかったツールは拒否の対象"
            "から外れる、session.py:993-996。スキルの制限があるときだけ意味を持つ)。Claude "
            "Code の ToolSearch は MCP の定義の読み込みを遅らせる用途。右の例は Lab 1.4 D"
            "(docs/tutorial/data/skills/residual-report。x-r4-keywords に「残差」も入っている"
            "ので日本語の依頼でも一致する)。MCP は別のスライドに分けた(2026-11-01 のレビュー)。",
        ),
    )


# ── MCP: from your request to a server's tool ───────────────────────────────

MCP_REQUEST = """{"role": "user", "content": "Which of my jobs ran last?"}
"tools": [..., {"type": "function", "function": {
   "name": "mcp.platform-tools.list_jobs",
   "description": "[first-class MCP] List recent platform jobs",
   "parameters": {"properties": {"limit": {"type": "integer",
     "default": 20}}}}}]"""

MCP_REPLY = """{"role": "assistant", "tool_calls": [{"id": "call_1",
  "function": {"name": "mcp.platform-tools.list_jobs",
   "arguments": "{\\"limit\\": 5}"}}]}"""

MCP_FORWARD = """POST <gateway>/gw/mcp/invoke
{"tool": "list_jobs", "arguments": {"limit": 5}}"""

MCP_RESULT = """{"role": "tool", "tool_call_id": "call_1",
 "content": "[TOOL_OUTPUT_START] …\\n[{\\"job_id\\": …}]\\n[TOOL_OUTPUT_END]"}"""

_MCP_PANELS = (
    (
        MCP_REQUEST,
        Inches(1.5),
        (
            "①  harness → model: the server's tools sit in the tool list like any other",
            "①  ハーネス → モデル:サーバのツールも、ほかのツールと同じく一覧に入る",
        ),
    ),
    (
        MCP_REPLY,
        Inches(1.08),
        (
            "②  model → harness: an ordinary tool call; the name says which server",
            "②  モデル → ハーネス:普通のツール呼び出し。名前でサーバが分かる",
        ),
    ),
    (
        MCP_FORWARD,
        Inches(0.82),
        (
            "③  gate first, then the harness FORWARDS it: UMRS finds the server, the server runs it",
            "③  まず許可ゲート。次にハーネスが転送:UMRS がサーバを探し、サーバが実行する",
        ),
    ),
    (
        MCP_RESULT,
        Inches(0.82),
        (
            "④  result → model: text again, marked “written by the server, not by you”",
            "④  結果 → モデル:これもテキスト。「サーバが書いたもの」という目印が付く",
        ),
    ),
)

_MCP_CARDS = (
    (
        ("What MCP adds", "MCP が加えるもの"),
        [
            (
                "A **standard plug**: any MCP server's tools reach the model as ordinary tools.",
                "**標準のプラグ**:どの MCP サーバのツールも、普通のツールとしてモデルに届く。",
            ),
            (
                "The model's side does not change — it still only writes a tool call (slide on "
                "tool calling).",
                "モデル側は何も変わらない。ツール呼び出しを書くだけ(ツール呼び出しのスライド)。",
            ),
            (
                "Only step ③ differs: the call leaves the harness for the server.",
                "違うのは ③ だけ:呼び出しがサーバへ出ていく。",
            ),
        ],
        "general",
        Inches(1.85),
    ),
    (
        ("R4's settings", "R4 の設定"),
        [
            (
                "Servers come from the UMRS registry, not a local config file: a project's "
                "servers come with it. Loaded when the session starts.",
                "サーバは手元の設定ではなく UMRS の登録簿から(プロジェクトのサーバがそのまま使える)。",
            ),
            (
                "Try it in Lab 1.4 E: r4 mcp hatch install platform-tools, then /mcp tools.",
                "Lab 1.4 E で試す:r4 mcp hatch install platform-tools → /mcp tools。",
            ),
        ],
        "r4tuned",
        Inches(1.5),
    ),
    (
        ("Why the result is fenced", "結果を区別する理由"),
        (
            "The reply was written by **someone else's program**, not by you or R4, and may "
            "hide instructions aimed at the model (prompt injection). So it is marked as data, "
            "and a call that copies from it asks first (gate row 2).",
            "返答を書いたのは、自分でも R4 でもない**他者のプログラム**。モデルへの指示が紛れ込んで"
            "いるかもしれない(プロンプトインジェクション)。だからデータとして目印を付け、そこから"
            "写した呼び出しは先に確認する(許可ゲートの 2 行目)。",
        ),
        "r4",
        Inches(1.6),
    ),
)


def mcp_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("MCP: from your request to a server's tool", "MCP:依頼からサーバのツールまで"),
        KICK_A,
    )
    harness_map.mini(slide, "mcp")
    deck.mark("mcp")
    y = BODY_TOP
    for code, h, title in _MCP_PANELS:
        code_panel(slide, MARGIN, y, _PANEL_W, h, code, size=10, title=title)
        y += h + Inches(0.1)
    _tab(slide, MARGIN, BODY_TOP, _PANEL_W, "general")
    x = MARGIN + _PANEL_W + Inches(0.25)
    w = CONTENT_W - _PANEL_W - Inches(0.25)
    y = BODY_TOP
    for title, body, kind, h in _MCP_CARDS:
        _box(slide, x, y, w, h, title, body, kind)
        y += h + Inches(0.15)
    notes(
        slide,
        (
            "5 min. Review 2026-11-01: show how a request reaches an MCP server with the same "
            "numbered structure as the tool-calling slide. GENERAL PATTERN: MCP is the shared "
            "standard (Claude Code .mcp.json, OpenAI Agents SDK MCPServerStdio, opencode, "
            "langchain-mcp-adapters); in all of them the harness lists the server's tools to "
            "the model and forwards the call. R4: build_first_class_mcp_tools registers each "
            "tool as mcp.<server>.<tool> with description '[first-class MCP] …' and the "
            "server's input schema as parameters (companion/mcp_first_class.py:126-157); they "
            "go to the model in the OpenAI tools array (agent.py:620-634). The handler checks "
            "required fields, then client.mcp.invoke(name, **args) (mcp_first_class.py:98-110) "
            "POSTs {tool, arguments} to <gateway>/gw/mcp/invoke (r4/src/r4/mcp.py:518-555, "
            "332-338); nginx authenticates and rewrites to /umrs/mcp/ "
            "(gateway/nginx/conf.d/server.conf:396-411), and UMRS calls the registered server. "
            "The reply becomes MCPToolResult; its to_message() content is injection-scanned and "
            "wrapped in [TOOL_OUTPUT_START] … [TOOL_OUTPUT_END] (mcp.py:147-205, "
            "agent/untrusted_scanner.py:705-740); errors come back as 'Tool error: …' "
            "(mcp_result_text, mcp_first_class.py:84-95). The result taints as MCP output "
            "(r4_contracts/taint.py), so a later call that copies ≥ 24 chars of it asks. Fixed "
            "2026-10-30: the handler read result.is_error/.content, fields "
            "MCPToolResult lacks, so every agent call failed. platform-tools "
            "(mcp-servers/hatch-registry/packages/platform-tools/server.py) is read-only: "
            "list_models, list_endpoints, list_jobs, endpoint_health. The job list in ④ is "
            "abbreviated.",
            "5 分。2026-11-01 のレビュー:依頼が MCP サーバに届く流れを、ツール呼び出しのスライドと"
            "同じ番号つきの構成で示す。一般的な仕組み:MCP は共通の標準(Claude Code の .mcp.json、"
            "OpenAI Agents SDK の MCPServerStdio、opencode、langchain-mcp-adapters)。どれもハーネスが"
            "サーバのツールをモデルに見せ、呼び出しを転送する。R4:build_first_class_mcp_tools が"
            "各ツールを mcp.<server>.<tool> として登録し、説明は「[first-class MCP] …」、引数の定義は"
            "サーバの入力スキーマそのまま(companion/mcp_first_class.py:126-157)。OpenAI 形式の "
            "tools 配列でモデルへ送る(agent.py:620-634)。ハンドラは必須の引数を確かめてから "
            "client.mcp.invoke(name, **args) を呼び(mcp_first_class.py:98-110)、{tool, arguments} "
            "を <gateway>/gw/mcp/invoke に POST する(r4/src/r4/mcp.py:518-555、332-338)。nginx が"
            "認証して /umrs/mcp/ に書き換え(gateway/nginx/conf.d/server.conf:396-411)、UMRS が"
            "登録済みのサーバを呼ぶ。応答は MCPToolResult になり、to_message() の内容は紛れ込んだ"
            "指示の検査を受け、[TOOL_OUTPUT_START] … [TOOL_OUTPUT_END] で囲まれる(mcp.py:147-205、"
            "agent/untrusted_scanner.py:705-740)。エラーは「Tool error: …」(mcp_result_text、"
            "mcp_first_class.py:84-95)。結果は MCP の出力として汚染に記録され、そこから 24 字以上"
            "写した呼び出しは確認になる。2026-10-30 に修正:以前は "
            "MCPToolResult にない result.is_error/.content を読んでいたため、エージェントからの"
            "呼び出しはすべて失敗していた。platform-tools(mcp-servers/hatch-registry/packages/"
            "platform-tools/server.py)は読み取り専用:list_models・list_endpoints・list_jobs・"
            "endpoint_health。④ のジョブ一覧は省略している。",
        ),
    )


# ── Why RAG? (introduction to the knowledge block) ──────────────────────────

_RAG_INTRO_STEPS = [
    (
        "**Search** your documents for the passages that match the question.",
        "質問に合う箇所を、自分の文書から**検索**する。",
    ),
    (
        "**Insert** them into the context, each with a label: [S1], [S2] …",
        "見つけた箇所にラベル([S1]、[S2] …)を付けて、コンテキストに**入れる**。",
    ),
    (
        "**Answer** from them and cite the label — then you check that source.",
        "モデルはそれをもとに**答え**、ラベルで出典を示す。最後は自分で出典を確かめる。",
    ),
]


def _book_box(slide, x, y, w, title, lines, fill) -> None:
    rect(slide, x, y, w, Inches(1.9), fill=fill, rounded=True)
    text_box(
        slide,
        x + Inches(0.2),
        y + Inches(0.12),
        w - Inches(0.4),
        Inches(0.35),
        title,
        Style(15, INK, True),
    )
    bullets(
        slide,
        x + Inches(0.2),
        y + Inches(0.55),
        w - Inches(0.4),
        Inches(1.3),
        lines,
        Style(12, INK),
        gap=4,
    )


def rag_intro_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck,
        ("Why RAG? Hand the model your documents", "なぜ RAG か:モデルに自分の文書を渡す"),
        KICK_K,
    )
    harness_map.mini(slide, "knowledge")
    sfx = "en" if lang() == "en" else "ja"
    plain = probe["answer_28"]["lab_" + sfx]["greedy"]
    rag = probe["answer_28"]["lab_rag_" + sfx]["greedy"]
    w = (CONTENT_W - Inches(4.6) - Inches(0.3)) // 2
    _book_box(
        slide,
        MARGIN,
        BODY_TOP,
        w,
        ("Closed book: the model alone", "教科書なし:モデルだけ"),
        [
            (
                "It knows only its training text — never your observatory's model.",
                "知っているのは学習した文章だけ。自分の観測所のモデルは知らない。",
            ),
            (
                f"Asked for the Moho depth, its most likely answer is “{plain}”.",
                f"モホ面の深さを聞くと、最も確率の高い答えは「{plain}」。",
            ),
            (
                "Retraining is slow and costly, and still cites nothing.",
                "学習し直すのは時間も費用もかかり、しかも出典は示せない。",
            ),
        ],
        TINT,
    )
    _book_box(
        slide,
        MARGIN + w + Inches(0.3),
        BODY_TOP,
        w,
        ("Open book: RAG", "教科書あり:RAG"),
        [
            (
                "The harness looks the answer up in **your** documents first.",
                "ハーネスがまず**自分の**文書で調べる。",
            ),
            (
                f"With the passage in front of the question, the same model answers “{rag}”.",
                f"その箇所を質問の前に置くと、同じモデルが「{rag}」と答える。",
            ),
            (
                "Upload a new document and the next answer can use it.",
                "新しい文書を入れれば、次の回答から使える。",
            ),
        ],
        TINT_AMBER,
    )
    x = MARGIN + 2 * w + Inches(0.6)
    sw = CONTENT_W - 2 * w - Inches(0.6)
    text_box(
        slide,
        x,
        BODY_TOP - Inches(0.05),
        sw,
        Inches(0.4),
        ("RAG in three steps", "RAG の 3 段階"),
        Style(16, OCEAN, True),
    )
    _steps(slide, x, BODY_TOP + Inches(0.4), sw, _RAG_INTRO_STEPS, h=Inches(0.78), size=12)
    _tab(slide, x, BODY_TOP + Inches(0.4), sw, "general")
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.0),
        2 * w + Inches(0.3),
        Inches(0.5),
        (
            "**RAG = Retrieval-Augmented Generation** (検索拡張生成): the model is unchanged; "
            "**what it reads** changes.",
            "**RAG = Retrieval-Augmented Generation(検索拡張生成)**:モデルは変えず、**モデルが"
            "読むもの**を変える。",
        ),
        Style(13, INK),
    )
    _box(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.55),
        2 * w + Inches(0.3),
        Inches(1.32),
        ("The labels [S1], [S2] …: who does what", "ラベル [S1]・[S2] …:誰が何をするか"),
        (
            "**R4 writes** a label in front of each passage and asks the model to cite it. "
            "**The model** only sees text: it copies the label next to what it used. **R4 "
            "checks** the answer — a label it never retrieved gets a warning — and shows the "
            "sources in the console.",
            "**R4 が**各箇所の前にラベルを書き、引用するよう指示する。**モデルは**文字列として"
            "読むだけで、使った箇所のラベルを回答に写す。**R4 が**回答を確かめ(検索していない"
            "ラベルには警告)、コンソールに出典を表示する。",
        ),
        "r4tuned",
        size=11,
    )
    flow(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.05),
        CONTENT_W,
        Inches(1.1),
        [
            (
                ("your question", "質問"),
                ("“Moho depth under the Kii Channel?”", "「紀伊水道のモホ面の深さは?」"),
            ),
            (("search", "検索"), ("your documents, on UMRS", "自分の文書(UMRS)")),
            (("context", "コンテキスト"), ("[S1] passage + the question", "[S1] の記述+質問")),
            (("model", "モデル"), ("reads both, writes the answer", "両方を読んで答える")),
            (("answer", "回答"), (f"“{rag} [S1]” — you check S1", f"「{rag} [S1]」→ S1 を確認")),
        ],
        size=12,
    )
    notes(
        slide,
        (
            "3 min. Review 2026-11-01: an introduction before the embeddings. LABELS (review "
            "2026-11-05: who reads [S1]?): the harness numbers each passage "
            "(r4-contracts context_pack.py:137-140; search_docs results the same, "
            "companion/tools.py:2441-2470) and adds 'cite passages as [S1]'; the model just "
            "copies labels as text; after every answer citation_check runs without a model "
            "call (companion/citation_check.py; R4_RAG_VERIFY annotate by default): a footer "
            "lists the sources and a Check: line flags labels that were never retrieved and "
            "numbers not found in the cited passage; strict mode turns an unsupported answer "
            "into NOT ENOUGH INFO; there is no retry. The console shows citation chips "
            "(packages/r4-tui-v3 chat.tsx). Bracketed numbers like [1] are a common convention "
            "in chat-with-documents tools; the exact [S1] spelling is R4's (ADR-093 D-4). The "
            "two answers "
            "are measured: Qwen2.5-VL 3B, plain text, the most likely token at each step "
            "(next_token_probe.json, answer_28.*.greedy): the question alone → "
            f"“{probe['answer_28']['lab_en']['greedy']}”; the reference-model passage in front → "
            f"“{probe['answer_28']['lab_rag_en']['greedy']}” (the Japanese prompts give the same "
            "two answers). GENERAL PATTERN: retrieval-augmented generation (Lewis et al., 2020) "
            "is how OpenAI file_search, LangChain retrievers and most document chat tools work; "
            "Claude Code has no document index and greps files instead. In R4 the search runs "
            "on UMRS and the agent reaches it through the search_docs tool "
            "(companion/tools.py:2556-2562), or the optional router puts passages in front of "
            "the question (retrieval_router.py).",
            "3 分。2026-11-01 のレビュー:埋め込みの前に導入を置く。ラベル(2026-11-05 のレビュー:「[S1] は"
            "誰が解釈するのか」):ハーネスが各箇所に番号を付け(r4-contracts の context_pack.py:137-140。"
            "search_docs の結果も同じ、companion/tools.py:2441-2470)、「cite passages as [S1]」と"
            "指示する。モデルはラベルを文字列として写すだけ。回答のたびに citation_check がモデルを"
            "使わずに動き(companion/citation_check.py。R4_RAG_VERIFY は既定で annotate)、末尾に"
            "出典を並べ、検索していないラベルや引用箇所にない数値を「Check:」の行で指摘する。strict "
            "では根拠のない回答を NOT ENOUGH INFO にする。やり直しはしない。コンソールは引用のチップを"
            "表示する(packages/r4-tui-v3 の chat.tsx)。[1] のような角括弧の番号は文書チャットで"
            "よく使われる書き方で、[S1] という綴りは R4 のもの(ADR-093 D-4)。2 つの答えは実測:Qwen2.5-VL "
            "3B、プレーンテキスト、各段で最も確率の高いトークン(next_token_probe.json の "
            "answer_28.*.greedy)。質問だけ → "
            f"「{probe['answer_28']['lab_ja']['greedy']}」、参照モデルの記述を前に置く → "
            f"「{probe['answer_28']['lab_rag_ja']['greedy']}」(英語のプロンプトでも同じ 2 つの答え)。"
            "一般的な仕組み:検索拡張生成(Lewis ほか、2020)は OpenAI の file_search、LangChain の"
            "リトリーバ、多くの文書チャットの仕組み。Claude Code は文書の索引を持たず grep で探す。"
            "R4 では検索を UMRS が行い、エージェントは search_docs ツールで呼ぶ"
            "(companion/tools.py:2556-2562)。任意のルータが質問の前に箇所を置くこともできる"
            "(retrieval_router.py)。",
        ),
    )


# ── 20 · Embeddings ──────────────────────────────────────────────────────────


_WORDS = (
    ("man", "man(男性)", 0.0, 0.9),
    ("woman", "woman(女性)", 1.35, 0.9),
    ("king", "king(王)", 0.45, 0.25),
    ("queen", "queen(女王)", 1.8, 0.25),
)
_WORD_ARROWS = (
    ("man", "king", OCEAN),
    ("woman", "queen", OCEAN),
    ("man", "woman", AMBER),
    ("king", "queen", AMBER),
)


def _vec_arrow(slide, x1, y1, x2, y2, color) -> None:
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    conn.line.color.rgb = color
    conn.line.width = Pt(2.25)
    ln = conn.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")


def _analogy_sketch(slide, oy) -> None:
    """The classic word-vector example: king − man + woman ≈ queen."""
    ox = MARGIN + Inches(0.3)
    pos = {k: (ox + Inches(x), oy + Inches(y)) for k, _, x, y in _WORDS}
    for a, b, color in _WORD_ARROWS:
        _vec_arrow(slide, *pos[a], *pos[b], color)
    for key, ja, _, _ in _WORDS:
        x, y = pos[key]
        dot = slide.shapes.add_shape(
            9, x - Inches(0.05), y - Inches(0.05), Inches(0.1), Inches(0.1)
        )
        dot.fill.solid()
        dot.fill.fore_color.rgb = NAVY
        dot.line.fill.background()
        dot.shadow.inherit = False
        text_box(
            slide,
            x - Inches(0.3),
            y + Inches(0.06),
            Inches(1.3),
            Inches(0.25),
            (key, ja),
            Style(10, INK, True),
        )
    text_box(
        slide,
        MARGIN + Inches(2.85),
        oy - Inches(0.1),
        Inches(4.95),
        Inches(1.35),
        (
            "**king − man + woman ≈ queen**\nA familiar example: in a word-vector space the step "
            "man → woman points the same way as king → queen (amber), and man → king the same "
            "as woman → queen (teal). Directions carry meaning. Classic word2vec result "
            "(Mikolov et al., 2013); e5 does the same for whole passages.",
            "**king − man + woman ≈ queen(王 − 男性 + 女性 ≈ 女王)**\n身近な例:単語のベクトル"
            "空間では、男性 → 女性 の向き(オレンジ)が 王 → 女王 と同じで、男性 → 王 の向き"
            "(青緑)が 女性 → 女王 と同じ。向きが意味を表す。word2vec の古典的な結果"
            "(Mikolov ほか、2013)。e5 は同じことを文章単位で行う。",
        ),
        Style(11, INK),
    )


_ROLE = {
    "answer": ("the answer", "答え", OCEAN),
    "paraphrase": ("other words", "言い換え", OCEAN),
    "same topic": ("same topic", "同じ話題", MUTED),
    "unrelated": ("unrelated", "無関係", ALERT),
}
_SENT_ROW = Inches(0.5)
_SCORE_W = Inches(2.0)


def _sentence_row(slide, y, row) -> None:
    en, ja, color = _ROLE[row["role"]]
    chip(
        slide,
        MARGIN,
        y + Inches(0.08),
        (en, ja),
        fill=color,
        color=WHITE,
        w=Inches(1.15),
        h=Inches(0.3),
        size=10,
    )
    text_box(
        slide,
        MARGIN + Inches(1.25),
        y,
        Inches(4.3),
        _SENT_ROW,
        (row["text"], row["text"]),
        Style(11, INK),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    bx = MARGIN + Inches(5.65)
    rect(slide, bx, y + Inches(0.12), int(_SCORE_W * row["score"]), Inches(0.24), fill=color)
    text_box(
        slide,
        bx + int(_SCORE_W * row["score"]) + Inches(0.05),
        y,
        Inches(0.6),
        _SENT_ROW,
        f"{row['score']:.2f}",
        Style(11, INK, True),
        anchor=MSO_ANCHOR.MIDDLE,
    )


def _sentence_demo(slide, demo: dict) -> None:
    """One question, four sentences, measured cosine similarity (review 2026-11-05)."""
    rect(slide, MARGIN, BODY_TOP, Inches(7.8), Inches(0.5), fill=NAVY, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.25),
        BODY_TOP,
        Inches(7.4),
        Inches(0.5),
        (f"question: {demo['question']}", f"質問:{demo['question']}"),
        Style(13, WHITE, True),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    y = BODY_TOP + Inches(0.62)
    for row in demo["sentences"]:
        _sentence_row(slide, y, row)
        y += _SENT_ROW + Inches(0.06)
    name = demo["model"].split("/")[1]
    text_box(
        slide,
        MARGIN,
        y,
        Inches(7.8),
        Inches(0.5),
        (
            f"Cosine similarity of each sentence to the question ({name}, the stack's embedder).",
            f"各文と質問のコサイン類似度({name}。スタック既定の e5-base-v2 は英語専用で、この"
            "日本語の例では無関係な文を 2 位にしてしまう)。",
        ),
        Style(10, MUTED, italic=True),
    )


def embeddings_slide(deck: Deck, emb: dict) -> None:
    slide = content(
        deck,
        (
            "Embeddings (ベクトル埋め込み): meaning as a vector",
            "埋め込み:意味をベクトル(数値列)に変換",
        ),
        KICK_K,
    )
    harness_map.mini(slide, "knowledge")
    entry = emb["e5-base-v2:" + ("en" if lang() == "en" else "ja")]
    demo = emb["sentence_demo"]["e5-base-v2:en" if lang() == "en" else "multilingual-e5-base:ja"]
    _sentence_demo(slide, demo)
    _analogy_sketch(slide, BODY_TOP + Inches(3.75))
    x = MARGIN + Inches(8.2)
    w = CONTENT_W - Inches(8.2)
    name, dim = entry["model"].split("/")[1], entry["dimension"]
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(4.7),
        ("How it works", "仕組み"),
        body_size=13,
        items=[
            (
                f"An embedding model ({name}) turns any text into a **vector** of {dim} numbers "
                "here. The length is model-specific — 384, 1024, 3072 … exist elsewhere; "
                "longer can distinguish finer but costs more.",
                f"埋め込みモデル({name})が文章を**ベクトル**(このスタックでは {dim} 個の数値)に"
                "変える。この数値の個数を次元数と呼び、モデルごとに決まる(384・1024・3072 など)。"
                "次元が多いほど細かく"
                "区別できるが、計算量も増える。",
            ),
            (
                "Similar meaning → vectors pointing the same way (cosine near 1); the "
                "direction between two words carries meaning too (bottom left).",
                "意味が近い → ベクトルの向きが近い(コサイン類似度が 1 に近い)。2 つの語の間の"
                "向きにも意味がある(左下の例)。",
            ),
            (
                "Measured (left): the answer ranks first; the same fact in other words comes "
                "next, with no shared keywords; the same topic but another question is lower; "
                "the unrelated sentence is last.",
                "実測(左):答えの文が 1 位。言い換えた文は、同じ単語がなくても 2 位。同じ話題でも"
                "別の問いの文はそれより下、無関係な文が最下位。",
            ),
            (
                "Scores bunch together — the **ranking** matters, not the value.",
                "スコアの差は小さい。大事なのは値ではなく**順位**。",
            ),
        ],
    )
    _tab(slide, x, BODY_TOP, w, "general")
    notes(
        slide,
        (
            "GENERAL PATTERN: embeddings are the standard retrieval building block (OpenAI "
            "file_search, LangChain vector stores); Claude Code has no built-in embedding "
            "search and greps instead. Measured with the stack's embedder e5-base-v2 (768-d; "
            "'query:'/'passage:' prefixes as UMRS applies them, shared/utils.py:36-63) over "
            "the knowledge pack, one chunk per section; pgvector ranks by cosine distance "
            "(dense.py:157-165). The Japanese pack with this English-only embedder still ranks "
            "the right sections high, but less sharply; multilingual-e5-base does better "
            "(embedding_probe.json): in Japanese the pick rules miss the top 6. Bar labels "
            "are document · section; 'intro' = a chunk without a section heading. Review "
            "2026-11-01 asked for a familiar example: king − man + woman ≈ queen is the "
            "word2vec analogy (Mikolov, Yih & Zweig, NAACL 2013); it is shown as the classic "
            "illustration, not measured with e5. Review 2026-11-05: the knowledge-pack ranking "
            "(section labels such as 'Review notes · What we found') did not read as an "
            "example, so the slide now scores one short question against four single "
            "sentences (embedding_probe.py SENTENCE_DEMO): e5-base-v2 on the English set "
            "0.81 / 0.76 / 0.68 / 0.64. On the Japanese set the English-only e5-base-v2 puts "
            "the unrelated lunch-break sentence second (0.82 / 0.79 / 0.79 / 0.80), so the JA "
            "slide shows multilingual-e5-base (0.86 / 0.76 / 0.75 / 0.70). The pack ranking "
            "stays in embedding_probe.json and handbook 04.",
            "一般的な仕組み:埋め込みは検索の標準的な部品(OpenAI の file_search、LangChain の"
            "ベクトルストア)。Claude Code には埋め込み検索がなく、grep で探す。スタックの埋め込み"
            "モデル e5-base-v2(768 次元。UMRS と同じく 'query:'/'passage:' の接頭辞、"
            "shared/utils.py:36-63)で、ナレッジパックを節ごとに測定。pgvector はコサイン距離で"
            "順位付け(dense.py:157-165)。英語専用のこのモデルでも日本語パックの関係する節が"
            "上位に来るが、差は小さく、読み取り規則は 6 位以内に入らない。multilingual-e5-base "
            "の方が良い(embedding_probe.json)。棒のラベルは「文書 · 節」。「冒頭」は節見出しの"
            "ないチャンク。2026-11-01 のレビューで身近な例を求められた:king − man + woman ≈ queen は "
            "word2vec の類推(Mikolov・Yih・Zweig、NAACL 2013)。古典的な例として示し、e5 では"
            "測っていない。2026-11-05 のレビュー:ナレッジパックの順位(「確認メモ・確認したこと」"
            "のような節の名前)は例として分かりにくかったので、短い質問 1 つと 4 つの文の類似度に"
            "変えた(embedding_probe.py の SENTENCE_DEMO)。英語の例は e5-base-v2 で 0.81 / 0.76 / "
            "0.68 / 0.64。日本語の例では英語専用の e5-base-v2 が無関係な昼休みの文を 2 位にする"
            "(0.82 / 0.79 / 0.79 / 0.80)ので、日本語版は multilingual-e5-base の値(0.86 / 0.76 / "
            "0.75 / 0.70)を示す。パックの順位は embedding_probe.json とハンドブック 04 に残す。",
        ),
    )


# ── 21 · RAG end to end ──────────────────────────────────────────────────────

_RAG_STEPS = (
    (("chunk", "分割"), ("256-token pieces", "文書を 256 トークンずつの断片(チャンク)に切る")),
    (("embed", "埋め込み"), ("e5-base-v2 → 768 numbers", "e5-base-v2 → 768 個の数値")),
    (("store", "保存"), ("the pgvector DB, your disk", "pgvector(ベクトル対応 DB)、自分の PC")),
    (("search", "検索"), ("cosine + keyword, fused", "コサイン+キーワードを融合")),
    (
        ("pack", "まとめる"),
        ("3–4 passages, citation labels [S1] …", "3〜4 チャンクに引用ラベル [S1] … を付ける"),
    ),
)

_RETRIEVAL = [
    (
        "**Dense** — by meaning (the vectors above).",
        "**意味検索(dense)** — 上で見たベクトルによる検索。",
    ),
    (
        "**Lexical** — exact words/IDs (BM25-style); catches codes like PQ-3.",
        "**語句検索(lexical)** — キーワード・ID の一致(BM25 などの古典的手法)。PQ-3 のような"
        "記号に強い。",
    ),
    (
        "**Hybrid** — both fused by rank (RRF); this stack's default.",
        "**ハイブリッド** — 両方の結果を順位で融合(RRF:各順位の逆数を足し合わせる)。このスタック"
        "の既定。",
    ),
    (
        "**Re-ranking** — a second model re-scores the top candidates: finer but slower; off "
        "by default in UMRS.",
        "**再順位付け(re-ranking)** — 上位候補を別のモデルで採点し直す。精度は上がるが遅い。"
        "UMRS では既定でオフ。",
    ),
]

_ROUTER = [
    (
        "**Automatic** — R4_RAG_ROUTER=on: passages go in front of your question on most "
        "turns (not slash commands, short acks or code-intent messages), capped by the "
        "reading model's profile: 4 passages / 2,000 tokens (3 / 1,200 for llama3.2:3b), "
        "never more than 12 % of the context budget; injection-flagged passages are dropped.",
        "**自動経路** — R4_RAG_ROUTER=on:ほとんどのターン(スラッシュコマンド・短い相づち・"
        "コード作業の依頼を除く)で、質問の前に [S1] などのラベル付きチャンクが置かれる。上限は"
        "モデルの規模で決まり、4 チャンク・2,000 トークン(llama3.2:3b は 3・1,200)、かつ"
        "コンテキスト予算の 12 % 以内。インジェクションと判定されたチャンクは除く。",
    ),
    (
        "**Agent-decided** — R4_RAG_ROUTER=off (the default): the agent calls the "
        "search_docs tool when it judges documents are needed; the result returns as an "
        "ordinary tool result in the loop (the usual way: Claude Code, OpenAI file_search).",
        "**エージェント判断** — R4_RAG_ROUTER=off(既定):エージェントが必要と判断したとき "
        "search_docs をツールとして呼び、結果は通常のツール結果としてループに戻る(一般的な"
        "やり方。Claude Code や OpenAI の file_search と同じ)。",
    ),
    (
        "Retrieval itself uses no chat model; ingest (upload → chunk → embed → index) runs "
        "as a UMRS job and survives interruptions.",
        "検索自体にチャットモデルは使わない。取り込み(アップロード → 分割 → 埋め込み → 索引)は"
        " UMRS のジョブで、中断しても状態が残る。",
    ),
]


def _rag_steps(slide) -> None:
    w = (CONTENT_W - Inches(0.25) * 4) / 5
    for i, (name, sub) in enumerate(_RAG_STEPS):
        x = MARGIN + i * (w + Inches(0.25))
        rect(slide, x, BODY_TOP, w, Inches(1.4), fill=TINT, rounded=True)
        badge(
            slide, x + Inches(0.15), BODY_TOP + Inches(0.15), str(i + 1), size=Inches(0.42), font=13
        )
        text_box(
            slide,
            x + Inches(0.65),
            BODY_TOP + Inches(0.2),
            w - Inches(0.75),
            Inches(0.4),
            name,
            Style(16, INK, True),
        )
        text_box(
            slide,
            x + Inches(0.15),
            BODY_TOP + Inches(0.72),
            w - Inches(0.3),
            Inches(0.6),
            sub,
            Style(12, MUTED),
        )
    scope_tag(slide, MARGIN + Inches(0.2), BODY_TOP - SCOPE_H // 2, "r4tuned")


def rag_pipeline_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "RAG end to end: from your PDF to the context",
            "RAG の全体像:文書からモデルのコンテキストまで",
        ),
        KICK_K,
    )
    harness_map.mini(slide, "knowledge")
    _rag_steps(slide)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(1.52),
        CONTENT_W,
        Inches(0.55),
        (
            "**RAG = Retrieval-Augmented Generation** (検索拡張生成): your documents are "
            "searched (by the harness or by the agent, see right); the best passages, labelled "
            "**[S1], [S2] …** (written by R4, copied by the model, checked by R4) so the answer can cite them, join the "
            "model's context as its material.",
            "**RAG = Retrieval-Augmented Generation(検索拡張生成)**:文書を検索し(ハーネスが"
            "自動で、またはエージェントが判断して。右を参照)、関連度の高いチャンクに引用ラベル [S1] [S2] …"
            "(R4 が書き、モデルが写し、R4 が確かめる)を付けてモデルのコンテキストに入れる"
            "仕組み。ラベルがあるので回答は根拠を示せる。",
        ),
        Style(13, INK),
    )
    y, h = BODY_TOP + Inches(2.12), Inches(3.08)
    card(
        slide,
        MARGIN,
        y,
        Inches(5.6),
        h,
        ("Retrieval is not one method", "検索は 1 通りではない"),
        body_size=12,
        items=_RETRIEVAL,
    )
    _tab(slide, MARGIN, y, Inches(5.6), "general")
    x = MARGIN + Inches(5.85)
    w = CONTENT_W - Inches(5.85)
    card(
        slide,
        x,
        y,
        w,
        h,
        ("How it enters the agent loop (R4 side)", "エージェントループへの入り方(R4 側)"),
        fill=TINT_AMBER,
        body_size=12,
        items=_ROUTER,
    )
    _tab(slide, x, y, w, "r4")
    notes(
        slide,
        (
            "GENERAL · R4 SETTINGS (pipeline): the chunk → embed → store → search → pack "
            "pattern is generic RAG (LangChain's EnsembleRetriever also fuses with RRF; OpenAI "
            "file_search is hosted hybrid search); the numbers and the server-side placement "
            "are R4's. UMRS: chunk scheme fixed-v0, 256-token windows (chunking.py, "
            "shared/utils.py:239-253); encode → dense cosine → lexical/identifier → RRF fusion "
            "(k=60) → hydrate → (rerank, off by default) → cut (retrieval/pipeline.py:3-21). "
            "GENERAL PATTERN (retrieval methods): dense, lexical, hybrid and re-ranking are "
            "textbook IR. R4-SPECIFIC (router and project knowledge): retrieval_router.py:8-16 "
            "(off by default; 'auto' routes only the listed size classes, default small); pack "
            "= min(profile.pack_tokens, 12 % of the budget) (context_budget.py:25, 73-80; "
            "r4-contracts/src/r4_contracts/models.py:144-180). qwen2.5:7b, qwen2.5:14b and "
            "qwen3:30b are 'medium' (4 / 2,000); llama3.2:3b is 'small' (3 / 1,200). On the "
            "32k stack int(25,395 × 0.12) = 3,047 is the ceiling, and it never binds. "
            "LangChain two-step chains always retrieve; Claude Code and OpenAI file_search "
            "let the model decide; a per-turn router with both lanes is R4's. Ingest is a "
            "durable UMRS job (knowledge_tools.py:139-230), with no harness-level equivalent "
            "elsewhere. CITATION LABELS (review 2026-11-01: 'is [S1] OpenAI syntax?'): no — "
            "[S1] is R4's own format (ADR-093 D-4). The pack is <passages note=…>, entries "
            "'[S1] source › section' + text, then the instruction 'cite passages as [S1]' and "
            "the question (r4-contracts/src/r4_contracts/context_pack.py:7-17, 64-67, 136-140, "
            "175-189); numbering continues across a session (evidence_ledger.py:73-87) and "
            "citation_check.py checks the answer's [S#]. The OpenAI Chat Completions API has "
            "no citation convention (the passages are plain text in a message); OpenAI's "
            "Responses API file_search returns file_citation annotations, and Anthropic's API "
            "has document citations — both are structured fields, not inline labels.",
            "一般的な仕組み・R4 の設定(流れ):分割 → 埋め込み → 保存 → 検索 → まとめる、は一般的"
            "な RAG の形(LangChain の EnsembleRetriever も RRF で融合し、OpenAI の file_search "
            "はホスティングされたハイブリッド検索)。数値とサーバ側に置く構成が R4 のもの。UMRS:"
            "分割方式 fixed-v0、256 トークン(chunking.py、shared/utils.py:239-253)。符号化 → "
            "密ベクトル(コサイン)→ 語句・ID → RRF 融合(k=60)→ 本文の取得 →(再順位付け、既定オフ)→ "
            "打ち切り(retrieval/pipeline.py:3-21)。一般的な仕組み(検索方式):密・語句・"
            "ハイブリッド・再順位付けは情報検索の基本。R4 独自(ルータとプロジェクトのナレッジ):"
            "retrieval_router.py:8-16(既定オフ。auto は指定した規模のモデルだけ、既定は "
            "small)。まとめる量 = min(profile.pack_tokens、予算の 12 %)(context_budget.py:25、"
            "73-80、r4-contracts/src/r4_contracts/models.py:144-180)。qwen2.5:7b・"
            "qwen2.5:14b・qwen3:30b は medium(4 チャンク・2,000 トークン)、llama3.2:3b は "
            "small(3・1,200)。32k のスタックでは int(25,395 × 0.12) = 3,047 が上限で、実際には"
            "効かない。LangChain の 2 段階のチェーンは常に検索し、Claude Code や OpenAI の "
            "file_search はモデルが判断する。両方の経路をターンごとに選ぶルータは R4 独自。取り"
            "込みは中断しても残る UMRS のジョブ(knowledge_tools.py:139-230)。引用ラベル"
            "(2026-11-01 のレビュー:「[S1] は OpenAI の書式か」):違う。[S1] は R4 独自の書式"
            "(ADR-093 D-4)。まとめは <passages note=…> の中に「[S1] 出典 › 節」と本文を並べ、"
            "最後に「cite passages as [S1]」という指示と質問を置く(r4-contracts/src/r4_contracts/"
            "context_pack.py:7-17、64-67、136-140、175-189)。番号はセッションを通して続き"
            "(evidence_ledger.py:73-87)、citation_check.py が回答の [S#] を確かめる。OpenAI の "
            "Chat Completions API に引用の決まりはない(箇所はメッセージ内の普通の文字列)。OpenAI "
            "の Responses API の file_search は file_citation という注釈を返し、Anthropic の API "
            "には文書の引用機能がある。どちらも構造化された項目で、本文中のラベルではない。",
        ),
    )


# ── 22 · Does RAG help? ──────────────────────────────────────────────────────


_PROMPT_W = Inches(7.8)


def _fed_prompts(slide, probe: dict, sfx: str) -> int:
    """The two prompts exactly as fed; returns the y below them."""
    plain = probe["prompts"]["lab_" + sfx]["text"]
    with_rag = probe["prompts"]["lab_rag_" + sfx]["text"]
    passage = with_rag[: len(with_rag) - len(plain)].strip()
    y = BODY_TOP
    for head, body, h in (
        (("A · question only", "A · 質問だけ"), plain.strip() + " …", Inches(0.78)),
        (
            (
                "B · the retrieved passage, a blank line, the same question",
                "B · 検索で見つかった記述、空行、同じ質問",
            ),
            f"**{passage}**\n\n{plain.strip()} …",
            Inches(1.62),
        ),
    ):
        rect(slide, MARGIN, y, _PROMPT_W, h, fill=NAVY, rounded=True)
        text_box(
            slide,
            MARGIN + Inches(0.2),
            y + Inches(0.08),
            _PROMPT_W - Inches(0.4),
            Inches(0.25),
            head,
            Style(11, AMBER, True),
        )
        text_box(
            slide,
            MARGIN + Inches(0.2),
            y + Inches(0.33),
            _PROMPT_W - Inches(0.4),
            h - Inches(0.38),
            (body, body),
            Style(10, WHITE, mono=True),
        )
        y += h + Inches(0.08)
    return int(y)


def _answer_card(slide, x, w, ans: dict, sfx: str) -> None:
    a, b = ans["lab_" + sfx], ans["lab_rag_" + sfx]
    pa, pb = a["0.4"], b["0.4"]
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.75),
        ("Two outcomes: “28 km” or not", "「28 km」か、それ以外か"),
        body_size=13,
        items=[
            (
                f"Most likely answer — A: “{a['greedy']}”; B: “{b['greedy']}”.",
                f"最も確率の高い答え — A:「{a['greedy']}」、B:「{b['greedy']}」。",
            ),
            (
                f"Chance of exactly “28 km” at R4's temperature 0.4 — A: {pa:.1%}; B: {pb:.0%}.",
                f"R4 の温度 0.4 で「28 km」と答える確率 — A:{pa:.1%}、B:{pb:.0%}。",
            ),
            (
                "Same model, same question: only the context changed.",
                "モデルも質問も同じ。変えたのはコンテキストだけ。",
            ),
        ],
    )
    _tab(slide, x, BODY_TOP, w, "general")


def rag_effect_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck, ("Does RAG help? The same model, measured", "RAG は効くか? 同じモデルで測定"), KICK_K
    )
    harness_map.mini(slide, "knowledge")
    sfx = "en" if lang() == "en" else "ja"
    ans = probe["answer_28"]
    y = _fed_prompts(slide, probe, sfx)
    names = (
        ("A · question only", "B · with the passage")
        if lang() == "en"
        else ("A · 質問だけ", "B · 記述あり")
    )
    pa, pb = ans["lab_" + sfx]["0.4"], ans["lab_rag_" + sfx]["0.4"]
    bar_chart(
        slide,
        MARGIN,
        y,
        _PROMPT_W,
        BODY_TOP + Inches(5.25) - y,
        [tr(("answers “28 km”", "「28 km」と答える")), tr(("anything else", "それ以外の答え"))],
        {names[0]: [pa, 1 - pa], names[1]: [pb, 1 - pb]},
        [MUTED, OCEAN],
        horizontal=True,
        max_value=1.0,
    )
    x = MARGIN + _PROMPT_W + Inches(0.4)
    w = CONTENT_W - _PROMPT_W - Inches(0.4)
    _answer_card(slide, x, w, ans, sfx)
    rect(slide, x, BODY_TOP + Inches(2.95), w, Inches(2.3), fill=TINT_AMBER, rounded=True)
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(3.1),
        w - Inches(0.4),
        Inches(2.05),
        (
            "RAG **moves the odds the right way — it does not guarantee correctness** "
            f"({1 - pb:.0%} of answers are still something else). The cited source ([S1] on "
            "the previous slide) is the passage the answer leans on: read it yourself before "
            "trusting the answer.",
            "RAG は確率を正しい方へ動かすが、**正解は保証しない**(それでも "
            f"{1 - pb:.0%} は別の答え)。引用元([S1] など)は回答の根拠となる記述。答えを信じる前に"
            "原文を自分で読む。",
        ),
        Style(13, INK),
    )
    notes(
        slide,
        (
            "GENERAL PATTERN: putting evidence in the context shifts the next-token "
            "probabilities for any model behind any harness. Review 2026-11-01: two outcomes "
            "only, and the exact text fed to the model on the slide. Measured on Qwen2.5-VL 3B "
            "with plain-text prompts — no chat template, no R4 instructions — exactly the two "
            "texts shown (next_token_probe.py PROMPTS lab_* / lab_rag_*; the slide adds the "
            "trailing '…'). 'Exactly 28 km' = p('2') × p('8' | '2') × p(no further digit | "
            "'28'), at temperature 0.4, R4's default (companion/agent.py _initial_temperature); "
            "next_token_probe.json answer_28 also records temperature 1 "
            f"(EN {ans['lab_en']['1.0']:.1%} → {ans['lab_rag_en']['1.0']:.1%}, JA "
            f"{ans['lab_ja']['1.0']:.1%} → {ans['lab_rag_ja']['1.0']:.1%}). 'Most likely "
            "answer' = the top token at every step, without the repetition penalty Hugging "
            "Face's generate() would add. The Japanese prompt gains less (a model trained "
            "mostly on English); the 7B chat model in the lab, with R4's instructions and the "
            "skill, does better — Lab 1.4 lets attendees measure it themselves.",
            "一般的な仕組み:根拠をコンテキストに入れると、どのハーネスのどのモデルでも次の"
            "トークンの確率が動く。2026-11-01 のレビュー:答えを 2 通りに絞り、モデルに渡した文章を"
            "そのままスライドに載せた。Qwen2.5-VL 3B、プレーンテキスト(チャットテンプレートも R4 の"
            "指示もなし)で、表示の 2 つの文章をそのまま入力(next_token_probe.py の PROMPTS の "
            "lab_* と lab_rag_*。末尾の「…」はスライドでの追加)。「ちょうど 28 km」= p(「2」)× "
            "p(「8」|「2」)× p(「28」の後に数字が続かない)、温度は R4 の既定の 0.4"
            "(companion/agent.py の _initial_temperature)。next_token_probe.json の answer_28 には"
            "温度 1 の値もある(英語 "
            f"{ans['lab_en']['1.0']:.1%} → {ans['lab_rag_en']['1.0']:.1%}、日本語 "
            f"{ans['lab_ja']['1.0']:.1%} → {ans['lab_rag_ja']['1.0']:.1%})。「最も確率の高い答え」"
            "= 各段で確率最大のトークンを選んだもの(Hugging Face の generate() が加える繰り返し"
            "ペナルティはなし)。日本語の方が効果は小さい(主に英語で学習したモデル)。ラボの 7B "
            "チャットモデルに R4 の指示とスキルを加えると、さらに良くなる。Lab 1.4 で各自が測る。",
        ),
    )


# ── Can RAG guarantee the answer? (review 2026-11-05) ──────────────────────

_CHECKS = [
    (
        "**Check the source.** Open [S1] and compare. R4 checks every answer and flags a "
        "label it never retrieved or a number missing from the cited passage.",
        "**出典を確かめる。**[S1] を開いて比べる。R4 は回答ごとに確かめ、検索していない"
        "ラベルや、引用箇所にない数値を指摘する。",
    ),
    (
        "**Make it checkable.** Ask for the quoted sentence next to the number.",
        "**確かめられる形で答えさせる。**数値の隣に、根拠の文をそのまま引用させる。",
    ),
    (
        "**Compute, don't recall.** Let the agent read the value from the file with a tool, "
        "or test the result.",
        "**思い出させず、計算させる。**値はツールでファイルから読ませる。結果はテストで確かめる。",
    ),
    (
        "**Raise the odds:** better retrieval, a larger model, a low temperature for lookups.",
        "**確率を上げる:**検索の改善、大きなモデル、調べものには低い温度。",
    ),
]


def guarantee_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck,
        ("Can RAG guarantee the right answer?", "RAG で正解を保証できるか?"),
        KICK_K,
    )
    harness_map.mini(slide, "knowledge")
    sfx = "en" if lang() == "en" else "ja"
    ans = probe["answer_28"]
    keys = ("lab_", "lab_rag_", "lab_rag_strict_")
    cats = [
        tr(("A · question", "A · 質問だけ")),
        tr(("B · + passage", "B · +記述")),
        tr(("C · + passage + “answer only from it”", "C · +記述+「資料だけで答えよ」")),
    ]
    bar_chart(
        slide,
        MARGIN,
        BODY_TOP + Inches(0.35),
        Inches(6.6),
        Inches(3.0),
        cats,
        {
            tr(("P(exactly “28 km”), temperature 0.4", "「28 km」の確率(温度 0.4)")): [
                ans[k + sfx]["0.4"] for k in keys
            ]
        },
        [OCEAN],
        number_format="0.0%",
        horizontal=False,
        max_value=1.0,
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP - Inches(0.05),
        Inches(6.6),
        Inches(0.4),
        ("No. Measured on the same 3B model:", "できない。同じ 3B モデルで測ると:"),
        Style(15, INK, True),
    )
    greedy = " / ".join(ans[k + sfx]["greedy"] for k in keys)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.45),
        Inches(6.6),
        Inches(1.7),
        (
            f"Most likely answer, A / B / C: {greedy}. Telling the model to answer only from "
            "the passage (C) did **not** help — a prompt alone cannot make an answer right. "
            "Checking can.",
            f"最も確率の高い答え(A / B / C):{greedy}。「資料だけで答え、数値はそのまま写せ」"
            "と指示しても(C)**良くならなかった**(日本語ではむしろ悪化)。指示だけでは正しくできない。"
            "正しさは確かめることで得る。",
        ),
        Style(13, INK),
    )
    x = MARGIN + Inches(6.95)
    w = CONTENT_W - Inches(6.95)
    text_box(
        slide,
        x,
        BODY_TOP - Inches(0.05),
        w,
        Inches(0.4),
        ("What makes answers reliable", "答えを信頼できるものにするには"),
        Style(15, OCEAN, True),
    )
    _steps(slide, x, BODY_TOP + Inches(0.4), w, _CHECKS, h=Inches(1.05), size=12, gap=Inches(0.12))
    _tab(slide, x, BODY_TOP + Inches(0.4), w, "general")
    notes(
        slide,
        (
            "3 min. Review 2026-11-05: 'then how do we guarantee a right answer with RAG?' "
            "Measured (next_token_probe.py, prompts lab_* / lab_rag_* / lab_rag_strict_*; "
            "plain text, Qwen2.5-VL 3B, temperature 0.4): EN "
            f"{ans['lab_en']['0.4']:.1%} / {ans['lab_rag_en']['0.4']:.0%} / "
            f"{ans['lab_rag_strict_en']['0.4']:.0%}, JA {ans['lab_ja']['0.4']:.1%} / "
            f"{ans['lab_rag_ja']['0.4']:.0%} / {ans['lab_rag_strict_ja']['0.4']:.0%}; most "
            "likely answer EN 100 / 28 / 28 km, JA 100 / 28 / 18 km — the instruction made the "
            "small model pick the other number in the passage. Nothing guarantees an answer; "
            "reliability comes from checking. R4's part: citation_check after every answer "
            "(companion/citation_check.py; R4_RAG_VERIFY=annotate by default, 'strict' turns "
            "an unsupported answer into NOT ENOUGH INFO), the sources shown in the console, "
            "python_exec to read values from files, tests (Lab 1.3). Retrieval quality: "
            "hybrid search, reranking (off by default), a multilingual embedder for Japanese. "
            "Lab 1.4 lets attendees measure the 7B chat model with R4's own pack.",
            "3 分。2026-11-05 のレビュー:「では RAG で正解を保証するにはどうするか」。実測"
            "(next_token_probe.py の lab_* / lab_rag_* / lab_rag_strict_*。プレーンテキスト、"
            "Qwen2.5-VL 3B、温度 0.4):英語 "
            f"{ans['lab_en']['0.4']:.1%} / {ans['lab_rag_en']['0.4']:.0%} / "
            f"{ans['lab_rag_strict_en']['0.4']:.0%}、日本語 {ans['lab_ja']['0.4']:.1%} / "
            f"{ans['lab_rag_ja']['0.4']:.0%} / {ans['lab_rag_strict_ja']['0.4']:.0%}。最も確率の"
            "高い答えは英語 100 / 28 / 28 km、日本語 100 / 28 / 18 km。指示によって小さなモデルが"
            "記述の中の別の数値を選んだ。保証できるものはなく、信頼は確かめることで得る。R4 の役割:"
            "回答ごとの citation_check(companion/citation_check.py。R4_RAG_VERIFY は既定で "
            "annotate、strict では根拠のない回答を NOT ENOUGH INFO にする)、コンソールでの出典の"
            "表示、ファイルから値を読む python_exec、テスト(Lab 1.3)。検索の質:ハイブリッド検索、"
            "再順位付け(既定オフ)、日本語には多言語の埋め込みモデル。Lab 1.4 で 7B のチャット"
            "モデルを R4 のパックで各自測る。",
        ),
    )


# ── 23 · Recap ───────────────────────────────────────────────────────────────

_RECAP_ROWS = [
    [
        ("Layer", "層"),
        ("Does", "すること"),
        ("Cannot", "できないこと"),
        ("You control it with", "調整方法"),
        ("Code (r4/src/r4/…)", "コード(r4/src/r4/…)"),
    ],
    [
        ("Model", "モデル"),
        ("scores next tokens", "次のトークンに確率を付ける"),
        (
            "know your lab; run code; remember",
            "研究室の事情を知る・コードを実行する・会話をまたいで覚える",
        ),
        ("choice of model, temperature", "モデルの選択・温度"),
        "client.py",
    ],
    [
        ("Agent loop", "エージェントループ"),
        ("turns tool requests into actions", "ツールの要求を実際の操作に変え、繰り返す"),
        ("judge if the result is right", "結果の正しさを判断する"),
        ("your goal + acceptance check", "目標+合格基準"),
        "companion/agent.py — _run_agentic_turn; companion/tools.py",
    ],
    [
        ("Harness", "ハーネス"),
        (
            "builds context, gates tools, manages memory",
            "コンテキストを組み立て、ツールを制御し、記憶を管理",
        ),
        ("make a weak model strong", "弱いモデルを強くする"),
        ("R4.md, skills, permissions", "R4.md・スキル・許可"),
        "companion/context.py, context_budget.py, session_turns.py, briefing.py, "
        "permissions.py, permission_rules.py, user_hooks.py, skills.py, tool_search.py, "
        "mcp_first_class.py (MCP)",
    ],
    [
        ("Knowledge", "ナレッジ"),
        ("puts the right passage in front of the model", "正しいチャンクをモデルの前に置く"),
        ("guarantee the model uses it", "モデルがそれを使うと保証する"),
        ("your documents, RAG on/off", "自分の文書・RAG のオン/オフ"),
        "companion/retrieval_router.py; umrs/…/knowledge/retrieval/",
    ],
]

_RECAP_SCOPE = (
    (
        "general",
        (
            "tool calling, the loop, compaction, the permission gate, skills, MCP, RAG: every "
            "mainstream harness has them",
            "ツール呼び出し・ループ・要約(コンパクション)・許可ゲート・スキル・MCP・RAG:"
            "主要なハーネスはどれも持つ",
        ),
    ),
    (
        "r4tuned",
        (
            "R4's numbers: a 1,000-round roof, loop guards (4 failures / 3 same results), "
            "nudges ≤ 2 / ≤ 3, compaction at 80 % keeping 6, output caps 32k / 40k / 120k, "
            "keys y / a / n",
            "R4 の数値:上限 1,000 ラウンド、空回り対策(失敗 4 回・同じ結果 3 回)、促しは最大 "
            "2 回と 3 回、80 % で要約して直近 6 件を残す、出力の上限 3.2 万・4 万・12 万字、"
            "承認キー y・a・n",
        ),
    ),
    (
        "r4",
        (
            "25-round checkpoint, dropping null arguments, checks on foreign arguments and "
            "credentials, keyword skills, the RAG router (R4_RAG_ROUTER), knowledge on UMRS",
            "25 ラウンドごとの確認、null 引数の除去、外部由来の引数と認証情報の確認、キーワード"
            "によるスキル、RAG の自動経路(R4_RAG_ROUTER)、UMRS 上のナレッジ",
        ),
    ),
)


_RECAP_COLS = [1.4, 2.3, 2.3, 2.1, 4.0]


def _table_h(rows, col_w, total_w, size: float) -> int:
    """Estimated rendered height of a table whose rows grow to fit their text."""
    widths = [total_w * c / sum(col_w) - Inches(0.2) for c in col_w]
    height = 0
    for row in rows:
        cells = [
            _text_h(cell if isinstance(cell, tuple) else (cell, cell), size, cw)
            for cell, cw in zip(row, widths, strict=True)
        ]
        height += max(max(cells) + Inches(0.12), Inches(0.5))
    return int(height)


def _recap_scope(slide, y) -> int:
    """One line per scope kind: the pill, then what falls under it; returns the y below."""
    col = max(_pill_w(kind) for kind, _ in _RECAP_SCOPE) + Inches(0.12)
    for kind, text in _RECAP_SCOPE:
        h = max(_text_h(text, 12, CONTENT_W - col), SCOPE_H)
        scope_tag(slide, MARGIN, y, kind)
        text_box(slide, MARGIN + col, y + Inches(0.02), CONTENT_W - col, h, text, Style(12, INK))
        y += h + Inches(0.1)
    return int(y)


def recap_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Recap: who does what — and where it lives", "まとめ:誰が何をし、コードのどこにあるか"),
        KICK_A,
    )
    deck.mark("recap")
    table(slide, MARGIN, BODY_TOP, CONTENT_W, _RECAP_ROWS, _RECAP_COLS, size=12, row_h=Inches(0.5))
    y = BODY_TOP + _table_h(_RECAP_ROWS, _RECAP_COLS, CONTENT_W, 12) + Inches(0.2)
    y = _recap_scope(slide, y)
    text_box(
        slide,
        MARGIN,
        y + Inches(0.05),
        CONTENT_W,
        Inches(0.6),
        (
            "Everything you saw is ordinary, readable Python. A harness is engineering, not "
            "magic — you can read it and change it. Full map: handbook 04, §16.",
            "すべて読める普通の Python コード。ハーネスは魔法ではなく技術で、自分で読んで変えられる"
            "(対応表:ハンドブック 04 の §16)。",
        ),
        Style(14, OCEAN, True),
    )
    notes(
        slide,
        (
            "2 min. The pills sum up the split: general patterns you will meet in Claude Code, "
            "the OpenAI Agents SDK, opencode or LangChain; R4's own numbers; and what only R4 "
            "does. Homework for computational-science students: read _run_agentic_turn and "
            "PermissionManager.check(). R4's own answer to self-grading: unattended runs "
            "(R4_AUTONOMOUS=1) get one completion review against the task (agent.py:3454-3510, "
            "H4; Claude Code does this with a Stop hook); build contracts (a written definition "
            "of done) get a fresh-context "
            "reviewer with read/run-only tools and a hard-threshold rubric (sprint_review.py:"
            "1-32, G15). Interactively, that reviewer is you: the acceptance check in your "
            "prompt (slide 42).",
            "2 分。下の 3 つの印がこの区分のまとめ:Claude Code・OpenAI Agents SDK・opencode・"
            "LangChain でも出会う"
            "一般的な仕組み、R4 の数値、R4 だけの仕組み。計算科学の学生への宿題:"
            "_run_agentic_turn と PermissionManager.check() を読む。モデルが自分の結果を正しく採点"
            "できない問題への R4 の対策:"
            "無人実行(R4_AUTONOMOUS=1)では終了前にタスクに照らした見直しを 1 回行う"
            "(agent.py:3454-3510、H4。Claude Code では Stop フックで同じことをする)。ビルド契約"
            "(作業の完了条件を書いた取り決め)"
            "では、読み取りと実行だけのツールを持つ新しいコンテキストのレビュー役が、合否の基準"
            "(ルーブリック)で採点する(sprint_review.py:1-32、G15)。対話で使うときのレビュー役は"
            "自分。依頼文の合格基準がそれにあたる(スライド 42)。",
        ),
    )


def add(deck: Deck, probe: dict, emb: dict) -> None:
    tool_call_slide(deck)
    loop_code_slide(deck)
    overflow_slide(deck)
    guardrail_slide(deck)
    slides_part1_overview.transition_slide(deck, "extend")
    skills_slide(deck)
    mcp_slide(deck)
    slides_part1_overview.transition_slide(deck, "knowledge")
    rag_intro_slide(deck, probe)
    embeddings_slide(deck, emb)
    rag_pipeline_slide(deck)
    rag_effect_slide(deck, probe)
    guarantee_slide(deck, probe)
    recap_slide(deck)
