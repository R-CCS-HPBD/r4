"""Broker implementations over the r4 client stack (ADR-020 §1).

Reverse control: engines receive these, never raw credentials. LLM calls
route through r4 Client (platform route via gateway/LiteLLM). MCP tool
execution is fail-closed until the MCP broker wiring lands (registry of
allowlisted servers per run policy).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r4_contracts.team_engine.events import ActorRef, EventKind, TeamEvent
from r4_contracts.team_engine.results import ArtifactRef
from r4_contracts.team_engine.services import (
    ModelChunk,
    ModelRequest,
    ToolCallRequest,
    ToolCallResult,
    ToolDescriptor,
)
from r4_contracts.team_engine.spec import BudgetSpec
from r4_teamengine_spi.context import (
    ArtifactBroker,
    BudgetMeter,
    EventSink,
    MemoryBroker,
    ModelBroker,
    ToolBroker,
    WorkerBroker,
)
from r4_teamengine_spi.errors import BudgetExhausted


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def usage_tokens(usage: Any, key: str) -> int:
    """A token count from the client's ``Usage`` dataclass or a raw dict.

    ``Client.chat`` returns ``chat_types.Usage``; reading it as a dict failed
    every role turn after the model had answered (live 2026-10-09). One
    reader for every broker that meters a model reply (ADR-092 D-7).
    """
    if usage is None:
        return 0
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
    return int(value or 0)


class ClientModelBroker(ModelBroker):
    """Streams model output through the r4 Client (platform route)."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0

    def available_models(self) -> list[str]:
        try:
            models = self._client.list_models()
        except Exception:  # noqa: BLE001 — listing is best-effort
            return []
        return [m.get("id", "") if isinstance(m, dict) else str(m) for m in models]

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:  # type: ignore[override]
        system, body = _split_system(request.messages)
        response = self._client.chat(
            message=None if body else " ",
            messages=body,
            model=request.model,
            system=system or None,
            temperature=request.temperature if request.temperature is not None else 0.7,
            max_tokens=request.max_tokens,
        )
        text = getattr(response, "content", "") or ""
        self._account(response, text)
        yield ModelChunk(text=text, finish_reason="stop")

    def _account(self, response: Any, text: str) -> None:
        # ``Client.chat`` answers with the ``Usage`` dataclass, which has no
        # ``.get``; the dict-only read raised after the model had replied
        # (ADR-092 rollout fix).
        usage = getattr(response, "usage", None)
        self.llm_calls += 1
        self.input_tokens += usage_tokens(usage, "prompt_tokens")
        self.output_tokens += usage_tokens(usage, "completion_tokens") or len(text.split())


def _split_system(messages: tuple) -> tuple[str, list[dict]]:
    body = [dict(m) for m in messages]
    if body and body[0].get("role") == "system":
        return str(body[0].get("content", "")), body[1:]
    return "", body


class PolicyToolBroker(ToolBroker):
    """Fail-closed tool access. Allowlist comes from the run policy;
    until MCP session wiring lands, everything is denied by default."""

    def __init__(self, allow: list[ToolDescriptor] | None = None) -> None:
        self._allow = allow or []
        self.calls: list[ToolCallRequest] = []

    def list_tools(self) -> list[ToolDescriptor]:
        return list(self._allow)

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        known = {(t.server, t.name) for t in self._allow}
        if (request.server, request.tool) not in known:
            return ToolCallResult(
                call_id=request.call_id,
                ok=False,
                error="POLICY_DENIED: tool not allowlisted for this run",
            )
        return ToolCallResult(
            call_id=request.call_id,
            ok=False,
            error="POLICY_DENIED: MCP execution broker not yet wired (phase 2b)",
        )


class RunArtifactBroker(ArtifactBroker):
    """Stores artifacts under the run directory; publish is a marker file."""

    def __init__(self, run_dir: Path) -> None:
        self._dir = run_dir / "artifacts"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._count = 0

    async def put(self, name: str, data: bytes, media_type: str, *, producer: str) -> ArtifactRef:
        import hashlib

        self._count += 1
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"art-{self._count}-{digest[:12]}"
        (self._dir / f"{artifact_id}.bin").write_bytes(data)
        meta = ArtifactRef(
            artifact_id=artifact_id,
            name=name,
            media_type=media_type,
            producer=producer,
            content_hash=digest,
        )
        (self._dir / f"{artifact_id}.json").write_text(json.dumps(meta.__dict__, indent=2))
        return meta

    async def get(self, artifact_id: str) -> bytes:
        return (self._dir / f"{artifact_id}.bin").read_bytes()

    async def publish(self, ref: ArtifactRef) -> ArtifactRef:
        (self._dir / f"{ref.artifact_id}.published").write_text(utcnow())
        return ref


class TranscriptMemoryBroker(MemoryBroker):
    """Minimal run-local memory. UMRS context-pack wiring lands with the
    server-side TeamService (phase 2b)."""

    def __init__(self, run_dir: Path) -> None:
        self._path = run_dir / "memory.jsonl"
        self._items: list[dict[str, Any]] = []

    async def recall(
        self, query: str, *, scope: str = "project", limit: int = 8
    ) -> list[dict[str, Any]]:
        matches = [i for i in self._items if query.lower() in json.dumps(i).lower()]
        return (matches or self._items)[:limit]

    async def store(self, kind: str, content: dict[str, Any]) -> str:
        record = {"kind": kind, "content": content, "ts": utcnow()}
        self._items.append(record)
        with self._path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return f"mem-{len(self._items)}"


class TokenBudgetMeter(BudgetMeter):
    """The run's token ceiling (``BudgetSpec.max_tokens``).

    Plan agents run on worker threads and their brokers charge every model
    round here (ADR-092 D-7), so the read-modify-write sits under a lock:
    without it two parallel rounds can lose a charge and overrun the
    ceiling the engine relies on to refuse the next agent.
    """

    def __init__(self, budgets: BudgetSpec) -> None:
        self._budgets = budgets
        self._lock = threading.Lock()
        self.tokens = 0

    @property
    def spent(self) -> int:
        """Tokens charged so far (for result and progress reporting)."""
        with self._lock:
            return self.tokens

    def spend_tokens(self, n: int) -> None:
        ceiling = self._budgets.max_tokens
        with self._lock:
            self.tokens += n
            total = self.tokens
        if ceiling is not None and total > ceiling:
            raise BudgetExhausted(f"token budget {ceiling} exceeded ({total})")

    def remaining(self) -> dict[str, Any]:
        ceiling = self._budgets.max_tokens
        with self._lock:
            spent = self.tokens
        return {"max_tokens": None if ceiling is None else max(0, ceiling - spent)}


class NullWorkerBroker(WorkerBroker):
    """No distributed workers on the client-side runtime (phase 2b: UMRS)."""

    async def request_worker(self, profile: dict) -> str:
        raise NotImplementedError("distributed workers require the server-side runtime")

    async def release_worker(self, handle: str) -> None:
        raise NotImplementedError("distributed workers require the server-side runtime")


class FileEventSink(EventSink):
    """Assigns seq, appends events.jsonl (append-only), fans out to
    subscriber queues. Mirrors Phase 0 MemoryEventSink semantics.

    A resumed attempt reuses the run directory (ADR-092 D-8), so ``seq``
    continues from the lines already in ``events.jsonl`` instead of
    restarting at 0 and writing duplicate sequence numbers into one log.

    Call ``emit`` from the event-loop thread: subscriber queues are
    ``asyncio.Queue`` objects, which are not thread-safe. The lock keeps
    the seq counter and the file append atomic for the one stray emit
    that does arrive from a worker thread; it does not make the queues
    safe for one.
    """

    def __init__(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self._log = run_dir / "events.jsonl"
        self._lock = threading.Lock()
        self._seq = _line_count(self._log)
        self._subscribers: list[asyncio.Queue] = []
        self._events: list[TeamEvent] = []

    @property
    def events(self) -> list[TeamEvent]:
        return list(self._events)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self._events:
            queue.put_nowait(event)
        self._subscribers.append(queue)
        return queue

    def emit(self, kind: str, actor: tuple[str, str], payload: dict) -> TeamEvent:
        with self._lock:
            event = TeamEvent(
                seq=self._seq,
                run_id=self.run_id,
                ts=utcnow(),
                kind=EventKind(kind),
                actor=ActorRef(kind=actor[0], name=actor[1]),
                payload=payload,
            )
            self._seq += 1
            self._events.append(event)
            with self._log.open("a") as fh:
                fh.write(json.dumps(_event_to_json(event)) + "\n")
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait(event)
        return event


def _line_count(path: Path) -> int:
    """Non-empty lines of an existing log; 0 when it does not exist yet."""
    try:
        with path.open() as fh:
            return sum(1 for line in fh if line.strip())
    except FileNotFoundError:
        return 0


def _event_to_json(event: TeamEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "run_id": event.run_id,
        "ts": event.ts,
        "kind": event.kind.value,
        "actor": {"kind": event.actor.kind, "name": event.actor.name},
        "payload": event.payload,
    }
