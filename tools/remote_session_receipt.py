"""Live receipt for the remote-session chain (ADR-046 Amendment 2).

Run from a workstation whose r4 profile is logged in to the gateway:

    cd r4 && python ../tools/remote_session_receipt.py <source-name> [<model>]

push <source> (sealed server-side) → create + mirror a `frontend` session →
POST …/venue/start (answers the `provisioning` row; the durable
`frontend_session` job provisions) → wait for `running` → queue one turn
(owner-scope queue, what the TUI does on send) → poll GET …/venue until
the worker reports `idle` with turn_seq ≥ 1
(or error/exited) → read the adopted assistant row from the journal →
adopt that row into a scratch local store (what the TUI watcher and
reconcile do) → POST …/venue/stop → re-read the journal (the server-side
stop and teardown keep the reply; the TUI's own stop-time save is pinned by
r4/tests/test_bridge_venue_verbs.py, not exercised here). Prints references only (fingerprints, ids, phases) —
never the key, never the session token. Exit 0 = PASS, 7 = INCOMPLETE,
2–6 = the step that refused (see the log line).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r4.client import Client
from r4.companion.session_store import CompanionSessionStore, journal_prefix
from r4.source import get_source_store
from r4.user_sources import UserSourcesClient

PROMPT = "Reply with exactly the text REMOTE-OK and nothing else."
POLL_SEC = 5
MAX_WAIT_SEC = 900
START_WAIT_SEC = 300
TERMINAL_PHASES = ("idle", "error", "exited")


class StepRefused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {msg}", flush=True)


def err_text(exc: Exception) -> str:
    d = getattr(exc, "details", None) or {}
    body = str(d.get("response_body"))[:300]
    return f"{type(exc).__name__} status={d.get('status_code')} code={d.get('code')} body={body}"


@dataclass
class Receipt:
    client: Client
    source: str
    model: str
    prefix: str = ""
    session_id: str = ""
    final_phase: str = ""

    def __post_init__(self) -> None:
        self.prefix = journal_prefix(self.client)

    def _url(self, tail: str) -> str:
        return f"{self.prefix}/companion/sessions/{self.session_id}{tail}"

    # ── steps ──────────────────────────────────────────────────────────

    def push(self) -> None:
        src = get_source_store().get(self.source)
        if src is None or not src.api_key:
            raise StepRefused(2, f"source {self.source!r} missing or has no key")
        kind = src.provider or ("openai_compat" if src.kind == "public" else src.kind)
        try:
            pushed = UserSourcesClient(self.client).push(
                name=src.name, provider_kind=kind, base_url=src.url, api_key=src.api_key
            )
        except Exception as exc:  # noqa: BLE001 — reported, then refused
            raise StepRefused(3, f"push FAILED: {err_text(exc)}") from exc
        log(
            f"push ok: allow_host={pushed.get('allow_host')} "
            f"fingerprint={pushed.get('key_fingerprint')} replaced={pushed.get('replaced')}"
        )

    def create_session(self) -> None:
        store = CompanionSessionStore()
        name = f"remote-receipt {datetime.now(timezone.utc):%m-%d %H:%M}"
        session = store.create_session(
            self.client, name=name, venue="frontend", model=self.model or None
        )
        self.session_id = session.session_id
        store.set_mirror_poster(self.client.post, self.client)
        store.save(session)
        sync = store.sync_status()
        log(f"session {self.session_id} mirror={sync.get('mode')} err={sync.get('error') or '-'}")
        if sync.get("mode") != "mirrored":
            raise StepRefused(4, "session not mirrored — journal unreachable")

    def start(self) -> None:
        body = {"source_refs": [self.source], "web_policy": "public"}
        try:
            started = self.client.post(self._url("/venue/start"), json=body, timeout=300)
        except Exception as exc:  # noqa: BLE001
            raise StepRefused(5, f"venue/start FAILED: {err_text(exc)}") from exc
        self._log_row("venue/start", started)
        if started.get("state") not in ("running", "provisioning"):
            raise StepRefused(6, f"start answered {started.get('state')}")
        self._await_running(started)

    def _log_row(self, label: str, row: dict[str, Any]) -> None:
        lease = row.get("lease") or {}
        job = row.get("job") or {}
        log(
            f"{label}: state={row.get('state')} egress_ip={row.get('egress_ip')} "
            f"lease_valid={lease.get('is_valid')} sources={row.get('source_names')} "
            f"job={job.get('job_id')}/{job.get('status')}/{job.get('attempt_status')} "
            f"reason={row.get('reason') or '-'}"
        )

    def _await_running(self, row: dict[str, Any]) -> None:
        """The durable job provisions after the start answers; wait for it."""
        deadline = time.time() + START_WAIT_SEC
        while row.get("state") == "provisioning" and time.time() < deadline:
            time.sleep(POLL_SEC)
            try:
                row = self.client.get(self._url("/venue"), timeout=15)
            except Exception as exc:  # noqa: BLE001 — transient; keep waiting
                log(f"venue poll error: {err_text(exc)}")
                continue
            self._log_row("venue", row)
        if row.get("state") != "running":
            raise StepRefused(6, f"runtime did not reach running (state={row.get('state')})")

    def queue_turn(self) -> None:
        body = {"action": "append", "content": PROMPT, "client_id": "live-receipt"}
        queued = self.client.post(self._url("/queue"), json=body, timeout=15)
        log(f"queue append applied={queued.get('applied')} depth={len(queued.get('queue') or [])}")

    def _poll_line(self, row: dict[str, Any]) -> str:
        rt = row.get("runtime") or {}
        lease = row.get("lease") or {}
        return (
            f"state={row.get('state')} phase={rt.get('phase') or '-'} "
            f"turn_seq={rt.get('turn_seq', 0)} detail={str(rt.get('detail') or '-')[:80]} "
            f"partial_len={len(str(rt.get('partial') or ''))} lease_valid={lease.get('is_valid')}"
        )

    def _turn_settled(self, row: dict[str, Any]) -> bool:
        rt = row.get("runtime") or {}
        self.final_phase = str(rt.get("phase") or "")
        if row.get("state") in ("failed", "stopped"):
            return True
        return self.final_phase in TERMINAL_PHASES and int(rt.get("turn_seq") or 0) >= 1

    def poll(self, wait_sec: int = MAX_WAIT_SEC) -> None:
        deadline = time.time() + wait_sec
        last = ""
        while time.time() < deadline:
            time.sleep(POLL_SEC)
            try:
                row = self.client.get(self._url("/venue"), timeout=15)
            except Exception as exc:  # noqa: BLE001 — transient; keep polling
                log(f"venue poll error: {err_text(exc)}")
                continue
            line = self._poll_line(row)
            if line != last:
                log(line)
                last = line
            if self._turn_settled(row):
                return
        log("poll: deadline reached")

    def read_journal(self, label: str = "journal") -> tuple[str, dict[str, Any]]:
        detail = self.client.get(self._url(""), timeout=15)
        payload = json.loads(detail.get("payload") or "{}")
        messages = payload.get("messages") or []
        if isinstance(messages, str):
            messages = json.loads(messages)
        roles = [m.get("role") for m in messages]
        assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        log(
            f"{label}: venue={detail.get('venue')} state={detail.get('state')} "
            f"marker={detail.get('updated_at')} messages={roles}"
        )
        text = str(assistant.get("content"))[:200] if assistant else ""
        log(f"{label} assistant row: {text or '(none)'}")
        return text, detail

    def adopt_locally(self, detail: dict[str, Any]) -> str:
        """Adopt the journal row into a scratch store, as the TUI watcher and
        reconcile do; the adopted session's assistant text ("" on failure)."""
        with tempfile.TemporaryDirectory() as tmp:
            store = CompanionSessionStore(db_path=Path(tmp) / "sessions.db")
            try:
                if not store.adopt_remote_detail(self.session_id, detail):
                    log("adopt: refused (row mismatch or unparseable payload)")
                    return ""
                session = store.load(self.session_id, self.client)
            except Exception as exc:  # noqa: BLE001 — the receipt reports, never dies
                log(f"adopt FAILED: {type(exc).__name__}: {str(exc)[:200]}")
                return ""
        if session is None:
            log("adopt: row did not load")
            return ""
        assistant = next((m for m in reversed(session.messages) if m.role == "assistant"), None)
        text = str(assistant.content)[:200] if assistant else ""
        log(
            f"adopt: venue={session.metadata.get('venue')} marker={session.updated_at} "
            f"assistant={text or '(none)'}"
        )
        return text

    def stop(self) -> None:
        try:
            stopped = self.client.post(self._url("/venue/stop"), json={}, timeout=150)
            log(f"venue/stop: state={stopped.get('state')} reason={stopped.get('reason')}")
        except Exception as exc:  # noqa: BLE001
            log(f"venue/stop FAILED: {err_text(exc)}")


def run(source: str, model: str, *, keep: bool = False, wait_sec: int = MAX_WAIT_SEC) -> int:
    client = Client()
    receipt = Receipt(client=client, source=source, model=model)
    log(
        f"gateway={client.config.gateway_url} prefix={receipt.prefix} profile={client.config.profile}"
    )
    try:
        receipt.push()
        receipt.create_session()
        receipt.start()
        receipt.queue_turn()
    except StepRefused as exc:
        log(str(exc))
        return exc.code
    receipt.poll(wait_sec)
    assistant_text, detail = receipt.read_journal()
    adopted_text = receipt.adopt_locally(detail) if assistant_text else ""
    kept_text = assistant_text
    if keep:
        log(f"session kept for inspection (no stop): {receipt.session_id}")
    else:
        receipt.stop()
        kept_text, _ = receipt.read_journal("after stop")
    ok = (
        bool(assistant_text)
        and adopted_text == assistant_text
        and kept_text == assistant_text
        and receipt.final_phase == "idle"
    )
    log(f"RECEIPT {'PASS' if ok else 'INCOMPLETE'} session={receipt.session_id}")
    return 0 if ok else 7


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", default="rikyu")
    parser.add_argument("model", nargs="?", default="")
    parser.add_argument("--keep", action="store_true", help="leave the sandbox up (no venue/stop)")
    parser.add_argument("--wait", type=int, default=MAX_WAIT_SEC, help="turn poll deadline (s)")
    args = parser.parse_args(argv[1:])
    return run(args.source, args.model, keep=args.keep, wait_sec=args.wait)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
