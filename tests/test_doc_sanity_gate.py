"""Doc sanity gate — generation-collapse detector."""

from __future__ import annotations

import tempfile
from pathlib import Path

import tools.doc_sanity_gate as gate


def _tmp(text: str) -> Path:
    lev = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    lev.write(text)
    lev.flush()
    return Path(lev.name)


def test_clean_english_passes():
    path = _tmp("# ADR\nThe family filter is honest.\nDetails follow.\n")
    assert gate.scan_file(path) == []


def test_cjk_only_passes():
    path = _tmp("# 設計\n進捗は良好である。\nカタログは名前だけを表示する。\n")
    assert gate.scan_file(path) == []


def test_mixed_script_token_flagged():
    path = _tmp("The catalog gets 出力saturated with tokens.\n")
    findings = gate.scan_file(path)
    assert any(f.rule == "mixed-script-token" for f in findings)


def test_url_and_code_fence_exempted():
    path = _tmp(
        "```\nThe word 出力 appears inside code.\n```\n"
        "Refer to https://example.com/docs/出力/section for the shared article.\n"
    )
    # URL line exempt; fence content exempt.
    findings = gate.scan_file(path)
    assert not any(f.rule == "mixed-script-token" for f in findings)


def test_non_utf8_hard_fails(tmp_path: Path):
    raw = tmp_path / "broken.md"
    raw.write_bytes(b"# title \xff\xed\n non-decodable region")
    findings = gate.scan_file(raw)
    assert findings
    assert findings[0].rule == "non-utf8"


def test_low_dictionary_density_paragraph_flagged():
    path = _tmp(
        "xkpvwrtjjlmnoqtis tphmnbcwr kjtphwzmck qxbpmnrtwjk sptyjkmwrt sprtnwqjm "
        "thebwr jkmrtwxpzn\n"
    )
    findings = gate.scan_file(path)
    assert any(f.rule == "low-dictionary-density" for f in findings)


def test_dated_status_table_row_not_flagged():
    """ISO dates are vowel-less but are not generation collapse (2026-09-14).

    The ADR index tripped the rule on four dates in one table row, which
    made every commit touching docs/adr fail the local gate.
    """
    path = _tmp(
        "| [036](036-loop-hosting.md) | Proposed (round 1 implemented "
        "2026-09-07; cluster-venue leg verified live 2026-09-10) | "
        "Amendment 1 (2026-09-06): snapshots · Amendment 2 (2026-09-07) |\n"
    )
    findings = gate.scan_file(path)
    assert not any(f.rule == "low-dictionary-density" for f in findings)


def test_identifier_dense_line_not_flagged():
    """Gap ids, commit/job handles and paths are identifiers, not words."""
    path = _tmp(
        "- **Gap anchors:** G-P4-1 (no durable loop job kind), G-P8-1 "
        "decisions; checkout converged to `umrs@5db8ac12` via "
        "`job-14d4eed4c54c` writing `~/.config/cni/net.d` on the venue\n"
    )
    findings = gate.scan_file(path)
    assert not any(f.rule == "low-dictionary-density" for f in findings)


def test_wordish_helper_separates_words_from_identifiers():
    assert gate._is_wordish("xkpvwrtjjlmnoqtis")
    assert gate._is_wordish("(sptyjkmwrt,")
    assert not gate._is_wordish("2026-09-07;")
    assert not gate._is_wordish("umrs@5db8ac12")
    assert not gate._is_wordish("G-P4-1")
    assert not gate._is_wordish("(0086)")


def test_fingerprint_stable():
    a = gate._fingerprint("r", "f", 3, "token")
    b = gate._fingerprint("r", "f", 3, "token")
    assert a == b
