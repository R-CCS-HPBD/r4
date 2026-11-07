"""Client-side ingest ledger (idempotent video-feed runs).

One JSONL file per profile under ``~/.video_feed/manifest-<profile>.jsonl``
(unless overridden). Entries carry capture identity, dedupe signature, and
the platform linkage (uploaded S3 key + workflow execution id).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DIR = Path(os.environ.get("VIDEO_FEED_DIR", Path.home() / ".video_feed"))


class Manifest:
    def __init__(self, profile: str, root: str | Path | None = None):
        self.profile = profile
        self.root = Path(root) if root else DEFAULT_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"manifest-{profile}.jsonl"
        self.path.touch(exist_ok=True)

    # ------------------------------------------------------------------
    def entries(self) -> Iterator[dict[str, Any]]:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)

    def find(self, signature: str) -> dict[str, Any] | None:
        for e in self.entries():
            if e.get("sig") == signature:
                return e
        return None

    def append(self, entry: dict[str, Any]) -> None:
        entry = dict(entry)
        entry.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def stat(self) -> dict[str, int]:
        n = 0
        by_status: dict[str, int] = {}
        for e in self.entries():
            n += 1
            s = str(e.get("status", "unknown"))
            by_status[s] = by_status.get(s, 0) + 1
        return {"entries": n, **by_status}
