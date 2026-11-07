"""Tests for the R4 CLI UX Architecture Redesign.

Covers:
- Deprecation framework (emit, suppress, phases)
- New command routing (agent, search, team, run, admin)
- Backward compatibility (old paths still work)
- Typo recovery (Levenshtein suggestions)
- Command groups (admin namespace)
- TUI slash aliases
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_cli_state(tmp_path, monkeypatch):
    """Keep CLI tests isolated from host config and singleton state."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_SUPPRESS_DEPRECATIONS", raising=False)

    import r4.auth
    import r4.config
    import r4.project

    r4.auth._auth_manager = None
    r4.config._config = None
    r4.project._project_manager = None


# ---------------------------------------------------------------------------
# Deprecation framework
# ---------------------------------------------------------------------------


class TestDeprecationFramework:
    """Test the formal deprecation warning system."""

    def test_emit_deprecation_warning_soft(self, capsys):
        from r4._cli.deprecation import DeprecationPhase, emit_deprecation_warning

        emit_deprecation_warning(
            "r4 governance",
            "r4 admin governance",
            phase=DeprecationPhase.SOFT,
        )
        err = capsys.readouterr().err
        assert "r4 governance" in err
        assert "r4 admin governance" in err
        assert "Deprecated" in err

    def test_emit_deprecation_warning_hidden(self, capsys):
        from r4._cli.deprecation import DeprecationPhase, emit_deprecation_warning

        emit_deprecation_warning(
            "r4 trace",
            "r4 admin trace",
            phase=DeprecationPhase.HIDDEN,
        )
        err = capsys.readouterr().err
        assert "deprecated" in err.lower()
        # Hidden phase uses shorter format
        assert "R4_SUPPRESS_DEPRECATIONS" not in err

    def test_suppress_via_env(self, monkeypatch, capsys):
        monkeypatch.setenv("R4_SUPPRESS_DEPRECATIONS", "1")

        from r4._cli.deprecation import DeprecationPhase, emit_deprecation_warning

        emit_deprecation_warning(
            "r4 governance",
            "r4 admin governance",
            phase=DeprecationPhase.SOFT,
        )
        err = capsys.readouterr().err
        assert err == ""

    def test_suppress_true_string(self, monkeypatch, capsys):
        monkeypatch.setenv("R4_SUPPRESS_DEPRECATIONS", "true")

        from r4._cli.deprecation import emit_deprecation_warning

        emit_deprecation_warning("r4 foo", "r4 bar")
        err = capsys.readouterr().err
        assert err == ""


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Typo recovery
# ---------------------------------------------------------------------------


class TestTypoRecovery:
    def test_levenshtein_exact(self):
        from r4.cli import _levenshtein

        assert _levenshtein("hello", "hello") == 0

    def test_levenshtein_one_edit(self):
        from r4.cli import _levenshtein

        assert _levenshtein("health", "healt") == 1
        assert _levenshtein("model", "modle") == 2

    def test_suggest_command_close(self):
        from r4.cli import _suggest_command

        known = ["health", "model", "agent", "search", "admin"]
        assert _suggest_command("healt", known) == "health"
        assert _suggest_command("modle", known) == "model"
        assert _suggest_command("serch", known) == "search"

    def test_suggest_command_too_far(self):
        from r4.cli import _suggest_command

        known = ["health", "model", "agent"]
        assert _suggest_command("zzzzz", known) is None


# ---------------------------------------------------------------------------
# Parser building
# ---------------------------------------------------------------------------


class TestParserBuilders:
    """Verify that extracted parser builders create proper subparser trees."""

    def test_build_agent_parser(self):
        import argparse

        from r4._cli.agent import build_agent_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        build_agent_parser(sub, name="agent")
        # Should be able to parse `agent chat`
        args = parser.parse_args(["agent", "chat"])
        assert args.agent_command == "chat"

    def test_build_search_parser(self):
        import argparse

        from r4._cli.search import build_search_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        build_search_parser(sub, name="search")
        args = parser.parse_args(["search", "--depth", "deep", "my query"])
        assert args.query == "my query"
        assert args.depth == "deep"

        args = parser.parse_args(["search", "--scope", "lab_only", "-p", "proj-1", "batch csv"])
        assert args.scope == "lab_only"
        assert args.project == "proj-1"
        assert args.query == "batch csv"

    def test_build_teamup_parser(self):
        import argparse

        from r4._cli.teamup import build_teamup_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        build_teamup_parser(sub, name="team")
        args = parser.parse_args(["team", "list"])
        assert args.teamup_command == "list"


# ---------------------------------------------------------------------------
# Command group (admin)
# ---------------------------------------------------------------------------


class TestAdminGroup:
    def test_admin_registers_children(self):
        import argparse

        from r4._cli.admin import AdminGroup

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        group = AdminGroup()
        group.register(sub)

        # Should parse `admin analytics`
        args = parser.parse_args(["admin", "analytics"])
        assert args.admin_command == "analytics"

    def test_admin_children_include_all(self):
        from r4._cli.admin import _build_admin_children

        children = _build_admin_children()
        names = {c.name for c in children}
        assert names == {"analytics", "trace"}


# ---------------------------------------------------------------------------
# Old commands removed from top-level registration
# ---------------------------------------------------------------------------


class TestOldCommandsRemoved:
    def test_old_commands_not_in_pre_register(self):
        from r4.cli import _PRE_REGISTER_COMMANDS

        for cmd in ("trace", "agent-exec", "ask"):
            assert cmd not in _PRE_REGISTER_COMMANDS, (
                f"{cmd} should be removed from _PRE_REGISTER_COMMANDS"
            )

    def test_old_modules_not_in_pre_register_modules(self):
        from r4.cli import _PRE_REGISTER_MODULES

        for mod in ("trace", "agent_exec", "ask"):
            assert mod not in _PRE_REGISTER_MODULES, (
                f"{mod} should be removed from _PRE_REGISTER_MODULES"
            )

    def test_new_commands_in_pre_register(self):
        from r4.cli import _PRE_REGISTER_COMMANDS

        # "run" moved to lazy registry dispatch (TASK-005); only
        # admin/team remain pre-registered.
        for cmd in ("admin", "team"):
            assert cmd in _PRE_REGISTER_COMMANDS, f"{cmd} missing from _PRE_REGISTER_COMMANDS"

    def test_new_modules_in_dispatch(self):
        from r4.cli import _DISPATCH_MODULES

        for mod in ("admin", "run", "team"):
            assert mod in _DISPATCH_MODULES, f"{mod} missing from _DISPATCH_MODULES"

    def test_old_modules_not_in_dispatch(self):
        from r4.cli import _DISPATCH_MODULES

        for mod in ("teamup", "agent_exec"):
            assert mod not in _DISPATCH_MODULES, f"{mod} should be removed from _DISPATCH_MODULES"


# ---------------------------------------------------------------------------
# Agent shortcut rewriting
# ---------------------------------------------------------------------------


class TestAgentShortcutRewriting:
    def test_top_level_agent_shortcut_how(self, monkeypatch):
        """r4 agent 'how do I ...' → r4 agent chat 'how do I ...'"""
        from r4.cli import _rewrite_agent_shortcut

        monkeypatch.setattr(sys, "argv", ["r4", "agent", "how do I check health"])
        _rewrite_agent_shortcut()
        assert sys.argv == ["r4", "agent", "chat", "how do I check health"]

    def test_top_level_agent_shortcut_action(self, monkeypatch):
        """r4 agent 'start qwen3' → r4 agent run 'start qwen3'"""
        from r4.cli import _rewrite_agent_shortcut

        monkeypatch.setattr(sys, "argv", ["r4", "agent", "start", "qwen3"])
        _rewrite_agent_shortcut()
        assert sys.argv == ["r4", "agent", "run", "start qwen3"]

    def test_ask_agent_shortcut_removed(self, monkeypatch):
        """r4 ask agent ... no longer rewrites (ask command removed)."""
        from r4.cli import _rewrite_agent_shortcut

        monkeypatch.setattr(sys, "argv", ["r4", "ask", "agent", "how do I start an endpoint"])
        _rewrite_agent_shortcut()
        # argv should be unchanged — ask path removed
        assert sys.argv == ["r4", "ask", "agent", "how do I start an endpoint"]


# ---------------------------------------------------------------------------
# Run command (agent-exec alias)
# ---------------------------------------------------------------------------


class TestRunCommand:
    def test_run_command_registered(self):
        from r4._cli.run import RunCommand

        assert RunCommand.name == "run"

    def test_run_command_delegates_to_agent_exec(self):
        from r4._cli.run import RunCommand

        cmd = RunCommand()
        # verify it can register without error
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        cmd.register(sub)
        args = parser.parse_args(["run", "list"])
        assert args.exec_command == "list"


# ---------------------------------------------------------------------------
# Team command (teamup alias)
# ---------------------------------------------------------------------------


class TestTeamCommand:
    def test_team_command_registered(self):
        from r4._cli.team import TeamCommand

        assert TeamCommand.name == "team"

    def test_team_parser_delegates_to_teamup(self):
        import argparse

        from r4._cli.team import TeamCommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        cmd = TeamCommand()
        cmd.register(sub)
        args = parser.parse_args(["team", "list"])
        assert args.teamup_command == "list"
