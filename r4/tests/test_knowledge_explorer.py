"""U4 tests — knowledge explorer data layer + app shell."""

from __future__ import annotations

from r4.knowledge_explorer.data import (
    SECTIONS,
    fetch_detail,
    fetch_documents,
    fetch_project_data,
    fetch_sections,
    verification_badge,
)


class _FakeKnowledge:
    """Recording stand-in for KnowledgeClient."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self, project_id):
        self.calls.append(f"status:{project_id}")
        return {
            "readiness_level": "partial",
            "artifacts": [
                {"artifact_id": "art-1", "title": "paper.pdf", "kind": "document"},
                {"artifact_id": "art-2", "title": "nb.ipynb", "kind": "notebook"},
            ],
        }

    def findings(self, project_id):
        self.calls.append("findings")
        return [{"id": "f-1", "severity": "warn", "message": "coverage gap", "check": "cov"}]

    def memory(self, project_id):
        self.calls.append("memory")
        return {"memories": [{"id": "m-1", "title": "hypothesis H7", "type": "hypo"}]}

    def list_plans(self, project_id):
        self.calls.append("plans")
        return [{"plan_id": "p-1", "goal": "screen catalysts", "status": "open"}]

    def readiness(self, project_id):
        self.calls.append("readiness")
        return {"readiness_level": "partial"}

    def get_lineage(self, project_id, artifact_id, depth=3):
        self.calls.append(f"lineage:{artifact_id}")
        return {"artifact_id": artifact_id, "ancestors": ["src-0"], "descendants": []}


# ── Data layer ───────────────────────────────────────────────────────────────


class TestDataLayer:
    def test_badge_levels(self):
        assert "✓" in verification_badge("ready")
        assert "◐" in verification_badge("partial")
        assert "✗" in verification_badge("insufficient")
        assert "…" in verification_badge(None)

    def test_project_data_aggregates(self):
        knowledge = _FakeKnowledge()
        data = fetch_project_data(knowledge, "proj-a")
        assert len(data["artifacts"]) == 2
        assert len(data["findings"]) == 1
        assert len(data["memory"]) == 1
        assert len(data["plans"]) == 1
        assert data["readiness_level"] == "partial"

    def test_sections_render_with_counts(self):
        knowledge = _FakeKnowledge()
        data = fetch_project_data(knowledge, "proj-a")
        rows = fetch_sections(data)
        assert [r["id"] for r in rows] == list(SECTIONS)
        counts = {r["id"]: r["count"] for r in rows}
        # graphs section added alongside the classic four (empty without registry)
        assert counts == {
            "artifacts": 2,
            "findings": 1,
            "memory": 1,
            "plans": 1,
            "graphs": 0,
        }

    def test_documents_artifacts_carry_badge(self):
        knowledge = _FakeKnowledge()
        rows = fetch_documents(knowledge, "proj-a", "artifacts")
        assert len(rows) == 2
        assert rows[0]["id"] == "art-1"
        assert "◐" in rows[0]["title"]  # partial badge from readiness

    def test_documents_by_section(self):
        knowledge = _FakeKnowledge()
        findings = fetch_documents(knowledge, "proj-a", "findings")
        assert findings[0]["id"] == "f-1"
        memory = fetch_documents(knowledge, "proj-a", "memory")
        assert memory[0]["id"] == "m-1"
        plans = fetch_documents(knowledge, "proj-a", "plans")
        assert plans[0]["id"] == "p-1"

    def test_unknown_section_returns_error_row(self):
        knowledge = _FakeKnowledge()
        rows = fetch_documents(knowledge, "proj-a", "nope")
        assert rows[0]["kind"] == "error"
        assert "unknown section" in rows[0]["label"]

    def test_detail_lineage_for_artifacts(self):
        knowledge = _FakeKnowledge()
        detail = fetch_detail(knowledge, "proj-a", "artifacts", "art-1")
        assert detail["kind"] == "lineage"
        assert detail["body"]["ancestors"] == ["src-0"]
        assert "lineage:art-1" in knowledge.calls

    def test_detail_generic_row_elsewhere(self):
        knowledge = _FakeKnowledge()
        detail = fetch_detail(knowledge, "proj-a", "findings", "f-1")
        assert detail["kind"] == "row"


# ── App pilot ────────────────────────────────────────────────────────────────


def _make_app():
    from r4.knowledge_explorer.app import ExploreApp

    knowledge = _FakeKnowledge()

    def data() -> dict:
        return fetch_project_data(knowledge, "proj-a")

    def docs(section) -> list[dict]:
        return fetch_documents(knowledge, "proj-a", section)

    def detail(section, doc_id) -> dict:
        return fetch_detail(knowledge, "proj-a", section, doc_id)

    return ExploreApp(
        project_id="proj-a",
        project_data_provider=data,
        documents_provider=docs,
        detail_provider=detail,
        export_provider=lambda: "bundle-path.kb",
    )


class TestExploreApp:
    async def test_sections_rendered_on_mount(self):
        from textual.widgets import ListView

        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            sections = app.query_one("#sections", ListView)
            assert len(sections.children) == len(SECTIONS)
            docs = app.query_one("#docs", ListView)
            assert len(docs.children) == 2  # artifacts by default

    async def test_section_switch_reloads_docs(self):
        from textual.widgets import ListView

        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._load_documents("findings")
            await pilot.pause()
            docs = app.query_one("#docs", ListView)
            assert len(docs.children) == 1
            assert docs.children[0].id == "f-1"

    async def test_doc_selection_updates_detail(self):
        from textual.widgets import Static

        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._load_detail("art-1")
            await pilot.pause()
            detail_text = str(app.query_one("#detail", Static).render())
            assert "art-1" in detail_text
            assert "ancestors" in detail_text

    async def test_export_notifies(self):
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_export()
            await pilot.pause()
            assert app._export_provider is not None
