from __future__ import annotations

from argparse import Namespace


class EmptySkillLoader:
    def scan_local(self):
        return []

    def search(self, query: str):
        return []


def test_skill_search_queries_remote_registry(monkeypatch, capsys) -> None:
    from r4._cli import skill as skill_cli

    class FakeClient:
        def get(self, path, params=None):
            assert path == "/gw/skills/search"
            assert params == {"q": "rag"}
            return [
                {
                    "skill_id": "skill-1",
                    "scope": "project",
                    "manifest": {
                        "name": "rag-literature",
                        "description": "Search project literature",
                    },
                }
            ]

    monkeypatch.setattr(skill_cli, "SkillLoader", EmptySkillLoader)
    monkeypatch.setattr("r4.client.Client", FakeClient)

    rc = skill_cli.SkillCommand()._search(Namespace(query="rag"))

    assert rc == 0
    output = capsys.readouterr().out
    assert "rag-literature [project]: Search project literature" in output


def test_install_from_registry_writes_skill_bundle(tmp_path, monkeypatch) -> None:
    from r4._cli import skill as skill_cli

    monkeypatch.setattr(skill_cli, "_SKILL_DIR", tmp_path)

    data = {
        "skill_id": "skill-1",
        "manifest": {
            "name": "rag-literature",
            "version": "1.0.0",
            "description": "Search project literature",
            "tags": ["rag"],
        },
        "skill_md": "Use project RAG before answering literature questions.",
    }

    rc = skill_cli.SkillCommand()._install_from_registry(data)

    assert rc == 0
    target = tmp_path / "rag-literature"
    assert (target / "config.json").exists()
    assert (target / "SKILL.md").read_text() == data["skill_md"]


def test_install_from_dir_rejects_invalid_skill_directory(tmp_path, capsys) -> None:
    from r4._cli import skill as skill_cli

    rc = skill_cli.SkillCommand()._install_from_dir(tmp_path)

    assert rc == 1
    assert "Not a valid skill directory" in capsys.readouterr().out
