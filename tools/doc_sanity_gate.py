"""Doc sanity gate — catches model-output collapse in load-bearing prose.

Model-generated docs sometimes degenerate into mixed-script word salad
("trust boats", "sessionment scoped", CJK+Latin spliced words, invented
tokens) that classic prose lints don't catch. This exists because one
platform team's review already named these patterns twice in the same doc
family. Cheap, deterministic, trie-of-verified-bugs vocabulary, not NLP.

Rules (each is a Finding):
- non-utf8: file cannot be decoded as UTF-8 → always fail (data lost).
- mixed-script-token: a single whitespace-token contains BOTH CJK
  (hiragana / katakana / CJK-unified) and ASCII letters — the signature of
  half-translated generation ("一つのコマンドrough") EXCEPT when the token
  is a known-accepted boundary form (url, path w/ CJK dir, quoted code
  fence lines, front-matter keys). Baseline list supports a ratchet.
- low-dictionary-density: serialized guard against full-line nonsense —
  if >60% of words in a sentence are 12+ chars, no vowel in any of them,
  AND not code tokens, flag. Loosely matches pure-noise paragraphs.

Modes:
- STRICT (docs/adr/**): any finding fails.
- BASELINE (docs/**): findings recorded in tools/doc_sanity_baseline.json
  are allowed ONLY up to their recorded fingerprint; new ones fail. The
  ratchet phrase is always real, never a decoration:
  decrease-of-findings is unencumbered; increases are hard fails.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CJK_RANGES = (
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3400, 0x4DBF),  # CJK Ext A
    (0x4E00, 0x9FFF),  # CJK Unified
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)
ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
CODE_FENCE_RE = re.compile(r"^\s*```")
FRONT_MATTER_RE = re.compile(r"^\s*[A-Za-z0-9_-]+:\s")
URL_RE = re.compile(r"https?://\S+")
WORD_SPLIT_RE = re.compile(r"\s+")

VOWELS = set("aeiouAEIOU")
LOW_DENSITY_TOKEN_MIN = 12
LOW_DENSITY_SENTENCE_RATIO = 0.6
LOW_DENSITY_MIN_TOKENS = 4
# Characters stripped before a token is judged word-like: markdown, list and
# table punctuation plus the separators identifiers are built from.
WORDISH_STRIP_RE = re.compile(r"[^0-9A-Za-z]+")

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "tools" / "doc_sanity_baseline.json"
# Scanned when no paths are given (the local gate and CI pass the same set;
# 2026-10-18, hardening plan H-11.8).
DEFAULT_SCOPES: tuple[str, ...] = ("docs/adr", "docs/development")

# Tokens that legitimately touch CJK and latin together (uri segments,
# code fences, path prefixes like snippets/(CJK dir)). Matched as regexes.
_TOKEN_ALLOW_PATTERNS = (
    re.compile(r"https?://\S+$"),
    re.compile(r"^\s*[/~/.\w.-]+/[^\s]+$"),  # unix path containing CJK subdir
    re.compile(r"^\s*$"),
    re.compile(
        r"^[A-Za-z0-9._-]+$"
    ),  # pure ascii — passes trivially; filter early (\w would also match CJK, silently swallowing real mixed tokens)
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(start <= cp <= end for start, end in CJK_RANGES)


def _has_cjk(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text)


def _has_ascii_letter(text: str) -> bool:
    return bool(ASCII_LETTER_RE.search(text))


@dataclass(frozen=True)
class Finding:
    rule: str
    file: str
    line: int
    token: str
    fingerprint: str

    def to_json(self) -> dict:
        return {
            "rule": self.rule,
            "file": self.file,
            "line": self.line,
            "token": self.token,
            "fingerprint": self.fingerprint,
        }


def _is_wordish(token: str) -> bool:
    """True when a token is a candidate *dictionary word*, not an identifier.

    The low-dictionary-density rule hunts generation-collapse gibberish —
    runs of pronounceable-looking but vowel-less words. Dates
    (``2026-09-07;``), gap ids (``G-P4-1``), commit/job handles
    (``umrs@5db8ac12``, ``job-14d4eed4c54c``) and paths
    (``~/.config/cni/net.d``) are vowel-less by construction and carry
    digits or separators; counting them made every dated status table
    look like noise (the ADR index tripped on four ISO dates alone).
    A token qualifies only when what remains after stripping punctuation
    is purely alphabetic — identifiers keep their digits and drop out.
    """
    stripped = WORDISH_STRIP_RE.sub("", token)
    return bool(stripped) and stripped.isalpha() and not _has_cjk(stripped)


def _fingerprint(rule: str, file: str, line: int, token: str) -> str:
    key = f"{rule}|{file}|{line}|{token}"
    import hashlib

    return hashlib.sha256(key.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def _non_utf8_finding(path: Path, exc: UnicodeDecodeError) -> Finding:
    """The one finding a file that cannot be decoded can carry."""
    return Finding(
        "non-utf8",
        str(path),
        0,
        f"{exc.reason} at {exc.start}",
        _fingerprint("non-utf8", str(path), 0, str(exc.start)),
    )


def _is_skippable_line(line: str) -> bool:
    """Front matter and pure-URL lines carry no prose to judge."""
    if FRONT_MATTER_RE.match(line):
        return True
    return bool(URL_RE.search(line)) and not _has_cjk(line)


def _token_is_allowed(token: str) -> bool:
    """Known-good CJK+latin boundary forms (uri segments, paths, keys)."""
    return any(re.match(pat, token) or pat.fullmatch(token) for pat in _TOKEN_ALLOW_PATTERNS)


def _mixed_script_findings(path: Path, number: int, line: str) -> list[Finding]:
    """Half-translated generation: one token holding CJK *and* ascii letters."""
    findings: list[Finding] = []
    for token in WORD_SPLIT_RE.split(line):
        if not token or token.startswith("http"):
            continue
        if not (_has_cjk(token) and _has_ascii_letter(token)):
            continue
        if _token_is_allowed(token):
            continue
        findings.append(
            Finding(
                "mixed-script-token",
                str(path),
                number,
                token[:80],
                _fingerprint("mixed-script-token", str(path), number, token[:80]),
            )
        )
    return findings


def _low_density_finding(path: Path, number: int, line: str) -> Finding | None:
    """Lines built mostly of long vowel-less *words* (generation collapse).

    The pre-fix rule counted only tokens ≥12 chars AND anything with a
    vowel made the rule unreachable — as written it could never fire (see
    2808f5b1's own fixture). Now: count word-like non-CJK tokens ≥6 chars
    with no aeiou; flag when ≥4 cluster on one line. Identifiers, dates and
    SHAs are excluded by :func:`_is_wordish` — they are vowel-less by
    construction and are not what collapse looks like (2026-09-14).
    """
    vowelless_long = [
        t
        for t in WORD_SPLIT_RE.split(line)
        if len(t) >= 6 and _is_wordish(t) and not (VOWELS & set(t))
    ]
    if len(vowelless_long) < LOW_DENSITY_MIN_TOKENS:
        return None
    return Finding(
        "low-dictionary-density",
        str(path),
        number,
        line[:80],
        _fingerprint("low-dictionary-density", str(path), number, line[:80]),
    )


def scan_file(path: Path) -> list[Finding]:
    """Every finding in one file; code fences and front matter are exempt."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        return [_non_utf8_finding(path, exc)]
    findings: list[Finding] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or _is_skippable_line(line):
            continue
        findings.extend(_mixed_script_findings(path, number, line))
        density = _low_density_finding(path, number, line)
        if density is not None:
            findings.append(density)
    return findings


DEFAULT_SIZE_LIMIT = 2 * 1024 * 1024  # 2MiB per file guard


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if path.suffix.lower() != ".md":
            continue
        if path.stat().st_size > DEFAULT_SIZE_LIMIT:
            continue
        findings.extend(scan_file(path))
    return findings


def _load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    data = json.loads(BASELINE_PATH.read_text())
    return {item["fingerprint"] for item in data.get("known_findings", [])}


def _save_baseline(findings: list[Finding]) -> None:
    BASELINE_PATH.write_text(
        json.dumps(
            {"known_findings": [f.to_json() for f in findings]},
            indent=2,
        )
        + "\n"
    )


def _partition_paths(argv: list[str]) -> tuple[list[Path], list[Path]]:
    """Split arguments into the STRICT tree (docs/adr) and the baselined rest."""
    strict = [Path(p) for p in argv if p.startswith("docs/adr/")]
    baselined = [Path(p) for p in argv if p.startswith("docs/") and not p.startswith("docs/adr/")]
    return strict, baselined


def _report_failures(findings: list[Finding], summary: str) -> int:
    """Print up to ten findings plus the summary line; always a failure."""
    for finding in findings[:10]:
        print(f"[fail] {finding.rule} {finding.file}:{finding.line} {finding.token}")
    print(summary)
    return 1


def default_paths(root: Path | None = None) -> list[str]:
    """Every .md under DEFAULT_SCOPES, repo-relative (the CI and local-gate scope)."""
    root = REPO_ROOT if root is None else root
    return sorted(
        path.relative_to(root).as_posix()
        for scope in DEFAULT_SCOPES
        for path in (root / scope).rglob("*.md")
        if path.is_file()
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = default_paths()
    strict_paths, baseline_paths = _partition_paths(argv)

    strict_findings = scan_paths(strict_paths)
    baseline_hits = scan_paths(baseline_paths)

    baseline = _load_baseline()
    unrecognized = [f for f in baseline_hits if f.fingerprint not in baseline]

    if strict_findings:
        return _report_failures(
            strict_findings,
            f"STRICT doc sanity: {len(strict_findings)} finding(s) in docs/adr/",
        )
    if unrecognized:
        return _report_failures(
            unrecognized,
            f"BASELINE doc sanity: {len(unrecognized)} new finding(s); record them in "
            f"{BASELINE_PATH} only after a human reads the line (ratchet is a real gate).",
        )

    print(f"doc sanity OK: 0 strict findings, {len(baseline_hits)} baselined findings only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
