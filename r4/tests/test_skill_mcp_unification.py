"""C6 unification: SkillRegistry.load_mcp_skills synthesizes a skill
per registered MCP server (bug.memo C6, 2026-04-25)."""

from __future__ import annotations

from types import SimpleNamespace

from r4.companion.skills import SkillRegistry


class _StubMCP:
    def list_servers(self):
        return [
            {"name": "filesystem", "description": "Local FS tools"},
            {"name": "database"},
        ]

    def list_tools(self, server=None, refresh=False):
        return [
            SimpleNamespace(name="fs_read", server="filesystem"),
            SimpleNamespace(name="fs_write", server="filesystem"),
            SimpleNamespace(name="db_query", server="database"),
        ]


class _StubClient:
    mcp = _StubMCP()


def test_load_mcp_skills_creates_skill_per_server() -> None:
    reg = SkillRegistry()
    n = reg.load_mcp_skills(_StubClient())
    assert n == 2
    fs = reg.get("mcp:filesystem")
    db = reg.get("mcp:database")
    assert fs is not None and db is not None
    assert fs.source == "mcp"
    assert set(fs.tool_names) == {"fs_read", "fs_write"}
    assert db.tool_names == ["db_query"]


def test_load_mcp_skills_handles_no_servers() -> None:
    class _Empty:
        class mcp:
            @staticmethod
            def list_servers():
                return []

            @staticmethod
            def list_tools(server=None, refresh=False):
                return []

    reg = SkillRegistry()
    assert reg.load_mcp_skills(_Empty()) == 0


def test_load_mcp_skills_swallows_failures() -> None:
    class _Broken:
        class mcp:
            @staticmethod
            def list_servers():
                raise RuntimeError("gateway down")

    reg = SkillRegistry()
    assert reg.load_mcp_skills(_Broken()) == 0
