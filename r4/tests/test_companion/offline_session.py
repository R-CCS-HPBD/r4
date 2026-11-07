"""A companion session with no platform and no model, for prompt-assembly tests.

Every platform call on the fake client raises ``ConnectionError`` (a task
container or an offline workstation); ``_request`` records the request body
the agent would send and answers with one plain text reply.
"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

#: A TB-4.0-like task: its words activate code, platform and teamup skills.
TASK = (
    "A team trained a model; fix the bug in the training script, write the "
    "output file to /app/out.json, and run the workflow pipeline that uploads "
    "the dataset to the model registry endpoint."
)


class _Offline:
    """Any attribute chain; calling or iterating it fails like a refused connection."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return _Offline()

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("offline")

    def __iter__(self) -> Any:
        raise ConnectionError("offline")


class OfflineClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            api_key=None,
            gateway_url="",
            project_id=None,
            model="test-model",
            profile="default",
            use_umrs_transport=False,
            decision_model="off",
        )
        self._config = self.config
        self.payloads: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return _Offline()

    def _request(self, payload: dict[str, Any], **_kwargs: Any) -> Any:
        self.payloads.append(payload)
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        message = {"role": "assistant", "content": "Done."}
        return SimpleNamespace(
            content="Done.",
            model="test-model",
            usage=SimpleNamespace(**usage),
            raw_response={
                "usage": usage,
                "choices": [{"message": message, "finish_reason": "stop"}],
            },
        )


class _NoHarness:
    """The research harness, silent: its environment probe and progress
    notes depend on the machine and on earlier tests."""

    decision_log: list[Any] = []

    def feedforward(self, _messages: Any) -> str:
        return ""

    def post_turn(self, **_kwargs: Any) -> None:
        return None


def first_request(
    monkeypatch: Any,
    tmp_path: Any,
    *,
    task: str = TASK,
    trust: bool = True,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, list[str], Any]:
    """Send ``task`` once; return (prompt context, offered tool names, session).

    The prompt context is the system message plus the ``<turn_context>`` block
    the first user message carries (wave 80 moved the per-turn state there;
    with the old layout the block is absent and this is the system message).
    The harness is off (its environment probe is machine-specific) and so is
    memory (other tests write the shared scratch HOME).
    """
    import r4.companion.model_resolution as model_resolution
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R4_HARNESS_ABLATE_MEMORY", "1")
    monkeypatch.setattr(model_resolution, "ensure_model_ready", lambda *_a, **_k: None)
    client = OfflineClient()
    mode = PermissionMode.TRUST if trust else PermissionMode.ASK_ALWAYS
    session = CompanionSession(
        session_id="offline",
        name="offline",
        client=client,
        model="test-model",
        permission_mode=mode,
        endpoint_refresh_interval_sec=86_400,
    )
    session.permissions.set_approval_callback(lambda *_a, **_k: True)
    session._harness = _NoHarness()
    session.metadata.update(metadata or {})
    session.send(task)
    payload = client.payloads[0]
    names = sorted(tool["function"]["name"] for tool in payload.get("tools") or [])
    return prompt_context(payload), names, session


def prompt_context(payload: dict[str, Any]) -> str:
    """The system message, then the turn block of the request's last message."""
    from r4.companion.turn_context import TURN_CONTEXT_OPEN

    messages = payload["messages"]
    last = str(messages[-1].get("content") or "")
    _, found, block = last.partition("\n\n" + TURN_CONTEXT_OPEN)
    turn = f"\n\n{TURN_CONTEXT_OPEN}{block}" if found else ""
    return messages[0]["content"] + turn


def normalized(system: str) -> str:
    """The prompt with this run's workspace path replaced (snapshot form)."""
    root = os.path.realpath(os.getcwd())
    return re.sub(re.escape(root), "<ROOT>", system)
