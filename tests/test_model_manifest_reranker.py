"""ADR-093 MD-3 (decided 2026-10-11): rccs stages bge-reranker-v2-m3 through
deploy/model_manifest.py.

The entry records what is staged; it never downloads (staging is a
MODEL_DOWNLOAD job / Ansible step), it names a staged DIRECTORY through an
env placeholder (the rag service refuses a hub id under HF_HUB_OFFLINE=1),
and staging it does not switch reranking on (UMRS_RAG_RERANK_DEFAULT=off).
The general manifest tests live in test_batch3_cor003_cor004_memb5_memc3.py;
this file needs only the manifest module.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from deploy import model_manifest as mm


def _rccs_reranker() -> mm.ModelManifestRef:
    (ref,) = [m for m in mm._PROFILE_MODELS["rccs"] if m.model_type == "reranker"]
    return ref


def test_rccs_stages_bge_reranker_v2_m3_as_a_directory():
    ref = _rccs_reranker()
    assert ref.name == "bge-reranker-v2-m3"
    assert ref.source == "filesystem_path"
    assert ref.path == "${RERANKER_MODEL_PATH}"
    assert "BAAI/bge-reranker-v2-m3" in ref.purpose  # the hub id staging uses
    assert "UMRS_RAG_RERANK_DEFAULT=off" in ref.purpose  # staged is not on
    assert ref.sha256 == ""  # filled by --lock after staging, never guessed


def test_the_existing_profiles_do_not_gain_the_reranker():
    """Adding it to hpc-full etc. would make their --verify fail on every
    site that does not stage it."""
    for profile, models in mm._PROFILE_MODELS.items():
        if profile != "rccs":
            assert all(m.model_type != "reranker" for m in models), profile


def test_rccs_list_is_offline_and_reports_unstaged(capsys):
    env = {k: v for k, v in os.environ.items() if k != "RERANKER_MODEL_PATH"}
    with patch.dict(os.environ, env, clear=True):
        assert mm.main(["--profile", "rccs", "--list"]) == 0
    out = capsys.readouterr().out
    assert "bge-reranker-v2-m3" in out and "[?]" in out  # unresolved, not fetched


def test_rccs_list_sees_a_staged_directory(tmp_path, capsys):
    staged = tmp_path / "bge-reranker-v2-m3"
    staged.mkdir()
    with patch.dict(os.environ, {"RERANKER_MODEL_PATH": str(staged)}):
        assert mm.cmd_list("rccs") == 0
    assert "[✓]" in capsys.readouterr().out
