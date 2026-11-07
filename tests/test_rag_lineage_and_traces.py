"""Tests for MEM-C1 (RAG lineage tracking) and COR-006 (traces CLI).

MEM-C1: Validates that the lineage metadata propagation in
compute_and_store_embedding injects the right keys into per-item metadata.

COR-006: Validates the r4 traces CLI command handler registration
and the traces route surface (UMRS router + NGINX edge).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# ``umrs`` must resolve from the submodule source tree, not the submodule
# directory itself (same requirement as the belt-wide PYTHONPATH).
# Path-splice removed (ADR-053): umrs resolves as a namespace package from the repo root.

# ===========================================================================
# MEM-C1: RAG lineage metadata propagation
# ===========================================================================


class _FakeModel:
    """Minimal model stub for compute_and_store_embedding tests."""

    model_name = "test-model-v1"

    def compute_embeddings(self, objects):
        return [[0.1] * 384 for _ in objects]


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason=(
        "umrs.inference.embeddings imports sentence_transformers at module "
        "level, which pulls the ML stack (torch); install umrs[server] or "
        "umrs/src/requirements.txt to exercise the lineage assertions"
    ),
)
class TestRAGLineageMetadata:
    """compute_and_store_embedding should inject lineage metadata."""

    def test_lineage_metadata_injected(self):
        """When indexing_job_id and model_version are given, metadata dicts
        should contain those keys after compute_and_store_embedding runs."""
        stored = {}

        def fake_store(
            data_base,
            embeddings,
            table_name,
            bucket_name,
            s3_keys,
            saved_s3_objects,
            modality_name,
            checksums,
            model_name,
            metadata_list=None,
        ):
            stored["metadata_list"] = metadata_list
            stored["model_name"] = model_name

        # Minimal mock for s3 connection and data loading

        # Patch the load_data_from_s3 function to avoid real S3
        import umrs.shared.storage.s3 as s3_mod

        original_load = s3_mod.load_data_from_s3

        def mock_load(s3_conn, bucket, keys, modality):
            objects = [b"fake_data" for _ in keys]
            metadata = [{} for _ in keys]
            checksums = ["abc123" for _ in keys]
            sizes = [8 for _ in keys]
            return objects, metadata, checksums, sizes, False

        s3_mod.load_data_from_s3 = mock_load

        try:
            from umrs.inference.embeddings import compute_and_store_embedding

            compute_and_store_embedding(
                path_to_bucket="test-bucket",
                s3_keys=["file1.txt", "file2.txt"],
                data_base=None,
                table_name="test_table",
                model=_FakeModel(),
                device="cpu",
                modality_name="image",
                s3_connection=None,
                output_bucket=None,
                store_fn=fake_store,
                indexing_job_id="job-lineage-001",
                model_version="1.2.3",
                model_path="/models/test-model",
            )

            assert "metadata_list" in stored
            for md in stored["metadata_list"]:
                assert md["indexing_job_id"] == "job-lineage-001"
                assert md["model_version"] == "1.2.3"
                assert md["model_path"] == "/models/test-model"
                assert md["embedding_dimension"] == 384
            assert stored["model_name"] == "test-model-v1"
        finally:
            s3_mod.load_data_from_s3 = original_load

    def test_no_lineage_when_not_provided(self):
        """When no lineage params are given, metadata should remain clean."""
        stored = {}

        def fake_store(
            data_base,
            embeddings,
            table_name,
            bucket_name,
            s3_keys,
            saved_s3_objects,
            modality_name,
            checksums,
            model_name,
            metadata_list=None,
        ):
            stored["metadata_list"] = metadata_list

        import umrs.shared.storage.s3 as s3_mod

        original_load = s3_mod.load_data_from_s3

        def mock_load(s3_conn, bucket, keys, modality):
            objects = [b"data"]
            metadata = [{}]
            checksums = ["abc"]
            sizes = [4]
            return objects, metadata, checksums, sizes, False

        s3_mod.load_data_from_s3 = mock_load

        try:
            from umrs.inference.embeddings import compute_and_store_embedding

            compute_and_store_embedding(
                path_to_bucket="bucket",
                s3_keys=["f.txt"],
                data_base=None,
                table_name="t",
                model=_FakeModel(),
                device="cpu",
                modality_name="image",
                s3_connection=None,
                output_bucket=None,
                store_fn=fake_store,
            )

            assert "metadata_list" in stored
            for md in stored["metadata_list"]:
                assert "indexing_job_id" not in md
                assert "model_version" not in md
        finally:
            s3_mod.load_data_from_s3 = original_load


# ===========================================================================
# COR-006: CLI structure and Gateway route module
# ===========================================================================


class TestTracesCommandRegistration:
    """r4 traces CLI command should be discoverable."""

    def test_traces_command_exists(self):
        """TracesCommand should be importable and have correct name."""
        from r4._cli.traces import TracesCommand

        cmd = TracesCommand()
        assert cmd.name == "traces"
        assert cmd.help

    def test_traces_subcommands_registered(self):
        """All four subcommands should be registered."""
        from argparse import ArgumentParser

        from r4._cli.traces import TracesCommand

        root = ArgumentParser()
        sub = root.add_subparsers()
        cmd = TracesCommand()
        parser = cmd.register(sub)
        # Parser should exist and be usable
        assert parser is not None

    def test_traces_list_requires_job_or_execution(self):
        """list subcommand needs --job or --execution (mutually exclusive)."""
        from argparse import ArgumentParser

        from r4._cli.traces import TracesCommand

        root = ArgumentParser()
        sub = root.add_subparsers()
        cmd = TracesCommand()
        cmd.register(sub)

        # Should fail without --job or --execution
        with pytest.raises(SystemExit):
            root.parse_args(["traces", "list"])


class TestTracesEdgeRoutes:
    """COR-006 traces surface at the edge.

    ``gateway/gateway/routes/traces.py`` was deleted with the whole Python
    gateway (ADR-014 Phases 3+4, commit 0aac3ec2, 2026-09-11). The same five
    endpoints now live on the UMRS router and are published at the edge by
    NGINX, so the assertions follow them there.
    """

    NGINX_CONF = os.path.join(
        os.path.dirname(__file__),
        "..",
        "gateway",
        "nginx",
        "conf.d",
        "server.conf",
    )

    def _traces_block(self) -> str:
        with open(self.NGINX_CONF) as fh:
            content = fh.read()
        start = content.index("location /gw/traces/ {")
        return content[start : content.index("\n    }", start)]

    def test_edge_proxies_traces_to_umrs(self):
        block = self._traces_block()
        assert "rewrite ^/gw/traces/(.*)$ /umrs/traces/$1 break;" in block
        assert "proxy_pass http://umrs" in block

    def test_edge_traces_route_requires_auth(self):
        # CONSTITUTION §2: every /gw/* route goes through auth_request.
        block = self._traces_block()
        assert "auth_request /auth/validate;" in block

    def test_umrs_router_has_routes(self):
        from umrs.api.traces import router

        assert router.prefix == "/umrs/traces"
        paths = [r.path for r in router.routes]
        assert "/umrs/traces/job/{job_id}" in paths
        assert "/umrs/traces/execution/{execution_id}" in paths
        assert "/umrs/traces/{trace_id}" in paths
        assert "/umrs/traces/{trace_id}/lineage" in paths
        assert "/umrs/traces/{trace_id}/children" in paths

    def test_umrs_traces_router_is_mounted(self):
        from umrs.api import traces_router

        assert traces_router.prefix == "/umrs/traces"
        sidecar = os.path.join(
            os.path.dirname(__file__),
            "..",
            "umrs",
            "src",
            "umrs",
            "server",
            "http_sidecar.py",
        )
        with open(sidecar) as fh:
            assert "include_router(traces_router)" in fh.read()


# ===========================================================================
# MEM-C1: Migration SQL structure tests
# ===========================================================================


class TestRAGLineageMigration:
    """Verify the migration SQL contains expected column definitions."""

    @pytest.fixture
    def migration_sql(self):
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "umrs",
            "src",
            "umrs",
            "migrations",
            "0041_rag_lineage_tracking.sql",
        )
        with open(path) as f:
            return f.read()

    def test_memory_items_columns(self, migration_sql):
        assert "document_source_uri" in migration_sql
        assert "document_source_type" in migration_sql

    def test_memory_vectors_columns(self, migration_sql):
        assert "model_version" in migration_sql
        assert "model_path" in migration_sql
        assert "embedding_dimension" in migration_sql
        assert "indexing_job_id" in migration_sql

    def test_indices_created(self, migration_sql):
        assert "idx_memory_items_source_type" in migration_sql
        assert "idx_memory_vectors_lineage" in migration_sql
        assert "idx_memory_vectors_model_version" in migration_sql

    def test_alter_table_not_create(self, migration_sql):
        """Migration should ALTER existing tables, not CREATE new ones."""
        assert "ALTER TABLE memory_items" in migration_sql
        assert "ALTER TABLE memory_vectors" in migration_sql
        assert "CREATE TABLE" not in migration_sql
