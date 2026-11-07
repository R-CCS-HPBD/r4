"""Gap-tool contract tests: sources, demo prep, gap wrappers, classification.

Pins the last CLI-only surfaces (source registry, endpoint upgrade,
workflow gates) now exposed to the Companion agent through
``build_client_extra_tools``, plus the demo-prep checklist/actions.
"""

from __future__ import annotations

import json

import pytest

from r4.companion.client_extra_tools import (
    build_client_extra_tools,
    build_demo_tools,
    build_source_tools,
)
from r4.companion.tools import get_builtin_tools


def _exec(tools, tool_name, **kwargs):
    tool = {t["name"]: t for t in tools}[tool_name]
    return tool["execute"](**kwargs)


class TestSourceTools:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        tools = build_source_tools()
        assert "No sources" in _exec(tools, "source_list")
        out = _exec(tools, "source_add", name="t1", url="http://gw:8000/", kind="platform")
        assert "t1" in out
        assert "t1" in _exec(tools, "source_list")
        assert "Active" in _exec(tools, "source_use", name="t1")
        assert _exec(tools, "source_list").startswith("*")
        assert "removed" in _exec(tools, "source_remove", name="t1")
        assert "No sources" in _exec(tools, "source_list")

    def test_bad_kind_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        out = _exec(build_source_tools(), "source_add", name="x", url="http://h", kind="weird")
        assert "Error" in out

    def test_use_says_it_takes_effect_next_session(self, tmp_path, monkeypatch):
        """X-c (multi-HPC plan S3, 2026-10-24): the running session keeps its
        site; the reply says so and points at ``site=`` (it used to claim
        "Active source: x" while nothing changed until a restart)."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        tools = build_source_tools()
        _exec(tools, "source_add", name="t1", url="http://gw:8000/", kind="platform")
        out = _exec(tools, "source_use", name="t1")
        assert "next session" in out and "site=" in out
        assert "next session" in _exec(tools, "source_use")

    def test_use_unknown_source_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        out = _exec(build_source_tools(), "source_use", name="ghost")
        assert "Error" in out


class _FakeWorkflows:
    def __init__(self):
        self.calls: list = []

    def upload(self, path, *, workflow_name=None, **kw):
        self.calls.append(("upload", path, workflow_name))
        return {"registered": workflow_name}

    def trigger(self, wf, *, input_files=None, parameters=None, project_id=None, wait=False, **kw):
        self.calls.append(("trigger", wf, input_files, parameters, project_id))
        return {"execution_id": "exec-1"}

    def approve_gate(self, execution_id, step, *, approved=True, **kw):
        self.calls.append(("gate", execution_id, step, approved))
        return {"status": "running"}


class _FakeVision:
    def upload_file(self, path):
        return {"uri": f"s3://bucket/{path.rsplit('/', 1)[-1]}"}


class _FakeMCP:
    def __init__(self):
        self.registered = None
        self.unregistered = None

    def register_server(self, name, url, description="", auth_type="none", **kw):
        self.registered = (name, url, description, auth_type)
        return {"server_id": "srv-1"}

    def unregister_server(self, server_id):
        self.unregistered = server_id
        return True


class _FakeClient:
    def __init__(self):
        self.workflows = _FakeWorkflows()
        self.vision = _FakeVision()
        self.mcp = _FakeMCP()
        self.upgraded = None
        self.provisioned = None

    def upgrade_endpoint(self, endpoint_id, model_path, wait=False, **kw):
        self.upgraded = (endpoint_id, model_path)
        return {"ok": True}

    def provision_endpoint(self, **kwargs):
        self.provisioned = kwargs
        return {"status": "provisioning", "instance_id": "ep-9"}


def _bundle(tmp_path):
    bundle = tmp_path / "wf"
    bundle.mkdir()
    (bundle / "workflow.json").write_text(json.dumps({"name": "wf-test"}))
    return bundle


_SPECS = {
    "alpha": {
        "aliases": ["first", "demo-a"],
        "title": "Example video demo A (fixture)",
        "bundle_candidates": ["{SPECS_DIR}/alpha/workflow"],
        "sample_candidates": ["{SPECS_DIR}/alpha/samples"],
        "default_project": "demo-project",
    },
    "beta": {
        "title": "Example video demo B (fixture)",
        "video_glob": ".mkv",
        "trigger_params": {"model": {"path": "weights/best.pt"}},
    },
}


@pytest.fixture
def demo_specs(tmp_path, monkeypatch):
    """A neutral demo-spec file: real demo definitions live outside this repo."""
    path = tmp_path / "demo_specs.json"
    path.write_text(json.dumps(_SPECS))
    monkeypatch.setenv("R4_DEMO_SPECS", str(path))
    return tmp_path


class TestDemoTools:
    def test_no_specs_configured(self, monkeypatch):
        """2026-11-03: no demo is built in; without R4_DEMO_SPECS the plan says so."""
        monkeypatch.delenv("R4_DEMO_SPECS", raising=False)
        text = _exec(build_demo_tools(None), "demo_plan", demo="all")
        assert "R4_DEMO_SPECS" in text
        assert "No demos configured" in text  # nothing is built in

    def test_plan_lists_configured_demos(self, demo_specs):
        text = _exec(build_demo_tools(None), "demo_plan", demo="all")
        assert "alpha" in text and "beta" in text
        assert "ALPHA_BUNDLE_DIR" in text  # env names derive from the demo name

    def test_specs_dir_placeholder_resolves(self, demo_specs):
        workflow = demo_specs / "alpha" / "workflow"
        workflow.mkdir(parents=True)
        text = _exec(build_demo_tools(None), "demo_plan", demo="first")
        assert f"workflow bundle: {workflow}" in text

    def test_plan_rejects_unknown(self, demo_specs):
        text = _exec(build_demo_tools(None), "demo_plan", demo="nope")
        assert "Unknown demo" in text

    def test_mutation_requires_client(self):
        names = {t["name"] for t in build_demo_tools(None)}
        assert "demo_plan" in names
        assert "demo_prepare" not in names

    def test_prepare_upload_stage_trigger(self, demo_specs, tmp_path, monkeypatch):
        bundle = _bundle(tmp_path)
        monkeypatch.setenv("ALPHA_BUNDLE_DIR", str(bundle))
        videos = tmp_path / "vids"
        videos.mkdir()
        (videos / "cam_a.mp4").write_bytes(b"x")
        monkeypatch.setenv("ALPHA_VIDEO_DIR", str(videos))
        client = _FakeClient()
        tools = build_demo_tools(client)

        assert "wf-test" in _exec(tools, "demo_prepare", demo="alpha", action="upload_bundle")
        assert "cam_a.mp4" in _exec(tools, "demo_prepare", demo="alpha", action="stage")
        out = _exec(
            tools,
            "demo_prepare",
            demo="alpha",
            action="trigger",
            input_key="k1",
            project_id="p9",
        )
        assert "exec-1" in out
        assert ("trigger", "wf-test", ["k1"], None, "p9") in client.workflows.calls

    def test_prepare_unknown_demo(self, demo_specs):
        client = _FakeClient()
        out = _exec(build_demo_tools(client), "demo_prepare", demo="nope", action="stage")
        assert "Unknown demo" in out

    def test_prepare_bad_action(self, demo_specs):
        client = _FakeClient()
        out = _exec(build_demo_tools(client), "demo_prepare", demo="beta", action="explode")
        assert "action must be one of" in out


class TestGapWrappers:
    def test_endpoint_upgrade(self):
        client = _FakeClient()
        tools = build_client_extra_tools(client)
        _exec(tools, "endpoint_upgrade", endpoint_id="ep-1", model_path="m2")
        assert client.upgraded == ("ep-1", "m2")

    def test_gate(self):
        client = _FakeClient()
        tools = build_client_extra_tools(client)
        _exec(tools, "workflow_approve_gate", execution_id="e1", step_name="s2", approved=False)
        assert ("gate", "e1", "s2", False) in client.workflows.calls

    def test_mcp_register_and_unregister(self):
        client = _FakeClient()
        tools = build_client_extra_tools(client)
        out = _exec(
            tools,
            "mcp_register_server",
            name="svc",
            url="http://mcp:8080",
            description="d",
        )
        assert "srv-1" in out
        assert client.mcp.registered == ("svc", "http://mcp:8080", "d", "none")
        out = _exec(tools, "mcp_unregister_server", server_id="srv-1")
        assert '"unregistered": true' in out
        assert client.mcp.unregistered == "srv-1"


class TestEndpointProvision:
    def test_full_fidelity_passthrough(self):
        client = _FakeClient()
        tools = get_builtin_tools(client)
        mounts = json.dumps({"mcp_servers": [{"source": "/host/x", "target": "/mcp-servers/x"}]})
        _exec(
            tools,
            "endpoint_provision",
            model="m1",
            mounts=mounts,
            egress_domains="a.example.com, b.example.org",
            max_model_len=8192,
            gpu_count=2,
            node_count=1,
            partition="gpu",
            ttl_sec=0,
            wait=False,
        )
        sent = client.provisioned
        assert sent["mounts"]["mcp_servers"][0]["target"] == "/mcp-servers/x"
        assert sent["egress_domains"] == ["a.example.com", "b.example.org"]
        assert (sent["max_model_len"], sent["gpu_count"], sent["ttl_sec"]) == (8192, 2, 0)
        assert sent["partition"] == "gpu" and sent["wait"] is False

    def test_defaults_unchanged(self):
        client = _FakeClient()
        tools = get_builtin_tools(client)
        _exec(tools, "endpoint_provision", model="m1")
        sent = client.provisioned
        assert sent["tier"] == "user" and sent["mounts"] is None
        assert sent["egress_domains"] is None and sent["wait"] is True

    def test_bad_mounts_json_rejected(self):
        client = _FakeClient()
        tools = get_builtin_tools(client)
        out = _exec(tools, "endpoint_provision", model="m1", mounts="[1,2]")
        assert "Error" in out
        assert client.provisioned is None


class TestRegistryAndClassification:
    _ALWAYS = {
        "source_list",
        "source_add",
        "source_use",
        "source_remove",
        "pool_up",
        "pool_down",
        "demo_plan",
    }
    _CLIENT_ONLY = {
        "demo_prepare",
        "endpoint_upgrade",
        "workflow_approve_gate",
        "mcp_register_server",
        "mcp_unregister_server",
    }

    def test_no_client_registry(self):
        names = {t["name"] for t in get_builtin_tools(None)}
        assert self._ALWAYS <= names
        assert not (self._CLIENT_ONLY & names)

    def test_with_client_registry(self):
        names = {t["name"] for t in get_builtin_tools(_FakeClient())}
        assert (self._ALWAYS | self._CLIENT_ONLY) <= names

    def test_permissions_classification(self):
        from r4.companion.permissions import NEVER_AUTO_APPROVE, RISKY_TOOLS, SAFE_TOOLS

        assert {"source_list", "demo_plan"} <= SAFE_TOOLS
        risky_expected = {
            "demo_prepare",
            "endpoint_upgrade",
            "workflow_approve_gate",
            "mcp_register_server",
            "mcp_unregister_server",
            "pool_up",
            "pool_down",
            "source_add",
            "source_use",
            "source_remove",
            "endpoint_provision",
            "endpoint_stop",
            "job_cancel",
            # knowledge writes are durable — ADR-050 adds the session-history build
            "knowledge_ingest",
            "knowledge_build_from_context",
        }
        assert risky_expected <= RISKY_TOOLS
        assert not ({"source_list", "demo_plan"} & NEVER_AUTO_APPROVE)

    def test_demo_prep_skill_registered(self):
        from r4.companion.skills import SkillRegistry

        skill = SkillRegistry().get("demo_prep")
        assert skill is not None
        assert skill.matches("demo prep for the video pipeline")
        assert "demo_prepare" in skill.tool_names

    def test_inference_env_skill_registered(self):
        from r4.companion.skills import SkillRegistry

        skill = SkillRegistry().get("inference_env")
        assert skill is not None
        assert skill.matches("prepare a customized inference env")
        needed = {"endpoint_provision", "mcp_register_server", "rag_upload", "workflow_upload"}
        assert needed <= set(skill.tool_names)
