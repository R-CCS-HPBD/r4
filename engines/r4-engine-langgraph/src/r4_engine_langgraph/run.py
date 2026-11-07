"""LGRun: builds and drives the StateGraph; brokers for every effect.

LLM calls go through the ModelBroker (metered). Events translate to
canonical TeamEvents via the run's EventSink. MemorySaver powers
same-process checkpoint/resume (ENGINE_INTERNAL scope).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, TypedDict

from r4_contracts.team_engine.events import TERMINAL_EVENT_KINDS, EventKind, TeamEvent
from r4_contracts.team_engine.results import (
    CheckpointRef,
    ExecutionGraph,
    NodeState,
    RunMetrics,
    TeamResult,
)
from r4_contracts.team_engine.services import ModelRequest
from r4_contracts.team_engine.spec import AgentSpec
from r4_teamengine_spi.context import RuntimeServices
from r4_teamengine_spi.engine import EngineRun
from r4_teamengine_spi.errors import BudgetExhausted, IncompatibleCheckpoint

from .engine import LangGraphEngine, LangGraphPlan, _adjacency

_TERMINAL = ("done", "failed", "cancelled")


class LGState(TypedDict, total=False):
    results: dict[str, str]
    llm_calls: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LGRun(EngineRun):
    def __init__(self, plan: LangGraphPlan, services: RuntimeServices) -> None:
        self._plan = plan
        self._svc = services
        self._status = "pending"
        self._graph: Any = None
        self._saver: Any = None
        self._task: asyncio.Task | None = None
        self._result: TeamResult | None = None
        self._llm_calls = 0
        self._checkpoints = 0
        self._tokens_charged = 0
        self._completed: set[str] = set()

    @property
    def run_id(self) -> str:
        return self._svc.run.run_id

    # ---- graph construction -------------------------------------------------

    def build_graph(self) -> None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(LGState)
        for agent in self._plan.spec.agents:
            builder.add_node(agent.name, self._make_node(agent))
        adjacency = _adjacency(self._plan.spec)
        predecessors = {t for targets in adjacency.values() for t in targets}
        for agent in self._plan.spec.agents:
            if agent.name not in predecessors:
                builder.add_edge(START, agent.name)
            for target in adjacency.get(agent.name, ()):
                builder.add_edge(agent.name, target)
            if not adjacency.get(agent.name):
                builder.add_edge(agent.name, END)
        self._saver = MemorySaver()
        self._graph = builder.compile(checkpointer=self._saver)

    def _make_node(self, agent: AgentSpec):
        async def node(state: LGState) -> dict[str, Any]:
            events = self._svc.events
            events.emit(EventKind.AGENT_STARTED.value, ("agent", agent.name), {})
            text = await self._invoke_model(agent, state)
            events.emit(EventKind.AGENT_COMPLETED.value, ("agent", agent.name), {})
            self._completed.add(agent.name)
            self._spend()
            results = dict(state.get("results", {}))
            results[agent.name] = text
            return {"results": results, "llm_calls": self._llm_calls}

        return node

    async def _invoke_model(self, agent: AgentSpec, state: LGState) -> str:
        prior = state.get("results", {})
        context = "\n".join(f"[{k}] {v}" for k, v in prior.items()) or "(first node)"
        request = ModelRequest(
            messages=(
                {"role": "system", "content": agent.instructions},
                {
                    "role": "user",
                    "content": f"Objective: {self._plan.spec.task.objective}\nPrior:\n{context}",
                },
            ),
            model=agent.model_ref,
            seed=self._svc.run.seed,
        )
        self._llm_calls += 1
        parts: list[str] = []
        async for chunk in self._svc.models.stream(request):
            parts.append(chunk.text)
            self._svc.events.emit(
                EventKind.AGENT_MESSAGE.value, ("agent", agent.name), {"delta": chunk.text}
            )
        return "".join(parts)

    def _spend(self) -> None:
        tokens = _tokens_used(self._svc.models)
        delta = max(0, tokens - self._tokens_charged)
        if delta:
            self._tokens_charged = tokens
            self._svc.budget.spend_tokens(delta)

    # ---- driving ------------------------------------------------------------

    async def _execute(self) -> None:
        events = self._svc.events
        events.emit(
            EventKind.RUN_STARTED.value,
            ("engine", LangGraphEngine.NAME),
            {"plan_hash": self._plan.plan_hash},
        )
        self._status = "running"
        config = {"configurable": {"thread_id": self.run_id}}
        try:
            async for _ in self._graph.astream(_initial_state(), config=config):
                pass
            self._complete()
        except BudgetExhausted as exc:
            self._terminal_fail(TeamResult(status="failed", summary=str(exc), error=str(exc)))
        except asyncio.CancelledError:
            self._terminal_fail(TeamResult(status="cancelled", summary="cancelled"))
        except Exception as exc:  # noqa: BLE001 — adapter boundary
            self._terminal_fail(
                TeamResult(status="failed", summary=str(exc), error=f"engine_error: {exc}")
            )

    def _complete(self) -> None:
        self._status = "done"
        self._result = TeamResult(
            status="success",
            summary=f"langgraph run complete ({len(self._completed)} nodes)",
            metrics=self._metrics(),
            reproducibility={
                "seed": self._svc.run.seed,
                "engine_version": LangGraphEngine.VERSION,
                "spec_hash": self._plan.plan_hash,
            },
        )
        self._svc.events.emit(EventKind.RUN_COMPLETED.value, ("engine", LangGraphEngine.NAME), {})

    def _terminal_fail(self, result: TeamResult) -> None:
        self._status = "failed" if result.status == "failed" else "cancelled"
        self._result = result
        kind = EventKind.RUN_FAILED if self._status == "failed" else EventKind.RUN_CANCELLED
        self._svc.events.emit(
            kind.value, ("engine", LangGraphEngine.NAME), {"error": result.error or ""}
        )

    # ---- EngineRun API --------------------------------------------------------

    async def events(self) -> AsyncIterator[TeamEvent]:  # type: ignore[override]
        queue = self._svc.events.subscribe()  # type: ignore[attr-defined]
        if self._task is None:
            self._task = asyncio.create_task(self._execute())
        while True:
            event = await queue.get()
            yield event
            if event.kind in TERMINAL_EVENT_KINDS:
                return

    async def checkpoint(self) -> CheckpointRef:
        self._checkpoints += 1
        self._svc.events.emit(
            EventKind.CHECKPOINT_CREATED.value, ("engine", LangGraphEngine.NAME), {}
        )
        return CheckpointRef(
            run_id=self.run_id,
            attempt_id=self._svc.run.attempt_id,
            engine_name=LangGraphEngine.NAME,
            engine_version=LangGraphEngine.VERSION,
            state_blob_id=f"memorysaver:{self.run_id}",
            event_offset=len(getattr(self._svc.events, "events", []) or []),
            created_at=_utcnow(),
        )

    async def resume(self, ref: CheckpointRef) -> None:
        if ref.engine_name != LangGraphEngine.NAME or ref.engine_version != LangGraphEngine.VERSION:
            raise IncompatibleCheckpoint(ref.engine_name)
        self._status = "running"

    async def cancel(self, reason: str) -> None:
        if self._status in _TERMINAL:
            return
        self._status = "cancelled"
        if self._task is not None:
            self._task.cancel()
        if self._result is None:
            self._terminal_fail(TeamResult(status="cancelled", summary=reason))

    def state(self) -> str:
        return self._status

    def graph(self) -> ExecutionGraph:
        agents = self._plan.spec.agents
        adjacency = _adjacency(self._plan.spec)
        return ExecutionGraph(
            nodes=tuple(
                NodeState(
                    node_id=a.name,
                    agent=a.name,
                    status=(
                        "done"
                        if a.name in self._completed
                        else ("pending" if self._status != "cancelled" else "skipped")
                    ),
                )
                for a in agents
            ),
            edges=tuple((src, dst) for src, targets in adjacency.items() for dst in targets),
        )

    async def result(self) -> TeamResult:
        if self._result is None:
            return TeamResult(status=self._status, summary="run not finished")
        return self._result

    async def metrics(self) -> RunMetrics:
        return self._metrics()

    def _metrics(self) -> RunMetrics:
        tokens = _tokens_used(self._svc.models)
        return RunMetrics(
            wallclock_sec=0.0,
            total_tokens=tokens,
            input_tokens=0,
            output_tokens=tokens,
            tool_calls=0,
            llm_calls=self._llm_calls,
            cost_usd=None,
            agent_steps=len(self._completed),
            checkpoint_count=self._checkpoints,
            human_interventions=0,
        )


def _tokens_used(models: Any) -> int:
    return int(getattr(models, "tokens_out", 0) or getattr(models, "output_tokens", 0) or 0)


def _initial_state() -> LGState:
    return LGState(results={}, llm_calls=0)
