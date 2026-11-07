"""Worker rail client for frontend-venue sessions (wave 2026-09-21).

The sandbox worker holds exactly one credential — its session token — and
reaches the platform only through the egress sidecar's rail listener
(``R4_VENUE_RAIL_URL``, ``http://egress:8081``). Every call is
``Authorization: Bearer <token>``; the sidecar strips identity headers and
path-anchors the request to this session's rail prefix, and the API
authenticates the lease alone (``umrs/api/_rail_identity.py``).

Never logs the token; never reads env proxies (``trust_env=False`` so the
rail bypasses the web proxy the worker uses for public egress).
"""

from __future__ import annotations

from typing import Any

import httpx

from .errors import R4Error

#: The reserved venue source standing for the site's own EndpointRegistry
#: rows (ADR-092 slice 8b; UMRS ``umrs.shared.site_source``): ``--source
#: site`` at start, ``model@site`` in a plan.
SITE_SOURCE = "site"


class VenueRailError(R4Error):
    """Rail refusal: ``status`` + the server's named reason (never a token)."""

    def __init__(self, status: int, reason: str, *, code: str = "") -> None:
        super().__init__(
            f"venue rail {status}: {reason}",
            {"status_code": status, "reason": reason, "code": code},
        )
        self.status = status
        self.reason = reason
        self.code = code


def rail_gateway_base(rail_url: str, session_id: str) -> str:
    """Base URL for platform verbs routed through the rail (workflows, jobs).

    The sidecar proxies ``/umrs/companion/sessions/<sid>/rail/`` to the API,
    which re-dispatches ``…/rail/gw/<family>/…`` to ``/umrs/<family>/…`` with
    the lease owner's identity minted server-side (ADR-046 A2, rail gw).
    """
    return f"{rail_url.rstrip('/')}/umrs/companion/sessions/{session_id}/rail/gw"


class VenueRailClient:
    def __init__(
        self,
        base_url: str,
        session_id: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url or not session_id or not token:
            raise R4Error("venue rail requires base_url, session_id and a session token")
        self.session_id = session_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
        )

    # ── plumbing ──────────────────────────────────────────────────────

    def _url(self, tail: str) -> str:
        return f"/umrs/companion/sessions/{self.session_id}/rail/{tail.lstrip('/')}"

    def _call(self, method: str, tail: str, json: Any = None) -> dict[str, Any]:
        try:
            response = self._client.request(method, self._url(tail), json=json)
        except httpx.HTTPError as exc:
            raise VenueRailError(0, f"rail unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise _rail_error(response)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def close(self) -> None:
        self._client.close()

    # ── verbs ─────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict[str, Any]:
        return self._call("GET", "snapshot")

    def put_snapshot(
        self, *, name: str, updated_at: str, payload: str, state: str = "", venue: str = ""
    ) -> dict[str, Any]:
        del venue  # the rail pins venue=frontend server-side; never body-chosen
        return self._call(
            "POST",
            "snapshot",
            json={"name": name, "updated_at": updated_at, "payload": payload, "state": state},
        )

    def pop_queue(self) -> dict[str, Any] | None:
        """Next queued turn ``{id, content, client_id, added_at}`` or None."""
        result = self._call("POST", "queue/pop", json={})
        front = result.get("front")
        return front if isinstance(front, dict) and front.get("content") else None

    def heartbeat(
        self,
        *,
        phase: str,
        turn_seq: int = 0,
        partial: str = "",
        detail: str = "",
        extend_seconds: int = 0,
        tool_activity: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "phase": phase,
            "turn_seq": int(turn_seq),
            "partial": partial,
            "detail": detail,
            "extend_seconds": int(extend_seconds),
        }
        if tool_activity is not None:
            # Bounded strings only; older servers drop the unknown key.
            body["tool_activity"] = [str(line)[:200] for line in tool_activity[:24]]
        return self._call(
            "POST",
            "heartbeat",
            json=body,
        )

    def request_approval(
        self, *, turn_id: str, action: dict[str, Any], approval_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"turn_id": turn_id, "action": action}
        if approval_id:
            body["approval_id"] = approval_id
        result = self._call("POST", "approvals", json=body)
        approval = result.get("approval")
        return approval if isinstance(approval, dict) else {}

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        result = self._call("GET", f"approvals/{approval_id}")
        approval = result.get("approval")
        return approval if isinstance(approval, dict) else {}

    # ── agent settings and questions (ADR-099 D-8): read, raise, never answer ─

    def get_settings(self) -> dict[str, Any]:
        """The owner's agent settings ``{session_id, rev, settings}`` (rev 0 = none)."""
        return self._call("GET", "settings")

    def ask_question(self, *, turn_id: str, question: str, options: list[str]) -> dict[str, Any]:
        """Raise a durable ask_user question for the owner; the pending record."""
        body = {"turn_id": turn_id, "question": question, "options": list(options)}
        record = self._call("POST", "questions", json=body).get("question")
        return record if isinstance(record, dict) else {}

    def get_question(self, question_id: str) -> dict[str, Any]:
        record = self._call("GET", f"questions/{question_id}").get("question")
        return record if isinstance(record, dict) else {}

    def expire_question(self, question_id: str) -> dict[str, Any]:
        """Give up waiting: a pending question becomes ``expired``."""
        record = self._call("POST", f"questions/{question_id}/expire", json={}).get("question")
        return record if isinstance(record, dict) else {}

    # ── plan gates (ADR-092 slice 8): raise and read, never decide ────────

    def request_plan_gate(self, *, gate: str, trigger_class: str, prompt: str) -> dict[str, Any]:
        """Raise a plan gate as an intent in the UMRS approvals inbox (ADR-066)."""
        body = {"gate": gate, "trigger_class": trigger_class, "prompt": prompt}
        approval = self._call("POST", "plan-gates", json=body).get("approval")
        return approval if isinstance(approval, dict) else {}

    def get_plan_gate(self, approval_id: str) -> dict[str, Any]:
        approval = self._call("GET", f"plan-gates/{approval_id}").get("approval")
        return approval if isinstance(approval, dict) else {}


def _rail_error(response: httpx.Response) -> VenueRailError:
    """Named refusal from a 4xx/5xx rail answer (detail/error field, then text)."""
    try:
        body = response.json()
        # UMRS typed errors carry the named reason in "message" (e.g.
        # ip_mismatch); older shapes use "detail"/"error".
        reason = str(body.get("detail") or body.get("message") or body.get("error") or "")[:200]
    except ValueError:
        reason = (response.text or "")[:200]
    return VenueRailError(
        response.status_code,
        reason or f"http {response.status_code}",
        code=response.headers.get("x-platform-error-code", ""),
    )


def build_rail_platform_client(
    *, rail_url: str, session_id: str, token: str, endpoint_url: str, model: str
):
    """An r4 ``Client`` whose platform surface rides the rail gw prefix.

    - ``gateway_url`` = the rail gw base → ``client.workflows`` / jobs URLs land on
      ``…/rail/gw/workflows|jobs/…`` (sidecar-proxied, lease-authenticated);
    - ``api_key`` = the session token → ``Authorization: Bearer`` on every call;
    - ``endpoint_url``/``endpoint_api_key`` = the inference sidecar + token, so
      the LLM route stays the credential-injecting proxy (never the gw prefix);
    - the ``/gw``/``/umrs`` prefix probe is short-circuited to ``""`` because the
      rail base already carries the full prefix.
    """
    from .client import Client
    from .config import Config, TransportMode
    from .model_routing import ModelRoute, parse_model_ref, public_endpoint

    class RailClient(Client):
        @property
        def _api_prefix(self) -> str:  # type: ignore[override]
            return ""

        def _resolve_gated_route(self, raw_model: str) -> Any:  # type: ignore[override]
            """``model@site`` (ADR-092 slice 8b): the site's own registry rows,
            through the same sidecar under its ``site/`` prefix; the session's
            lease must carry the site source, and ``umrs-infer`` applies the
            ADR-062 gate server-side. Any other ref resolves as before."""
            if parse_model_ref(raw_model).source == SITE_SOURCE:
                return ModelRoute(kind="public", base_url=f"{public_endpoint(self._config)}/site")
            return super()._resolve_gated_route(raw_model)

    config = Config(
        profile="venue",
        gateway_url=rail_gateway_base(rail_url, session_id),
        api_key=token,
        endpoint_url=endpoint_url,
        endpoint_api_key=token,
        model=model,
        # The sandbox has no ZMQ path: every platform verb is HTTP over the rail.
        use_umrs_transport=False,
        transport_mode=TransportMode.GATEWAY,
    )
    return RailClient(config=config)
