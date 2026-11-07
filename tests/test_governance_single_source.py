"""Governance rules live in one home (hardening plan H-11.4; 2026-10-18).

Four rules were copied by hand into up to eleven files and drifted apart.
docs/README.md §2 names one home for each; this test fails when a rule's text
appears again, verbatim or near-verbatim (case, whitespace, ``*`` and `````
ignored), in a normative document outside its home:

- the gate command (``bash scripts/ci-lint-local.sh``): home
  docs/onboarding/developer.md (setup, gate and suite commands); AGENTS.md
  rule 1 states it as a rule that bites;
- the ownership table: home CONSTITUTION.md §1.3;
- the wave contract: home AGENTS.md (short form with the Report line);
  CONSTITUTION §9.2 stays the binding text until ADR-094 is accepted;
- the no-credentials rule: home AGENTS.md rule 6.

A short pointer is allowed: a line that names the home file (for example
"gate: ``bash scripts/ci-lint-local.sh --files …`` ([developer.md §3](…))" or
"… (AGENTS.md rule 1)"; the agent personas cite AGENTS.md since 2026-10-18).
Records are not scanned: plans and the ledger, ADRs, history, archives, the
retired loop docs, dated verification reports, STATE.md and benchmark results
say what was true or run on their date (``EXCLUDED_PREFIXES``).

Copies that existed on 2026-10-18 are listed in ``ALLOWLIST`` with a reason.
Most are pending pointer edits (deferred to the files' owners); the list may
only shrink: an entry whose file no longer holds the rule fails
``test_allowlist_has_no_stale_entries``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Dated records and retired trees: their text is what held on their date.
EXCLUDED_PREFIXES: tuple[str, ...] = (
    "docs/development/",  # plans and the append-only wave ledger
    "docs/adr/",  # decision records (ADR-094 quotes the constitution it amends)
    "docs/history/",
    "docs/loop-engineering/",  # retired 2026-09-14 (docs/README.md §4)
    "docs/verification/",  # dated verification reports
    "benchmarks/",
    "STATE.md",  # escalation and release-track records
)
EXCLUDED_PARTS: tuple[str, ...] = ("/archive/",)


@dataclass(frozen=True)
class Rule:
    name: str
    homes: frozenset[str]
    # Names whose presence on a line makes that line a pointer to the home.
    pointer_names: tuple[str, ...]


GATE = Rule(
    "gate-command",
    frozenset({"docs/onboarding/developer.md", "AGENTS.md"}),
    ("onboarding/developer.md", "developer.md", "AGENTS.md"),
)
OWNERSHIP = Rule("ownership-table", frozenset({"CONSTITUTION.md"}), ())
WAVE = Rule(
    "wave-contract",
    frozenset({"AGENTS.md", "CONSTITUTION.md"}),
    ("AGENTS.md", "CONSTITUTION"),
)
CREDENTIALS = Rule("no-credentials", frozenset({"AGENTS.md"}), ("AGENTS.md",))
RULES: tuple[Rule, ...] = (GATE, OWNERSHIP, WAVE, CREDENTIALS)

_PENDING = "pending pointer edit (H-11.4, deferred to the file's owner, 2026-10-18)"
_INSTRUCTION = "path-scoped instruction file (docs/README.md §2: shorter form of a rule)"
_ADR094 = "binding constitution text until ADR-094 (Proposed, M-H5) moves procedures out"

ALLOWLIST: dict[str, dict[str, str]] = {
    GATE.name: {
        "CONSTITUTION.md": _ADR094 + " (§7 gate commands)",
        "R4.md": "R4 product briefing (r4/src/r4/briefing.py) must name its own gate",
        "docs/AGENT_TASK_QUEUE.md": "pre-reset task queue (2026-07-11); each spec carries its commands",
        "docs/autonomous-science/DEVELOPMENT_PLAN.md": "dated plan (2026-06-21) outside docs/development",
        ".github/instructions/ci-harness.instructions.md": "documents the gate script itself",
        ".github/instructions/development.instructions.md": _INSTRUCTION,
        ".github/instructions/hpc-deployment.instructions.md": _INSTRUCTION,
        ".github/instructions/mutation-safety.instructions.md": _INSTRUCTION,
        ".github/instructions/umrs-branch-separation.instructions.md": _INSTRUCTION
        + " (--umrs-changed)",
        ".github/pull_request_template.md": "PR checklist names the command the author ran",
    },
    OWNERSHIP.name: {},
    WAVE.name: {},
    CREDENTIALS.name: {
        "CONSTITUTION.md": _ADR094 + " (§8 item 9, §9.3 rule 4)",
        "R4.md": "R4 product briefing (r4/src/r4/briefing.py) must state it without AGENTS.md",
    },
}

_GATE_RE = re.compile(r"bash\s+scripts/ci-lint-local\.sh")
_CREDENTIAL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"never\s+(?:handle|perform)\s+credential"),
    re.compile(r"credential\s+operations"),
)
_MARKUP_RE = re.compile(r"[*`]")


def normalise(text: str) -> str:
    """Case, whitespace and emphasis/code markup do not make a copy distinct."""
    text = _MARKUP_RE.sub("", text).replace("->", "→")
    return re.sub(r"\s+", " ", text).strip().lower()


# --- needles derived from the homes --------------------------------------------


def _section(text: str, heading: str) -> str:
    _, found, rest = text.partition(heading)
    assert found, f"heading {heading!r} not found"
    return re.split(r"\n#{1,3} ", rest, maxsplit=1)[0]


def ownership_needles(constitution: str) -> list[str]:
    """The first 60 normalised characters of every cell of the §1.3 table body."""
    section = _section(constitution, "### 1.3 Ownership")
    rows = [ln for ln in section.splitlines() if ln.startswith("| **")]
    cells = [cell for row in rows for cell in row.strip("|").split("|")[1:]]
    return [normalise(cell)[:60] for cell in cells if len(normalise(cell)) >= 30]


def wave_needles(agents: str) -> list[str]:
    """The agent line of the wave contract block (its distinctive step chain)."""
    block = _section(agents, "## Wave contract")
    steps = [ln.split(":", 1)[1] for ln in block.splitlines() if ln.startswith("Agent:")]
    assert steps, "AGENTS.md wave contract has no 'Agent:' line"
    return [normalise(steps[0])]


def credentials_needles(agents: str) -> list[str]:
    match = re.search(r"^6\. (.+)$", _section(agents, "## Rules that bite"), re.MULTILINE)
    assert match, "AGENTS.md rule 6 not found"
    return [normalise(match.group(1))]


def _needles() -> dict[str, list[str]]:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    constitution = (ROOT / "CONSTITUTION.md").read_text(encoding="utf-8")
    return {
        OWNERSHIP.name: ownership_needles(constitution),
        WAVE.name: wave_needles(agents),
        CREDENTIALS.name: credentials_needles(agents),
    }


# --- matching -----------------------------------------------------------------


def line_hits(rule: Rule, line: str, needles: Iterable[str]) -> bool:
    """True when ``line`` restates ``rule`` (before the pointer allowance)."""
    norm = normalise(line)
    if rule is GATE:
        return bool(_GATE_RE.search(norm))
    if rule is CREDENTIALS and any(p.search(norm) for p in _CREDENTIAL_RES):
        return True
    return any(needle and needle in norm for needle in needles)


def is_pointer(rule: Rule, line: str) -> bool:
    return any(name in line for name in rule.pointer_names)


def duplicate_lines(rule: Rule, text: str, needles: Iterable[str]) -> list[int]:
    """Line numbers that restate ``rule`` without citing its home."""
    needles = list(needles)
    return [
        number
        for number, line in enumerate(text.splitlines(), 1)
        if line_hits(rule, line, needles) and not is_pointer(rule, line)
    ]


def is_normative(path: str) -> bool:
    if path.startswith(EXCLUDED_PREFIXES):
        return False
    return not any(part in f"/{path}" for part in EXCLUDED_PARTS)


def _tracked_markdown() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.splitlines() if is_normative(p) and (ROOT / p).is_file()]


def find_copies(rule: Rule, files: dict[str, str], needles: Iterable[str]) -> dict[str, list[int]]:
    """Files outside the rule's homes that restate it: path -> line numbers."""
    needles = list(needles)
    copies = {
        path: duplicate_lines(rule, text, needles)
        for path, text in files.items()
        if path not in rule.homes
    }
    return {path: lines for path, lines in copies.items() if lines}


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    return {p: (ROOT / p).read_text(encoding="utf-8") for p in _tracked_markdown()}


@pytest.fixture(scope="module")
def needles() -> dict[str, list[str]]:
    return _needles()


def _copies(rule: Rule, corpus: dict[str, str], needles: dict[str, list[str]]):
    return find_copies(rule, corpus, needles.get(rule.name, []))


# --- live checks ----------------------------------------------------------------


@pytest.mark.parametrize("rule", RULES, ids=[r.name for r in RULES])
def test_rule_has_a_single_home(rule, corpus, needles):
    copies = _copies(rule, corpus, needles)
    new = {p: lines for p, lines in copies.items() if p not in ALLOWLIST[rule.name]}
    assert not new, (
        f"{rule.name} restated outside {sorted(rule.homes)}; replace with a one-line "
        f"pointer to the home: {new}"
    )


@pytest.mark.parametrize("rule", RULES, ids=[r.name for r in RULES])
def test_allowlist_has_no_stale_entries(rule, corpus, needles):
    copies = _copies(rule, corpus, needles)
    stale = sorted(set(ALLOWLIST[rule.name]) - set(copies))
    assert not stale, f"{rule.name}: remove these ALLOWLIST entries (fixed): {stale}"


def test_allowlist_entries_carry_reasons():
    empty = [(r, p) for r, entries in ALLOWLIST.items() for p, why in entries.items() if not why]
    assert not empty


@pytest.mark.parametrize("rule", RULES, ids=[r.name for r in RULES])
def test_homes_hold_their_rule(rule, needles):
    for home in rule.homes:
        text = (ROOT / home).read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines() if line_hits(rule, ln, needles.get(rule.name, []))]
        assert hits, f"{home} no longer states {rule.name}; update this test's homes"


def test_ownership_needles_cover_every_owner(needles):
    assert len(needles[OWNERSHIP.name]) >= 8  # four owners x (Owns, Never)


# --- unit tests on fixtures -------------------------------------------------------


def test_normalise_ignores_case_whitespace_and_markup():
    assert normalise("**Never**  handle\n `credentials`") == "never handle credentials"


def test_gate_copy_is_found_and_a_pointer_is_not():
    text = (
        "Run `bash  scripts/ci-lint-local.sh --files x`.\n"
        "Gate: see developer.md for `bash scripts/ci-lint-local.sh`.\n"
        "Run `bash scripts/ci-lint-local.sh --files x` (AGENTS.md rule 1).\n"
    )
    assert duplicate_lines(GATE, text, []) == [1]


def test_ownership_near_verbatim_copy_is_found():
    constitution = (
        "### 1.3 Ownership (from the design)\n\n| Owner | Owns | Never |\n|---|---|---|\n"
        "| **UMRS** | accounts and quotas; the client–server transport and its identity | "
        "exposes a bucket, a database or a scheduler to a client |\n\n### 1.4 Next\n"
    )
    found = ownership_needles(constitution)
    copy = "UMRS owns **accounts and quotas;   the client–server transport** and its identity."
    files = {"CONSTITUTION.md": constitution, "docs/x.md": copy, "docs/y.md": "unrelated"}
    assert find_copies(OWNERSHIP, files, found) == {"docs/x.md": [1]}


def test_wave_contract_copy_is_found():
    agents = (
        "## Wave contract\n```\nAgent:      Verify → Decide (or escalate) → Implement → Gate\n```\n"
    )
    found = wave_needles(agents)
    copy = "agents do: verify -> decide (or escalate) -> implement -> gate, then report"
    assert duplicate_lines(WAVE, copy, found) == [1]


def test_credentials_paraphrase_is_found():
    found = credentials_needles("## Rules that bite\n6. **Never** handle credentials: ask.\n")
    assert duplicate_lines(CREDENTIALS, "Agents never perform credential ops.", found) == [1]
    assert duplicate_lines(CREDENTIALS, "Never handle credentials: ask.", found) == [1]
    assert (
        duplicate_lines(CREDENTIALS, "Rule 6 of AGENTS.md: never handle credentials.", found) == []
    )


def test_records_are_not_normative():
    assert not is_normative("docs/development/wave-ledger.md")
    assert not is_normative("docs/adr/094-x.md")
    assert not is_normative("r4/docs/archive/old.md")
    assert is_normative("CONTRIBUTING.md")
    assert is_normative(".github/instructions/x.instructions.md")
