"""The ``knowledge_models`` line of ``r4 doctor`` and ``r4 health full``.

ADR-116 amendment (2026-11-13): Docling (PDF layout parsing) and GLiNER
(entity pre-tagging) are on by default on a platform, served warm by its
``umrs-docmodels`` service. The platform advertises the state in the
``knowledge_models`` block of connect-info (address-free: no URL, no path);
this check reads it through the gateway and turns it into one line.

The line is advisory. A model that is switched on but cannot run never
fails an understand job (it falls back to pypdf, or to no hints), so it
never fails ``r4 doctor`` either: such a state shows as "–" with the reason,
not as "✗". When the service does not answer, whether the server runs the
model itself depends on the platform's ``service.local_fallback``
(``off``, ``sandbox`` or ``inprocess``; an older platform that does not say
ran it in the server).
"""

from __future__ import annotations

from typing import Any

_PROBE_TIMEOUT_SEC = 10


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _local_fallback(service: dict[str, Any]) -> str:
    """What the server runs itself when the service does not answer."""
    return str(service.get("local_fallback") or "inprocess")


def _usable(model: dict[str, Any], service: dict[str, Any]) -> bool:
    """A switched-on model runs: the service holds it, or the server has it
    staged and may run it itself."""
    if service.get("ready") is True:
        return True
    if not model.get("staged"):
        return False
    return not service.get("url_set") or _local_fallback(service) != "off"


def _why_not(model: dict[str, Any]) -> str:
    return "not staged" if not model.get("staged") else "service not answering"


def _docling_text(docling: dict[str, Any], service: dict[str, Any]) -> str:
    if not docling.get("enabled"):
        return "Docling off (pypdf)"
    version = f" {docling['version']}" if docling.get("version") else ""
    if _usable(docling, service):
        return f"Docling{version} on"
    return f"Docling{version} on but {_why_not(docling)} (pypdf fallback)"


def _gliner_text(gliner: dict[str, Any], service: dict[str, Any]) -> str:
    if not gliner.get("enabled"):
        return "GLiNER off"
    if _usable(gliner, service):
        return "GLiNER on"
    return f"GLiNER on but {_why_not(gliner)} (no hints)"


_FALLBACK_TEXT = {
    "off": "no local fallback",
    "sandbox": "sandbox fallback",
    "inprocess": "server fallback",
}


def _service_text(service: dict[str, Any]) -> str:
    if not service.get("url_set"):
        return "run in the server"
    ready = service.get("ready")
    if ready is True:
        return "umrs-docmodels ready"
    if ready is False:
        fallback = _FALLBACK_TEXT.get(_local_fallback(service), "server fallback")
        return f"umrs-docmodels not answering ({fallback})"
    return "umrs-docmodels state unknown"


def summarize(block: dict[str, Any]) -> str:
    """One line, e.g. ``Docling 2.150.0 on, GLiNER on, umrs-docmodels ready``."""
    service = _mapping(block.get("service"))
    parts = (
        _docling_text(_mapping(block.get("docling")), service),
        _gliner_text(_mapping(block.get("gliner")), service),
        _service_text(service),
    )
    return ", ".join(parts)


def _all_usable(block: dict[str, Any]) -> bool:
    service = _mapping(block.get("service"))
    models = (_mapping(block.get("docling")), _mapping(block.get("gliner")))
    return all(_usable(m, service) for m in models if m.get("enabled"))


def knowledge_models_result(connect_info: Any) -> dict[str, Any]:
    """The check result from a connect-info answer; the block passes through as is."""
    block = _mapping(_mapping(connect_info).get("knowledge_models"))
    if not block:
        return {"ok": None, "message": "not advertised by this platform (older UMRS)"}
    ok = True if _all_usable(block) else None
    return {"ok": ok, "summary": summarize(block), **block}


def check_knowledge_models(config: Any) -> dict[str, Any]:
    """Read the platform's ``knowledge_models`` block from connect-info."""
    try:
        from ..client import Client

        client = Client(config=config)
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/ops/connect-info",
            timeout=_PROBE_TIMEOUT_SEC,
        )
    except Exception as exc:
        return {"ok": None, "message": f"not reachable ({exc})"}
    return knowledge_models_result(data)
