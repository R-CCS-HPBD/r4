"""Tests for COR-001 deployment checklist generator."""

from __future__ import annotations

import sys

from tools import deployment_checklist as dc


class TestChecklistGeneration:
    def test_hpc_full(self):
        cl = dc.generate_checklist("hpc-full")
        assert cl is not None
        assert cl.profile == "hpc-full"
        assert cl.envelope == "hpc"
        assert len(cl.items) >= 15

    def test_hpc_minimal(self):
        cl = dc.generate_checklist("hpc-minimal")
        assert cl is not None
        assert cl.envelope == "hpc"

    def test_k8s_cluster(self):
        cl = dc.generate_checklist("k8s-cluster")
        assert cl is not None
        assert cl.envelope == "k8s"
        assert any("helm" in i.description.lower() for i in cl.items)

    def test_dev_compose(self):
        cl = dc.generate_checklist("dev-compose")
        assert cl is not None
        assert cl.envelope == "dev"

    def test_unknown_profile(self):
        assert dc.generate_checklist("nonexistent") is None


class TestChecklistOutput:
    def test_text_output(self):
        cl = dc.generate_checklist("hpc-full")
        text = cl.to_text()
        assert "hpc-full" in text
        assert "[CRIT]" in text

    def test_markdown_output(self):
        cl = dc.generate_checklist("hpc-full")
        md = cl.to_markdown()
        assert "# Deployment Checklist" in md
        assert "## Prerequisites" in md
        assert "- [ ]" in md
        assert "```bash" in md

    def test_categories_present(self):
        cl = dc.generate_checklist("hpc-full")
        cats = {i.category for i in cl.items}
        assert "prereq" in cats
        assert "config" in cats
        assert "validate" in cats
        assert "smoke" in cats


class TestChecklistItem:
    def test_critical_default(self):
        item = dc.ChecklistItem(1, "prereq", "test")
        assert item.critical is True

    def test_non_critical(self):
        item = dc.ChecklistItem(1, "prereq", "test", critical=False)
        assert item.critical is False

    def test_with_command(self):
        item = dc.ChecklistItem(1, "smoke", "test", command="echo ok")
        assert item.command == "echo ok"


class TestMain:
    def test_main_with_profile(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dc", "--profile", "hpc-full"])
        rc = dc.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "hpc-full" in out

    def test_main_all(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dc", "--all"])
        rc = dc.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "hpc-full" in out
        assert "k8s-cluster" in out

    def test_main_markdown(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dc", "--profile", "dev-compose", "--format", "markdown"])
        rc = dc.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "# Deployment Checklist" in out

    def test_main_output_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "dc",
                "--profile",
                "hpc-full",
                "--output-dir",
                str(tmp_path),
            ],
        )
        rc = dc.main()
        assert rc == 0
        assert (tmp_path / "checklist-hpc-full.txt").exists()

    def test_main_unknown_profile(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dc", "--profile", "bad"])
        rc = dc.main()
        assert rc == 1
