"""Pins for r4.tui.mcp_config JSONC loading (string-aware comment strip)."""

from __future__ import annotations

import json

from r4.tui.mcp_config import _load_json_file, _strip_jsonc_comments, set_mcp_enabled


def test_strip_jsonc_comments_preserves_https_urls():
    text = '{"url": "https://mcp.context7.com/mcp"} // see docs'
    assert json.loads(_strip_jsonc_comments(text))["url"] == "https://mcp.context7.com/mcp"


def test_strip_jsonc_comments_removes_full_line_comments():
    text = '// a comment\n{"a": 1}'
    assert json.loads(_strip_jsonc_comments(text)) == {"a": 1}


def test_strip_jsonc_comments_preserves_escaped_quotes():
    text = '{"note": "say \\"hi\\" // not a comment", "b": "x"}'
    assert json.loads(_strip_jsonc_comments(text))["note"] == 'say "hi" // not a comment'


def test_load_json_file_with_real_world_mcpservers(tmp_path):
    path = tmp_path / "mcp-servers.json"
    path.write_text(
        """
        {
          // mirror of an opencode mcp block
          "mcpServers": {
            "context7": {"type": "http", "url": "https://mcp.context7.com/mcp"},
            "hf": {"type": "http", "url": "https://hf.co/mcp",
                   "headers": {"Authorization": "Bearer ${HF_TOKEN}"}}
          }
        }
        """
    )
    data = _load_json_file(path)
    urls = {name: e["url"] for name, e in data["mcpServers"].items()}
    assert urls == {"context7": "https://mcp.context7.com/mcp", "hf": "https://hf.co/mcp"}


def test_set_mcp_enabled_preserves_comments_and_urls(tmp_path):
    path = tmp_path / "mcp-servers.json"
    url = "https://mcp.context7.com/mcp"
    entry = f'    "context7": {{\n      "type": "http",\n      "url": "{url}"\n    }}'
    path.write_text('{\n  // header comment\n  "mcpServers": {\n' + entry + "\n  }\n}\n")
    assert set_mcp_enabled("context7", False, path) is True
    text = path.read_text()
    assert "// header comment" in text
    assert "https://mcp.context7.com/mcp" in text
    assert '"enabled": false' in text
    assert json.loads(_strip_jsonc_comments(text))["mcpServers"]["context7"]["enabled"] is False


def test_set_mcp_enabled_flips_existing_flag_back(tmp_path):
    path = tmp_path / "mcp-servers.json"
    path.write_text('{"mcpServers": {"x": {"type": "stdio", "command": "uvx", "enabled": false}}}')
    assert set_mcp_enabled("x", True, path) is True
    text = path.read_text()
    assert text.count('"enabled"') == 1
    assert '"enabled": true' in text


def test_set_mcp_enabled_missing_entry_or_file_returns_false(tmp_path):
    assert set_mcp_enabled("nope", False, tmp_path / "absent.json") is False
    path = tmp_path / "mcp-servers.json"
    path.write_text('{"mcpServers": {"a": {"type": "http", "url": "https://a"}}}')
    assert set_mcp_enabled("other", False, path) is False
    assert '"enabled"' not in path.read_text()


def test_load_mcp_configs_reads_enabled_flag(tmp_path, monkeypatch):
    (tmp_path / "r4").mkdir()
    (tmp_path / "r4" / "mcp-servers.json").write_text(
        '{"mcpServers": {"a": {"type": "http", "url": "https://a", "enabled": false},'
        ' "b": {"type": "http", "url": "https://b"}}}'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from r4.tui.mcp_config import load_mcp_configs

    by_name = {c.name: c.enabled for c in load_mcp_configs()}
    assert by_name == {"a": False, "b": True}
