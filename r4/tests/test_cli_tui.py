"""Tests for ``r4 tui`` binary resolution and install helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from r4._cli import tui as tui_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_TUI_BIN", raising=False)
    monkeypatch.delenv("R4_TUI_DIST_BASE_URL", raising=False)
    monkeypatch.delenv("R4_TUI_VERSION", raising=False)
    monkeypatch.setenv("R4_TUI_HOME", str(tmp_path))
    yield


class TestPlatformMapping:
    def test_known_platforms(self, monkeypatch):
        cases = {
            ("darwin", "arm64"): "darwin-arm64",
            ("darwin", "x86_64"): "darwin-x64",
            ("linux", "x86_64"): "linux-x64",
            ("linux", "aarch64"): "linux-arm64",
        }
        for (plat, machine), expected in cases.items():
            monkeypatch.setattr(tui_module.sys, "platform", plat)
            monkeypatch.setattr(tui_module.platform, "machine", lambda m=machine: m)
            assert tui_module.platform_key() == expected
            assert tui_module.tui_asset_name() == f"r4-tui-{expected}"

    def test_unknown_platform(self, monkeypatch):
        monkeypatch.setattr(tui_module.sys, "platform", "win32")
        monkeypatch.setattr(tui_module.platform, "machine", lambda: "AMD64")
        assert tui_module.platform_key() is None
        assert tui_module.tui_asset_name() is None

    def test_intel_mac_gets_the_x64_build_not_arm64(self, monkeypatch):
        """Rosetta runs x86_64 code on arm64, not arm64 code on an Intel Mac;
        before 2026-11-07 an Intel Mac downloaded a binary it cannot exec."""
        monkeypatch.setattr(tui_module.sys, "platform", "darwin")
        monkeypatch.setattr(tui_module.platform, "machine", lambda: "x86_64")
        assert tui_module.tui_asset_name() == "r4-tui-darwin-x64"

    def test_windows_native_install_points_at_wsl2(self, monkeypatch, capsys):
        monkeypatch.setattr(tui_module.sys, "platform", "win32")
        monkeypatch.setattr(tui_module.platform, "machine", lambda: "AMD64")
        assert tui_module.install_binary() == 1
        err = capsys.readouterr().err
        assert "No prebuilt TUI binary for win32/AMD64" in err
        assert "WSL2" in err

    def test_other_unsupported_platform_has_no_wsl_hint(self, monkeypatch):
        monkeypatch.setattr(tui_module.sys, "platform", "freebsd14")
        monkeypatch.setattr(tui_module.platform, "machine", lambda: "amd64")
        msg = tui_module.unsupported_platform_message()
        assert "freebsd14/amd64" in msg
        assert "WSL2" not in msg


class TestResolve:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        binary = tmp_path / "custom-tui"
        binary.write_text("#!/bin/sh\n")
        monkeypatch.setenv("R4_TUI_BIN", str(binary))
        assert tui_module.resolve_tui_binary() == binary

    def test_env_override_missing_file_falls_through_to_none(self, monkeypatch):
        monkeypatch.setenv("R4_TUI_BIN", "/definitely/not/here")
        assert tui_module.resolve_tui_binary() is None

    def test_user_bin(self, tmp_path):
        path = tmp_path / "bin" / "r4-tui"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"binary")
        assert tui_module.resolve_tui_binary() == path

    def test_nothing_installed(self):
        assert tui_module.resolve_tui_binary() is None


class TestDistUrl:
    def test_default(self):
        url = tui_module.dist_base_url()
        assert url.startswith("https://")
        assert not url.endswith("/")

    def test_env_override_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("R4_TUI_DIST_BASE_URL", "https://internal.example/tui/")
        assert tui_module.dist_base_url() == "https://internal.example/tui"

    def test_tag_pins_download_path(self):
        assert tui_module.dist_base_url("v0.2.0-rc2") == (
            f"https://github.com/{tui_module.RELEASE_REPO}/releases/download/v0.2.0-rc2"
        )


class TestReleaseRepo:
    """The public repository is the default; R4_RELEASE_REPO overrides it.

    The module reads the variable once at import (a reload would re-register
    the ``tui`` command), so these tests drive the helper it calls.
    """

    def test_default_is_public_repo(self, monkeypatch):
        monkeypatch.delenv("R4_RELEASE_REPO", raising=False)
        assert tui_module.release_repo_from_env() == "R-CCS-HPBD/r4"

    def test_empty_override_keeps_public_repo(self, monkeypatch):
        monkeypatch.setenv("R4_RELEASE_REPO", "")
        assert tui_module.release_repo_from_env() == "R-CCS-HPBD/r4"

    def test_env_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("R4_RELEASE_REPO", "example-org/r4-dev")
        assert tui_module.release_repo_from_env() == "example-org/r4-dev"

    def test_module_constant_comes_from_the_helper(self):
        assert tui_module.PUBLIC_RELEASE_REPO == "R-CCS-HPBD/r4"
        assert tui_module.RELEASE_REPO_ENV == "R4_RELEASE_REPO"
        assert tui_module.DEFAULT_DIST_BASE_URL == (
            f"https://github.com/{tui_module.RELEASE_REPO}/releases/latest/download"
        )


class TestReleaseTag:
    """releases/latest skips prereleases — the newest release is resolved instead."""

    def test_version_env_pin_wins(self, monkeypatch):
        monkeypatch.setenv("R4_TUI_VERSION", "v0.1.0")
        monkeypatch.setattr(tui_module, "_newest_release_tag", lambda: "v9")
        assert tui_module.release_tag() == "v0.1.0"

    def test_newest_skips_drafts_keeps_prereleases(self, monkeypatch):
        monkeypatch.delenv("R4_TUI_VERSION", raising=False)
        monkeypatch.setattr(
            tui_module.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("404")),
        )
        monkeypatch.setattr(
            tui_module,
            "_gh_releases",
            lambda: [
                {"tag_name": "v0.3.0", "draft": True},
                {"tag_name": "v0.2.0-rc2", "draft": False, "prerelease": True},
            ],
        )
        assert tui_module.release_tag() == "v0.2.0-rc2"

    def test_unreachable_is_none(self, monkeypatch):
        monkeypatch.delenv("R4_TUI_VERSION", raising=False)
        monkeypatch.setattr(
            tui_module.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
        )
        monkeypatch.setattr(tui_module, "_gh_releases", lambda: [])
        assert tui_module.release_tag() is None


class TestSha256:
    def test_known_digest(self, tmp_path):
        f = tmp_path / "x"
        f.write_bytes(b"hello")
        assert (
            tui_module._sha256(f)
            == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )


class TestCutoverDecision:
    """Console-v3-launch surface decision (post-Ink, ADR-014 deletion leg).

    --legacy-tui no longer selects Ink; it yields the REPL fallback. The
    R4_COMPANION_TUI=ink env knob is gone with the Ink tree.
    """

    def test_legacy_flag_wins(self, monkeypatch):
        from r4.companion.ui import _should_launch_v3

        monkeypatch.setattr("r4._cli.console.console_available", lambda: True)
        assert _should_launch_v3(legacy_tui=True) is False

    def test_ink_env_no_longer_selects_ink(self, monkeypatch):
        """R4_COMPANION_TUI=ink has no effect after the Ink removal."""
        from r4.companion.ui import _should_launch_v3

        monkeypatch.setenv("R4_COMPANION_TUI", "ink")
        monkeypatch.setattr("r4._cli.console.console_available", lambda: True)
        assert _should_launch_v3(legacy_tui=False) is True  # env inert now

    def test_v3_when_available(self, monkeypatch):
        from r4.companion.ui import _should_launch_v3

        monkeypatch.delenv("R4_COMPANION_TUI", raising=False)
        monkeypatch.setattr("r4._cli.console.console_available", lambda: True)
        assert _should_launch_v3(legacy_tui=False) is True

    def test_no_v3_when_unavailable(self, monkeypatch):
        from r4.companion.ui import _should_launch_v3

        monkeypatch.delenv("R4_COMPANION_TUI", raising=False)
        monkeypatch.setattr("r4._cli.console.console_available", lambda: False)
        assert _should_launch_v3(legacy_tui=False) is False

    def test_probe_failure_falls_back_honestly(self, monkeypatch):
        from r4.companion.ui import _should_launch_v3

        def _boom():
            raise RuntimeError("broken")

        monkeypatch.delenv("R4_COMPANION_TUI", raising=False)
        monkeypatch.setattr("r4._cli.console.console_available", _boom)
        assert _should_launch_v3(legacy_tui=False) is False


class TestConsoleAvailable:
    def test_binary_short_circuit(self, monkeypatch):
        from r4._cli import console as console_module

        fake_bin = Path(__file__)
        monkeypatch.setattr(console_module, "resolve_tui_binary", lambda: fake_bin)
        assert console_module.console_available() is True

    def test_no_binary_needs_pkg_and_bun(self, monkeypatch):
        from r4._cli import console as console_module

        monkeypatch.setattr(console_module, "resolve_tui_binary", lambda: None)
        monkeypatch.setattr(console_module, "_console_package_dir", lambda: Path("/x"))
        monkeypatch.setattr(console_module, "_find_bun", lambda: "/usr/bin/bun")
        assert console_module.console_available() is True
        monkeypatch.setattr(console_module, "_find_bun", lambda: None)
        assert console_module.console_available() is False
        monkeypatch.setattr(console_module, "_console_package_dir", lambda: None)
        monkeypatch.setattr(console_module, "_find_bun", lambda: "/usr/bin/bun")
        assert console_module.console_available() is False


class TestDownloadFailureHint:
    """The default release repository is public (2026-11-07).

    Before, a failed download told every user "The repository is private",
    although ``PUBLIC_RELEASE_REPO`` is the default and needs no token.
    """

    def test_failed_download_names_the_repo_and_the_private_case(self, monkeypatch, capsys):
        monkeypatch.setattr(tui_module, "tui_asset_name", lambda: "r4-tui-linux-x64")

        def offline(name, tmp):
            raise OSError("network unreachable")

        monkeypatch.setattr(tui_module, "_fetch_assets", offline)
        assert tui_module.install_binary() == 1
        err = capsys.readouterr().err
        assert "The repository is private" not in err
        assert f"Release repository: {tui_module.RELEASE_REPO}" in err
        assert f"If {tui_module.RELEASE_REPO_ENV} names a private" in err
        assert "network unreachable" in err
