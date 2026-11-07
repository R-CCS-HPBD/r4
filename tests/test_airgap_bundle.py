"""Tests for COR-003 airgap packaging and bootstrap diagnostics."""

from __future__ import annotations

import json

# Add deploy/ to path so we can import airgap_bundle directly
from deploy import airgap_bundle


class TestImageRef:
    def test_full_ref_no_digest(self):
        img = airgap_bundle.ImageRef("library/postgres", "16-alpine")
        assert img.full_ref == "docker.io/library/postgres:16-alpine"

    def test_full_ref_with_digest(self):
        img = airgap_bundle.ImageRef("library/postgres", "16", digest="sha256:abc")
        assert "@sha256:abc" in img.full_ref


class TestProfileImages:
    def test_known_profiles(self):
        for profile in ("hpc-full", "hpc-minimal", "k8s-cluster", "dev-compose"):
            assert profile in airgap_bundle._PROFILE_IMAGES
            images = airgap_bundle._PROFILE_IMAGES[profile]
            assert len(images) > 0

    def test_hpc_full_has_postgres(self):
        images = airgap_bundle._PROFILE_IMAGES["hpc-full"]
        names = [img.name for img in images]
        assert "library/postgres" in names

    def test_hpc_minimal_subset(self):
        minimal = len(airgap_bundle._PROFILE_IMAGES["hpc-minimal"])
        full = len(airgap_bundle._PROFILE_IMAGES["hpc-full"])
        assert minimal < full


class TestCmdList:
    def test_valid_profile(self, capsys):
        rc = airgap_bundle.cmd_list("hpc-full")
        assert rc == 0
        out = capsys.readouterr().out
        assert "postgres" in out

    def test_unknown_profile(self):
        rc = airgap_bundle.cmd_list("nonexistent")
        assert rc == 1


class TestCmdLock:
    def test_creates_lock_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(airgap_bundle, "ROOT", tmp_path)
        (tmp_path / "deploy").mkdir()

        rc = airgap_bundle.cmd_lock("hpc-full")
        assert rc == 0

        lock_file = tmp_path / "deploy" / "airgap-hpc-full.lock.json"
        assert lock_file.exists()

        data = json.loads(lock_file.read_text())
        assert data["profile"] == "hpc-full"
        assert len(data["images"]) > 0

    def test_unknown_profile(self):
        rc = airgap_bundle.cmd_lock("nonexistent")
        assert rc == 1


class TestImageManifest:
    def test_round_trip(self, tmp_path):
        manifest = airgap_bundle.ImageManifest(
            profile="test",
            images=[{"name": "img1", "tag": "v1"}],
        )
        path = tmp_path / "test.lock.json"
        path.write_text(manifest.to_json())

        loaded = airgap_bundle.ImageManifest.from_file(path)
        assert loaded.profile == "test"
        assert len(loaded.images) == 1


class TestCmdDiagnose:
    def test_diagnose_runs(self, capsys):
        _rc = airgap_bundle.cmd_diagnose()
        out = capsys.readouterr().out
        assert "Airgap Bootstrap Diagnostics" in out
        assert "Python:" in out


class TestCmdVerify:
    def test_missing_bundle(self):
        rc = airgap_bundle.cmd_verify("/nonexistent/bundle.tar")
        assert rc == 1

    def test_verify_existing_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(airgap_bundle, "ROOT", tmp_path)
        deploy = tmp_path / "deploy"
        deploy.mkdir()

        # Create a dummy lock file
        lock = deploy / "airgap-test.lock.json"
        manifest = airgap_bundle.ImageManifest(profile="test", images=[])
        lock.write_text(manifest.to_json())

        # Create a dummy bundle
        bundle = tmp_path / "bundle.tar"
        bundle.write_bytes(b"dummy")

        rc = airgap_bundle.cmd_verify(str(bundle))
        assert rc == 0
        out = capsys.readouterr().out
        assert "sha256:" in out
