"""R1-7: canary sweep scanner (unit level, §5.3)."""

from __future__ import annotations

from pathlib import Path

from tools.canary_credential_sweep import main, scan_tree

CANARY = "sk-canary-abc123deadbeef"
FP = "sha256:1a2b3c4d"


def _tree(tmp_path, files: dict[str, str]) -> Path:
    root = tmp_path / "canary-root"
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


class TestScan:
    def test_clean_tree_passes(self, tmp_path):
        root = _tree(tmp_path, {"a.txt": "hello", "sub/b.log": "world"})
        report = scan_tree(root, {CANARY: "canary", FP: "fingerprint"})
        assert not report.violations
        assert not report.allowed_hits

    def test_canary_hit_is_violation(self, tmp_path):
        root = _tree(tmp_path, {"notes.txt": f"used {CANARY} here"})
        report = scan_tree(root, {CANARY: "canary"})
        assert len(report.violations) == 1
        hit = report.violations[0]
        assert hit.needle_kind == "canary"
        assert "<needle>" in hit.line_preview
        assert CANARY not in hit.line_preview

    def test_fingerprint_hit_flagged(self, tmp_path):
        root = _tree(tmp_path, {"dump.sql": f"INSERT ... ({FP}, 'x')"})
        report = scan_tree(root, {FP: "fingerprint"})
        assert len(report.violations) == 1

    def test_token_hit_flagged_too(self, tmp_path):
        root = _tree(tmp_path, {"proc.txt": "R4_TOKEN=sess-secret-token"})
        report = scan_tree(root, {"sess-secret-token": "token"})
        assert [h.needle_kind for h in report.violations] == ["token"]

    def test_allowed_path_globs_swallowed(self, tmp_path):
        root = _tree(
            tmp_path,
            {"dump.ciphertext.sql": f"blob {FP}", "logs/app.log": f"echo {FP}"},
        )
        report = scan_tree(root, {FP: "fingerprint"})
        # Genuine leak on the app log stays a violation; the sealed row is allowed.
        assert {h.path for h in report.violations} == {"logs/app.log"}
        assert {h.path for h in report.allowed_hits} == {"dump.ciphertext.sql"}

    def test_unknown_binary_scanned_safely(self, tmp_path):
        root = _tree(tmp_path, {"bin.obj": "\x00\x01\x02binary"})
        (root / "bin.obj").write_bytes(b"\x00\x01\x02" + CANARY.encode() + b"\x04")
        report = scan_tree(root, {CANARY: "canary"})
        assert len(report.violations) == 1

    def test_symlinks_skipped(self, tmp_path):
        root = _tree(tmp_path, {"real.txt": "ok", "dir/hide.txt": CANARY})
        (root / "link.txt").symlink_to(root / "dir" / "hide.txt")
        report = scan_tree(root, {CANARY: "canary"})
        # Symlinks are not followed — leak still hidden across a link, honest scan.
        scanned = {h.path for h in report.violations}
        assert "dir/hide.txt" in scanned
        assert "link.txt" not in scanned


class TestCli:
    def test_no_needles_errors(self, tmp_path, capsys):
        assert main(["--scan-dir", str(tmp_path)]) == 2
        assert "nothing to scan" in capsys.readouterr().err

    def test_violations_exit_1(self, tmp_path):
        root = _tree(tmp_path, {"boom.txt": CANARY})
        code = main(["--scan-dir", str(root), "--canary", CANARY])
        assert code == 1

    def test_clean_exit_0(self, tmp_path):
        root = _tree(tmp_path, {"clean.txt": "nothing here"})
        assert main(["--scan-dir", str(root), "--canary", CANARY, "--fingerprint", FP]) == 0

    def test_live_mode_exits_2_with_guidance(self, capsys, monkeypatch):
        for var in ("UMRS_SEAL_PUBKEY_B64", "UMRS_UNSEAL_PRIVKEY_PATH"):
            monkeypatch.delenv(var, raising=False)
        assert main(["--live"]) == 2
        err = capsys.readouterr().err
        assert "missing config" in err
