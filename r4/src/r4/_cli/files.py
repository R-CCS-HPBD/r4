"""``r4 files`` — Gateway file transfer surface.

Commands:

    r4 files upload PATH [--key KEY] [--purpose PURPOSE]    Upload one file
    r4 files upload DIR --bucket B --recursive [--key PFX]  Upload a folder to your bucket
    r4 files list --bucket B [--prefix P]                   List your bucket
    r4 files download KEY [-o PATH] [--bucket BUCKET]       Download one object
    r4 files download PFX --bucket B --recursive -o DIR     Download everything under PFX

These wrap the Phase-2l Gateway routes only — no presigned URLs, no direct
S3 access from clients. The upload lands under the active project namespace
(e.g. ``r4-data/<project>/<key>``); the download reads from any permitted
bucket (usually ``r4-data-output`` for workflow artifacts).
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from ..files import FilesClient
from .base import CommandHandler
from .registry import command


@command
class FilesCommand(CommandHandler):
    """``r4 files`` — upload/download files through the Gateway."""

    name = "files"
    help = "Upload and download files through the Gateway (project-namespace safe)"

    # ------------------------------------------------------------------
    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Transfer data files through the Gateway. Uploads land under "
                "the active project namespace (r4-data/<project>/<key>); "
                "downloads stream from any permitted bucket (usually "
                "r4-data-output for workflow artifacts)."
            ),
            epilog=(
                "Examples:\n"
                "  r4 files upload sample.mp4 --key clips/sample.mp4 --purpose video-feed\n"
                "  r4 files download runs/wf-xxx/outputs/results.csv -o results.csv "
                "--bucket r4-data-output\n"
                "  r4 files upload ./videos --bucket alice.example-project-b.videos --recursive\n"
                "  r4 files list --bucket alice.example-project-b.videos-output --prefix runs/\n"
                "  r4 files download runs/wf-xxx/ --bucket alice.example-project-b.videos-output "
                "--recursive -o results/\n"
            ),
        )
        sub = parser.add_subparsers(dest="files_command", metavar="<files_command>")

        up = sub.add_parser("upload", help="Upload one local file")
        up.add_argument("path", help="Local file path to upload")
        up.add_argument("--key", help="S3 key under the project (default: basename)")
        up.add_argument("--purpose", default="general", help="Purpose tag (default: general)")
        up.add_argument("--bucket", help="A bucket you own (r4 bucket list); keys keep your layout")
        up.add_argument(
            "--recursive",
            "-r",
            action="store_true",
            help="PATH is a folder: upload every file under it (needs --bucket; --key = prefix)",
        )
        up.add_argument("--json", "-j", action="store_true", help="Emit JSON")

        ls = sub.add_parser("list", help="List objects in a bucket you own")
        ls.add_argument("--bucket", required=True, help="Your bucket or its -output pair")
        ls.add_argument("--prefix", default="", help="Key prefix")
        ls.add_argument("--json", "-j", action="store_true", help="Emit JSON")

        dl = sub.add_parser("download", help="Download one object")
        dl.add_argument("key", help="S3 key to download")
        dl.add_argument("-o", "--output", help="Local destination (default: basename of key)")
        dl.add_argument(
            "--bucket",
            help="Bucket to read from (default upload bucket; outputs use r4-data-output)",
        )
        dl.add_argument(
            "--recursive",
            "-r",
            action="store_true",
            help="KEY is a prefix: download every object under it into -o DIR (needs --bucket)",
        )
        dl.add_argument(
            "--json", "-j", action="store_true", help="Emit JSON metadata only (no file write)"
        )

        return parser

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "files_command", None)
        if sub == "upload":
            return self._cmd_upload(args)
        if sub == "download":
            if getattr(args, "recursive", False):
                return self._cmd_download_tree(args)
            return self._cmd_download(args)
        if sub == "list":
            return self._cmd_list(args)
        print("Usage: r4 files upload|list|download (see --help)", file=sys.stderr)
        return 2

    # ------------------------------------------------------------------
    def _cmd_upload(self, args: Namespace) -> int:
        path = Path(args.path)
        if not path.exists():
            print(f"✗ File not found: {path}", file=sys.stderr)
            return 1
        if path.is_dir():
            return self._cmd_upload_tree(args, path)

        client = FilesClient()
        try:
            result = client.upload_file(
                path, key=args.key, purpose=args.purpose, bucket=getattr(args, "bucket", None)
            )
        except Exception as exc:  # noqa: BLE001 - surface the gateway error message
            print(f"✗ Upload failed: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0

        key = result.get("key", args.key or path.name)
        size = result.get("size") or path.stat().st_size
        uri = result.get("uri") or result.get("bucket", "?")
        print(f"✓ Uploaded {path.name} ({size} bytes) → {uri}")
        print(f"  key: {key}")
        return 0

    def _cmd_download(self, args: Namespace) -> int:
        dest = Path(args.output or Path(args.key).name)

        client = FilesClient()
        try:
            if args.json:
                data = client.download_file(args.key, bucket=args.bucket)
                meta = {"key": args.key, "bucket": args.bucket, "size": len(data), "dest": None}
                print(json.dumps(meta, indent=2))
                return 0
            # Streams to disk and creates missing parent folders (2026-10-02:
            # `-o videos/cam/x.mp4` failed, and the body was held in memory).
            client.download_file(args.key, dest=dest, bucket=args.bucket)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ Download failed: {exc}", file=sys.stderr)
            return 1
        print(f"✓ Downloaded {dest.stat().st_size} bytes → {dest}")
        return 0

    def _cmd_upload_tree(self, args: Namespace, root: Path) -> int:
        if not (getattr(args, "recursive", False) and getattr(args, "bucket", None)):
            print("✗ PATH is a folder: add --bucket <yours> --recursive", file=sys.stderr)
            return 2
        prefix = (args.key or "").strip("/")
        files = sorted(p for p in root.rglob("*") if p.is_file())
        client = FilesClient()
        failed = sum(
            not _upload_one(client, args.bucket, path, root, prefix, i, len(files))
            for i, path in enumerate(files, 1)
        )
        print(f"{len(files) - failed}/{len(files)} file(s) uploaded to {args.bucket}")
        return 1 if failed else 0

    def _cmd_list(self, args: Namespace) -> int:
        try:
            objects = FilesClient().list_objects(args.bucket, args.prefix)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ List failed: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(objects, indent=2))
            return 0
        for obj in objects:
            print(f"{obj.get('size', 0):>14}  {obj.get('key')}")
        print(f"{len(objects)} object(s) in {args.bucket}/{args.prefix}")
        return 0

    def _cmd_download_tree(self, args: Namespace) -> int:
        if not args.bucket:
            print("✗ --recursive needs --bucket <yours>", file=sys.stderr)
            return 2
        prefix = args.key
        dest_root = Path(args.output or ".")
        client = FilesClient()
        try:
            objects = client.list_objects(args.bucket, prefix)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ List failed: {exc}", file=sys.stderr)
            return 1
        failed = sum(
            not _download_one(
                client, args.bucket, str(obj.get("key")), prefix, dest_root, i, len(objects)
            )
            for i, obj in enumerate(objects, 1)
        )
        print(f"{len(objects) - failed}/{len(objects)} object(s) downloaded to {dest_root}")
        return 1 if failed else 0


def _upload_one(
    client: FilesClient, bucket: str, path: Path, root: Path, prefix: str, i: int, n: int
) -> bool:
    """Upload *path* at ``<prefix>/<path relative to root>``; False on failure."""
    rel = path.relative_to(root).as_posix()
    key = f"{prefix}/{rel}" if prefix else rel
    try:
        client.upload_file(path, key=key, bucket=bucket)
    except Exception as exc:  # noqa: BLE001 - keep going, report at the end
        print(f"✗ [{i}/{n}] {rel}: {exc}", file=sys.stderr)
        return False
    print(f"✓ [{i}/{n}] {rel} → s3://{bucket}/{key}")
    return True


def _download_one(
    client: FilesClient, bucket: str, key: str, prefix: str, dest_root: Path, i: int, n: int
) -> bool:
    """Mirror *key* (minus *prefix*) under *dest_root*; False on failure."""
    rel = key[len(prefix) :].lstrip("/") if key.startswith(prefix) else key
    if ".." in Path(rel).parts:
        print(f"✗ [{i}/{n}] {key}: refusing a key that leaves the target folder", file=sys.stderr)
        return False
    target = dest_root / (rel or Path(key).name)
    try:
        client.download_file(key, dest=target, bucket=bucket)
    except Exception as exc:  # noqa: BLE001 - keep going, report at the end
        print(f"✗ [{i}/{n}] {key}: {exc}", file=sys.stderr)
        return False
    print(f"✓ [{i}/{n}] {key} → {target}")
    return True


def cmd_files(args: Namespace) -> int:
    """Programmatic entry point used by aliases/tests."""
    return FilesCommand().handle(args)
