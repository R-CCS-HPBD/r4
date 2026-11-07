"""HybridRuntime — research-loop control routing across Local + UMRS runtimes.

Design (Milestone 4):

- ``start_loop`` picks a venue from ``spec.requires``: cluster-class needs
  (gpu / cores / cluster / fugaku / slurm) route to the UMRS substrate;
  everything else lands on the LocalRuntime. Agent never names a backend.
- All other verbs first probe the runtime that holds the loop (loop-id
  prefix is the fast path — "local-"/"loop-" derived) and fall through to
  the second store on miss. Cross-store mutation of one loop never happens.

Authority rule unchanged: a loop is written by exactly one runtime. The
hybrid layer only routes; it does not duplicate or mirror writes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .api import (
    IterationState,
    LoopSpec,
    LoopState,
    ResearchRuntime,
    RuntimeCapabilities,
)
from .local import LocalRuntime, _default_db_path
from .local_policy import APPROVAL_PREFIX

_UMRS_REQUIREMENT_KEYS = frozenset({"gpu", "cores", "cluster", "fugaku", "slurm", "mpi"})


def _holds_cluster_requirement(requires: Dict) -> bool:
    """Cluster-class resources asked for → UMRS venue."""
    if not requires:
        return False
    return any(requires.get(key) not in (None, False, 0, "0", "") for key in _UMRS_REQUIREMENT_KEYS)


def _venue_for_spec(spec: LoopSpec) -> str:
    return "umrs" if _holds_cluster_requirement(dict(spec.requires)) else "local"


class HybridRuntime:
    """Route loop control between LocalRuntime (local) and UmrsRuntime (umrs)."""

    def __init__(
        self, locals_runtime: LocalRuntime, umrs_runtime: Optional[ResearchRuntime]
    ) -> None:
        self._local = locals_runtime
        self._umrs = umrs_runtime

    # ── identity ────────────────────────────────────────────────────────

    def capabilities(self) -> RuntimeCapabilities:
        if self._umrs is None:
            backends = ("local",)
        else:
            backends = ("local",) + tuple(self._umrs.capabilities().backends)
        return RuntimeCapabilities(
            location="hybrid",
            durable=True,
            distributed=self._umrs is not None,
            backends=backends,
            approvals=True,  # the local venue's own list at least (ADR-092 slice 6)
        )

    def _candidates(self, loop_id: str) -> Iterable[object]:
        if loop_id.startswith("local-"):
            pairs = (self._local, self._umrs)
        else:
            pairs = (self._umrs, self._local)
        for runtime in pairs:
            if runtime is not None:
                yield runtime

    # ── verbs ────────────────────────────────────────────────────────────

    def start_loop(self, spec: LoopSpec, executor=None) -> LoopState:
        venue = _venue_for_spec(spec)
        if venue == "umrs" and self._umrs is not None:
            state = self._umrs.start_loop(spec, executor=None)
        else:
            state = self._local.start_loop(spec, executor=executor)
        return self._stamp(state, spec, venue)

    @staticmethod
    def _stamp(state: LoopState, spec: LoopSpec, venue: str) -> LoopState:
        """View-layer venue/requires disclosure on the returned descriptor.

        The owning runtime's own metadata always wins on collision; local-
        only markers (venue, recorded requires) are view-side annotations so
        consumers see the ask even when the remote store doesn't keep them.
        """
        out = {**state.metadata}
        out.setdefault("venue", venue)
        out.setdefault("requires", dict(spec.requires))
        if spec.session_id:
            out.setdefault("session_id", spec.session_id)
        state.metadata = out
        return state

    def get_loop(self, loop_id: str) -> LoopState:
        return self._dispatch(loop_id, "get_loop")

    def stop_loop(self, loop_id: str) -> LoopState:
        return self._dispatch(loop_id, "stop_loop")

    def pause_loop(self, loop_id: str) -> LoopState:
        return self._dispatch(loop_id, "pause_loop")

    def resume_loop(self, loop_id: str) -> LoopState:
        return self._dispatch(loop_id, "resume_loop")

    def list_iterations(self, loop_id: str, limit: int = 50) -> List[IterationState]:
        return self._dispatch(loop_id, "list_iterations", limit=limit)

    def list_loops(self, project_id: Optional[str] = None) -> List[LoopState]:
        loops = list(self._local.list_loops(project_id))
        if self._umrs is not None:
            loops.extend(self._umrs.list_loops(project_id))
        return loops

    def list_approvals(self, loop_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Both venues' pending approvals: a local plan loop's own list
        (ADR-092 slice 6) and the UMRS durable inbox (ADR-036)."""
        rows = list(self._local.list_approvals(loop_id))
        if self._umrs is not None and not (loop_id or "").startswith("local-"):
            rows.extend(self._umrs.list_approvals(loop_id))
        return rows

    def decide_approval(self, approval_id: str, decision: str) -> Dict[str, Any]:
        if approval_id.startswith(APPROVAL_PREFIX):
            return self._local.decide_approval(approval_id, decision)
        if self._umrs is None:
            raise LookupError("no UMRS runtime configured: approvals live on UMRS (ADR-036)")
        return self._umrs.decide_approval(approval_id, decision)

    # ── dispatch helper ────────────────────────────────────────────────

    def _dispatch(self, loop_id: str, verb: str, **kwargs):
        first_error: Exception | None = None
        for runtime in self._candidates(loop_id):
            try:
                return getattr(runtime, verb)(loop_id, **kwargs)
            except LookupError as exc:
                first_error = first_error or exc
        raise first_error or LookupError(f"loop {loop_id} not found in any runtime store")


__all__ = ["HybridRuntime", "_default_db_path"]
