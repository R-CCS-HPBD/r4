"""Tests for profile manifest generator (COR-001).

Validates:
- All profiles from the matrix generate valid manifests
- Manifest contents: env vars, checklist, smoke commands, rollback
- Output formats: JSON, Markdown, env template
- Error handling for unknown profiles
- Round-trip: generated manifest can be serialized and re-read
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

# tools is importable from the repo root (ADR-053); no path splice needed.
from tools.profile_manifest_generator import (
    ChecklistItem,
    EnvVar,
    generate_all_manifests,
    generate_manifest,
    list_profiles,
    load_matrix,
    main,
    write_manifest,
)


class TestLoadMatrix(unittest.TestCase):
    """Test matrix loading."""

    def test_load_default_matrix(self):
        data = load_matrix()
        self.assertIn("entries", data)
        self.assertIn("blessed_envelopes", data)
        self.assertGreater(len(data["entries"]), 0)

    def test_matrix_has_expected_profiles(self):
        data = load_matrix()
        profiles = {e["profile"] for e in data["entries"]}
        self.assertIn("hpc-full", profiles)
        self.assertIn("k8s-cluster", profiles)
        self.assertIn("development", profiles)


class TestListProfiles(unittest.TestCase):
    """Test profile listing."""

    def test_list_profiles(self):
        profiles = list_profiles()
        self.assertIn("hpc-full", profiles)
        self.assertIn("k8s-cluster", profiles)
        self.assertIn("development", profiles)
        # Should be sorted
        self.assertEqual(profiles, sorted(profiles))


class TestGenerateManifest(unittest.TestCase):
    """Test manifest generation for individual profiles."""

    def test_hpc_full_manifest(self):
        m = generate_manifest("hpc-full")
        self.assertEqual(m.profile, "hpc-full")
        self.assertEqual(m.envelope, "hpc")
        self.assertIn("supported", m.support_level)
        self.assertGreater(len(m.entries), 0)
        self.assertGreater(len(m.env_vars), 0)
        self.assertGreater(len(m.smoke_commands), 0)
        self.assertGreater(len(m.rollback_procedures), 0)
        self.assertGreater(len(m.required_services), 0)
        self.assertGreater(len(m.checklist), 0)

    def test_k8s_cluster_manifest(self):
        m = generate_manifest("k8s-cluster")
        self.assertEqual(m.envelope, "k8s")
        descriptions = [c.description for c in m.checklist]
        self.assertTrue(
            any("kubectl" in d for d in descriptions),
            "K8s profile should have kubectl checklist item",
        )

    def test_dev_compose_manifest(self):
        m = generate_manifest("development")
        self.assertEqual(m.envelope, "dev-server")

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError) as ctx:
            generate_manifest("nonexistent-profile")
        self.assertIn("Unknown profile", str(ctx.exception))

    def test_env_vars_include_required(self):
        m = generate_manifest("hpc-full")
        required = [v for v in m.env_vars if v.required]
        required_names = {v.name for v in required}
        self.assertIn("UMRS_DB_URL", required_names)
        self.assertIn("VLLM_MODEL_PATH", required_names)
        self.assertIn("SLURM_PARTITION", required_names)

    def test_secrets_are_marked(self):
        m = generate_manifest("hpc-full")
        secret_vars = [v for v in m.env_vars if v.secret]
        secret_names = {v.name for v in secret_vars}
        self.assertIn("LITELLM_MASTER_KEY", secret_names)
        self.assertIn("UMRS_S3_SECRET_KEY", secret_names)

    def test_checklist_has_common_items(self):
        m = generate_manifest("hpc-full")
        categories = {c.category for c in m.checklist}
        self.assertIn("Environment", categories)
        self.assertIn("Database", categories)

    def test_checklist_has_profile_items(self):
        m = generate_manifest("hpc-full")
        descriptions = [c.description for c in m.checklist]
        self.assertTrue(
            any("Slurm" in d for d in descriptions),
            "HPC profile should have Slurm checklist item",
        )
        self.assertTrue(
            any("Model" in d for d in descriptions),
            "HPC profile should have Model checklist item",
        )

    def test_readiness_gates(self):
        m = generate_manifest("hpc-full")
        self.assertGreater(len(m.readiness_gates), 0)

    def test_services_are_sorted(self):
        m = generate_manifest("hpc-full")
        self.assertEqual(m.required_services, sorted(m.required_services))


class TestGenerateAllManifests(unittest.TestCase):
    """Test bulk manifest generation."""

    def test_generates_all(self):
        manifests = generate_all_manifests()
        profiles = list_profiles()
        self.assertEqual(set(manifests.keys()), set(profiles))

    def test_all_manifests_valid(self):
        manifests = generate_all_manifests()
        for name, m in manifests.items():
            self.assertEqual(m.profile, name)
            self.assertNotEqual(m.envelope, "")
            self.assertGreater(len(m.checklist), 0)


class TestManifestSerialization(unittest.TestCase):
    """Test manifest serialization to dict/JSON."""

    def test_to_dict(self):
        m = generate_manifest("hpc-full")
        d = m.to_dict()
        self.assertEqual(d["profile"], "hpc-full")
        self.assertEqual(d["envelope"], "hpc")
        self.assertIn("env_vars", d)
        self.assertIn("smoke_commands", d)
        self.assertIn("rollback_procedures", d)
        self.assertIn("checklist", d)
        self.assertIn("runtime_entries", d)

    def test_json_roundtrip(self):
        m = generate_manifest("hpc-full")
        d = m.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        self.assertEqual(loaded["profile"], "hpc-full")
        self.assertEqual(len(loaded["env_vars"]), len(m.env_vars))


class TestManifestMarkdown(unittest.TestCase):
    """Test Markdown output."""

    def test_markdown_has_sections(self):
        m = generate_manifest("hpc-full")
        md = m.to_markdown()
        self.assertIn("# Deployment Manifest: hpc-full", md)
        self.assertIn("## Required Services", md)
        self.assertIn("## Environment Variables", md)
        self.assertIn("## Pre-Deployment Checklist", md)
        self.assertIn("## Smoke Commands", md)
        self.assertIn("## Rollback Procedures", md)

    def test_markdown_has_env_vars(self):
        m = generate_manifest("hpc-full")
        md = m.to_markdown()
        self.assertIn("UMRS_DB_URL", md)
        self.assertIn("VLLM_MODEL_PATH", md)

    def test_markdown_secrets_noted(self):
        m = generate_manifest("hpc-full")
        md = m.to_markdown()
        self.assertIn("SECRET", md)


class TestEnvTemplate(unittest.TestCase):
    """Test .env template output."""

    def test_env_template_has_header(self):
        m = generate_manifest("hpc-full")
        env = m.to_env_template()
        self.assertIn("# Environment template", env)
        self.assertIn("hpc-full", env)

    def test_env_template_has_categories(self):
        m = generate_manifest("hpc-full")
        env = m.to_env_template()
        self.assertIn("PostgreSQL", env)
        self.assertIn("S3/MinIO", env)

    def test_env_template_secrets_commented(self):
        m = generate_manifest("hpc-full")
        env = m.to_env_template()
        # Secrets should be commented out
        self.assertIn("# LITELLM_MASTER_KEY=", env)
        self.assertIn("SECRET", env)


class TestWriteManifest(unittest.TestCase):
    """Test file output."""

    def test_write_all_formats(self):
        m = generate_manifest("development")
        with tempfile.TemporaryDirectory() as tmpdir:
            written = write_manifest(m, tmpdir, "all")
            self.assertEqual(len(written), 3)
            suffixes = {Path(f).suffix for f in written}
            self.assertIn(".json", suffixes)
            self.assertIn(".md", suffixes)
            # .env.template has compound extension
            self.assertTrue(
                any("template" in f for f in written),
                "Should write .env.template file",
            )

    def test_write_json_only(self):
        m = generate_manifest("development")
        with tempfile.TemporaryDirectory() as tmpdir:
            written = write_manifest(m, tmpdir, "json")
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].endswith(".json"))

    def test_written_json_valid(self):
        m = generate_manifest("hpc-full")
        with tempfile.TemporaryDirectory() as tmpdir:
            written = write_manifest(m, tmpdir, "json")
            with open(written[0]) as f:
                data = json.load(f)
            self.assertEqual(data["profile"], "hpc-full")


class TestCLI(unittest.TestCase):
    """Test CLI interface."""

    def test_list_profiles_cli(self):
        result = main(["--list"])
        self.assertEqual(result, 0)

    def test_profile_markdown_cli(self):
        result = main(["--profile", "hpc-full", "--format", "markdown"])
        self.assertEqual(result, 0)

    def test_profile_json_cli(self):
        result = main(["--profile", "development", "--format", "json"])
        self.assertEqual(result, 0)

    def test_profile_env_cli(self):
        result = main(["--profile", "k8s-cluster", "--format", "env"])
        self.assertEqual(result, 0)

    def test_unknown_profile_cli(self):
        result = main(["--profile", "nonexistent"])
        self.assertEqual(result, 1)

    def test_all_with_output_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--all", "--output", tmpdir, "--format", "all"])
            self.assertEqual(result, 0)
            files = list(Path(tmpdir).glob("*"))
            self.assertGreater(len(files), 0)


class TestEnvVarDataclass(unittest.TestCase):
    """Test EnvVar dataclass."""

    def test_to_dict_minimal(self):
        ev = EnvVar(name="FOO")
        d = ev.to_dict()
        self.assertEqual(d["name"], "FOO")
        self.assertTrue(d["required"])
        self.assertNotIn("description", d)

    def test_to_dict_full(self):
        ev = EnvVar(
            name="BAR",
            required=False,
            description="A bar",
            example="baz",
            secret=True,
        )
        d = ev.to_dict()
        self.assertFalse(d["required"])
        self.assertEqual(d["description"], "A bar")
        self.assertTrue(d["secret"])


class TestChecklistItem(unittest.TestCase):
    """Test ChecklistItem dataclass."""

    def test_to_dict(self):
        ci = ChecklistItem("Runtime", "Check docker", "docker version")
        d = ci.to_dict()
        self.assertEqual(d["category"], "Runtime")
        self.assertEqual(d["verification"], "docker version")

    def test_to_dict_no_verification(self):
        ci = ChecklistItem("Network", "Ports open")
        d = ci.to_dict()
        self.assertNotIn("verification", d)


if __name__ == "__main__":
    unittest.main()
