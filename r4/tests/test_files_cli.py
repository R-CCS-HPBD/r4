"""Regression tests for `r4 files upload/download` CLI verbs.

The files surface is the Phase-2l canonical data transfer path:
everything goes through the Gateway HTTP routes—no presigned URLs, no
direct S3 access from clients.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from r4._cli.files import FilesCommand, cmd_files


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ----------------------------------------------------------------------
# FilesClient smoke (mocked HTTP)
# ----------------------------------------------------------------------


def test_files_client_upload(tmp_path):
    """FilesClient.upload_file posts multipart to /gw/files/upload."""
    from r4.files import FilesClient

    fixture = tmp_path / "clip.mp4"
    fixture.write_bytes(b"fake video bytes")

    client = FilesClient()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "key": "default/clips/clip.mp4",
        "bucket": "r4-data",
        "uri": "s3://r4-data/default/clips/clip.mp4",
        "size": 16,
    }

    with (
        patch.object(client._http, "_client") as client_ctx,
        patch.object(client._http, "build_headers", return_value={}),
    ):
        ctx = client_ctx.return_value.__enter__.return_value
        ctx.post.return_value = mock_response

        # Mock raise_for_status to avoid httpx internals on MagicMock
        with patch("r4.files.raise_for_status"):
            result = client.upload_file(fixture, key="clips/clip.mp4", purpose="video")

    assert result["key"] == "default/clips/clip.mp4"
    ctx.post.assert_called_once()
    assert "/gw/files/upload" in ctx.post.call_args[0][0]


def test_files_client_download_writes_file(tmp_path):
    """FilesClient.download_file streams via GET and writes dest."""
    from r4.files import FilesClient

    client = FilesClient()
    mock_response = MagicMock()
    mock_response.is_error = False
    mock_response.iter_bytes.return_value = [b"raw ", b"bytes"]

    with (
        patch.object(client._http, "_client") as client_ctx,
        patch.object(client._http, "build_headers", return_value={}),
        patch("r4.files.raise_for_status"),
        patch("r4.files.raise_if_web_login"),
    ):
        ctx = client_ctx.return_value.__enter__.return_value
        ctx.stream.return_value.__enter__.return_value = mock_response

        dest = tmp_path / "sub" / "out.bin"
        result = client.download_file("some/key", dest=dest, bucket="r4-data-output")

    assert result is None
    assert dest.read_bytes() == b"raw bytes"  # streamed to disk, parent created
    assert not (tmp_path / "sub" / "out.bin.part").exists()
    ctx.stream.assert_called_once()
    assert "/gw/files/download/some/key" in ctx.stream.call_args[0][1]


# ----------------------------------------------------------------------
# CLI wiring (mocked FilesClient)
# ----------------------------------------------------------------------


def test_cmd_files_upload_success(capsys):
    """cmd_files upload reports success."""
    result_payload = {
        "key": "default/clips/sample.mp4",
        "uri": "s3://r4-data/default/clips/sample.mp4",
        "size": 100,
    }
    with (
        patch("r4._cli.files.FilesClient") as mock_cls,
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.is_dir", return_value=False),
        patch("pathlib.Path.stat") as mock_stat,
    ):
        mock_stat.return_value.st_size = 100
        mock_cls.return_value.upload_file.return_value = result_payload

        code = cmd_files(
            Namespace(
                files_command="upload",
                path="sample.mp4",
                key="clips/sample.mp4",
                purpose="video",
                json=False,
            )
        )
    assert code == 0
    out = capsys.readouterr().out
    assert "Uploaded" in out
    assert "clips/sample.mp4" in out


def test_cmd_files_upload_missing_file(capsys):
    """cmd_files upload fails cleanly on missing local file."""
    with patch("pathlib.Path.exists", return_value=False):
        code = cmd_files(
            Namespace(
                files_command="upload",
                path="nope.mp4",
                key=None,
                purpose="general",
                json=False,
            )
        )
    assert code == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_cmd_files_download_defaults_to_basename(tmp_path, capsys):
    """cmd_files download defaults output to basename of key."""

    def _write(key, *, dest=None, bucket=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"artifact bytes")

    with patch("r4._cli.files.FilesClient") as mock_cls:
        mock_cls.return_value.download_file.side_effect = _write
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            # Use chdir so Path(dest) resolves in tmp_path
            import os

            prev = os.getcwd()
            os.chdir(tmp_path)
            try:
                code = cmd_files(
                    Namespace(
                        files_command="download",
                        key="runs/wf-123/outputs/results.csv",
                        output=None,
                        bucket="r4-data-output",
                        json=False,
                    )
                )
            finally:
                os.chdir(prev)

    assert code == 0
    out = capsys.readouterr().out
    assert "results.csv" in out
    assert (tmp_path / "results.csv").read_bytes() == b"artifact bytes"


def test_cmd_files_download_json_mode(capsys):
    """--json emits metadata without writing a file."""
    with patch("r4._cli.files.FilesClient") as mock_cls:
        mock_cls.return_value.download_file.return_value = b"x" * 42
        code = cmd_files(
            Namespace(
                files_command="download",
                key="some/key",
                output=None,
                bucket=None,
                json=True,
            )
        )
    assert code == 0
    out = capsys.readouterr().out
    import json

    data = json.loads(out)
    assert data["size"] == 42
    assert data["key"] == "some/key"


def test_cmd_files_unknown_action(capsys):
    """Unknown verb prints usage and exits 2."""
    code = cmd_files(Namespace(files_command="nope"))
    assert code == 2


def test_files_command_handler_is_commandhandler():
    """FilesCommand registers as a CommandHandler subclass."""
    from r4._cli.base import CommandHandler

    assert issubclass(FilesCommand, CommandHandler)
    assert FilesCommand.name == "files"


# ----------------------------------------------------------------------
# ADR-079: owned buckets (create/list), --bucket, list, recursive transfers
# ----------------------------------------------------------------------


def _json_client(responses):
    from r4.files import FilesClient

    client = FilesClient()
    calls = []

    def fake_json(method, path, *, params=None, body=None, timeout=None):
        calls.append((method, path, params, body))
        return responses.pop(0)

    client._json = fake_json  # type: ignore[method-assign]
    return client, calls


def test_create_and_list_buckets_hit_the_gateway_routes():
    client, calls = _json_client(
        [{"bucket": "alice.lab.videos"}, {"buckets": [{"bucket": "alice.lab.videos"}]}]
    )
    assert client.create_bucket("lab", "videos")["bucket"] == "alice.lab.videos"
    assert client.list_buckets() == [{"bucket": "alice.lab.videos"}]
    assert calls[0][:2] == ("POST", "/gw/buckets") and calls[0][3] == {
        "project": "lab",
        "alias": "videos",
    }
    assert calls[1][:2] == ("GET", "/gw/buckets")


def test_list_objects_follows_every_page():
    client, calls = _json_client(
        [
            {"objects": [{"key": "a"}], "next_token": "t1"},
            {"objects": [{"key": "b"}], "next_token": None},
        ]
    )
    assert [o["key"] for o in client.list_objects("alice.lab.videos", "runs/")] == ["a", "b"]
    assert calls[1][2] == {"bucket": "alice.lab.videos", "prefix": "runs/", "token": "t1"}


def test_upload_with_bucket_sends_it_and_a_long_timeout(tmp_path):
    from r4.files import FilesClient

    f = tmp_path / "w.pt"
    f.write_bytes(b"x")
    client = FilesClient()
    with (
        patch.object(client._http, "_client") as client_ctx,
        patch.object(client._http, "build_headers", return_value={}),
        patch("r4.files.raise_for_status"),
        patch("r4.files.raise_if_web_login"),
    ):
        ctx = client_ctx.return_value.__enter__.return_value
        ctx.post.return_value.json.return_value = {"key": "models/w.pt"}
        client.upload_file(f, key="models/w.pt", bucket="alice.lab.videos")
    assert ctx.post.call_args.kwargs["data"] == {
        "purpose": "general",
        "key": "models/w.pt",
        "bucket": "alice.lab.videos",
    }
    assert client_ctx.call_args[0][0] == 7200.0


def test_recursive_upload_keeps_the_folder_layout(tmp_path, capsys):
    root = tmp_path / "clips"
    (root / "cam_A").mkdir(parents=True)
    (root / "cam_A" / "1.mp4").write_bytes(b"1")
    (root / "2.mp4").write_bytes(b"2")
    with patch("r4._cli.files.FilesClient") as mock_cls:
        code = cmd_files(
            Namespace(
                files_command="upload",
                path=str(root),
                key="clips_",
                purpose="general",
                bucket="alice.lab.videos",
                recursive=True,
                json=False,
            )
        )
    assert code == 0
    keys = sorted(c.kwargs["key"] for c in mock_cls.return_value.upload_file.call_args_list)
    assert keys == ["clips_/2.mp4", "clips_/cam_A/1.mp4"]


def test_folder_upload_without_bucket_is_refused(tmp_path):
    (tmp_path / "d").mkdir()
    code = cmd_files(
        Namespace(
            files_command="upload",
            path=str(tmp_path / "d"),
            key=None,
            purpose="general",
            bucket=None,
            recursive=True,
            json=False,
        )
    )
    assert code == 2


def test_recursive_download_mirrors_keys_under_the_prefix(tmp_path):
    with patch("r4._cli.files.FilesClient") as mock_cls:
        mock_cls.return_value.list_objects.return_value = [
            {"key": "runs/wf-1/agg/results.tsv"},
            {"key": "runs/wf-1/cnn/a.csv"},
        ]
        code = cmd_files(
            Namespace(
                files_command="download",
                key="runs/wf-1/",
                output=str(tmp_path),
                bucket="alice.lab.videos-output",
                recursive=True,
                json=False,
            )
        )
    assert code == 0
    dests = sorted(
        str(c.kwargs["dest"]) for c in mock_cls.return_value.download_file.call_args_list
    )
    assert dests == [str(tmp_path / "agg/results.tsv"), str(tmp_path / "cnn/a.csv")]


def test_bucket_cli_create_and_list(capsys):
    from r4._cli.bucket import cmd_bucket

    with patch("r4._cli.bucket.FilesClient") as mock_cls:
        mock_cls.return_value.create_bucket.return_value = {
            "bucket": "alice.lab.videos",
            "output_bucket": "alice.lab.videos-output",
        }
        assert (
            cmd_bucket(
                Namespace(bucket_command="create", project="lab", alias="videos", json=False)
            )
            == 0
        )
        mock_cls.return_value.list_buckets.return_value = []
        assert cmd_bucket(Namespace(bucket_command="list", json=False)) == 0
    out = capsys.readouterr().out
    assert "alice.lab.videos-output" in out and "r4 bucket create" in out


def test_single_download_streams_to_a_nested_destination(tmp_path, capsys):
    # Regression: `-o videos/cam/x.mp4` failed (no parent folder) and the
    # body was held in memory; the CLI now hands dest to the streaming client.
    dest = tmp_path / "videos" / "session_01_A" / "x.mp4"

    def _write(key, *, dest=None, bucket=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"video")

    with patch("r4._cli.files.FilesClient") as mock_cls:
        mock_cls.return_value.download_file.side_effect = _write
        code = cmd_files(
            Namespace(
                files_command="download",
                key="session_01_A/x.mp4",
                output=str(dest),
                bucket="alice.lab.v",
                json=False,
                recursive=False,
            )
        )
    assert code == 0 and dest.read_bytes() == b"video"
    call = mock_cls.return_value.download_file.call_args
    assert call.kwargs["dest"] == dest and call.kwargs["bucket"] == "alice.lab.v"
    assert "5 bytes" in capsys.readouterr().out
