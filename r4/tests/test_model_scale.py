"""ADR-093 D-14 (C22): model-scale detection across every registered source.

The maintainer (2026-10-10): "r4 needs a logic to detect the size/scale of a
model user registered to r4, as we have multiple registration of sources
(self-deployed, rikyu, ollama, or any other public endpoint)". Every source
kind is served here by an ``httpx.MockTransport``; no test reaches a network.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from r4 import config as r4_config
from r4 import model_scale
from r4.model_scale import (
    CACHE_TTL_S,
    PROBE_TIMEOUT_S,
    ModelScale,
    cache_path,
    detect_model_scale,
    load_cached_scales,
    overrides_path,
    rag_profile_name,
    session_model_scale,
    set_override,
    take_session_notice,
    unset_override,
)
from r4.source import Source, get_source_store

SECRET = "sk-test-SECRET-4242"
OLLAMA = Source(name="ollama", kind="public", url="http://ollama.test:11434", provider="ollama")
RIKYU = Source(name="rikyu", kind="public", url="https://rikyu.test/v1", api_key=SECRET)
PLATFORM = Source(name="standalone", kind="platform", url="http://gw.test", api_key=SECRET)
BEDROCK = Source(name="aws", kind="public", url="", provider="bedrock")
ANTHROPIC = Source(
    name="claude", kind="public", url="https://api.anthropic.test", provider="anthropic"
)

LLAMA_SHOW = {
    "details": {"family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4_K_M"},
    "model_info": {
        "general.architecture": "llama",
        "general.basename": "Llama-3.2",
        "general.parameter_count": 3_212_749_888,
        "general.size_label": "3B",
        "llama.context_length": 131_072,
    },
}
MOE_INFO = {
    "general.architecture": "qwen3moe",
    "general.parameter_count": 30_532_122_624,
    "qwen3moe.context_length": 40_960,
    "qwen3moe.expert_count": 128,
    "qwen3moe.expert_used_count": 8,
}
KIMI_LIST = {
    "object": "list",
    "data": [
        {
            "id": "kimi-k3",
            "object": "model",
            "owned_by": "vllm",
            "root": "moonshotai/Kimi-K3",
            "parent": None,
            "max_model_len": 1_048_576,
        }
    ],
}


def _registry_entry(**extra):
    entry = {
        "model_id": "m-1",
        "name": "chat-default",
        "scope": "system",
        "model_type": "llm",
        "storage_path": "",
    }
    return {**entry, **extra}


class FakeNet:
    """Routes ``(method, url)`` to canned replies and records every request."""

    def __init__(self):
        self.routes = {}
        self.requests = []

    def add(self, method, url, body=None, *, status=200, exc=None, delay=0.0):
        self.routes[(method, url)] = (status, body, exc, delay)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        status, body, exc, delay = self.routes.get((request.method, url), (404, {}, None, 0))
        time.sleep(delay)
        if exc is not None:
            raise exc
        return httpx.Response(status, json=body)

    def paths(self):
        return [request.url.path for request in self.requests]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("R4_MODEL_SCALE", "R4_RAG_PROFILE", "R4_SOURCE", "R4_GATEWAY_URL", "R4_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(r4_config, "_config", None)
    model_scale.clear_scale_memo()
    import r4._http as _r4_http

    _r4_http._API_PREFIX_CACHE.clear()  # H-10.2 mode probe memo (determinism)
    yield tmp_path / "r4"
    model_scale.clear_scale_memo()


@pytest.fixture(autouse=True)
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(model_scale, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


def _scale_tuple(scale: ModelScale):
    return (scale.size_class, scale.params_b, scale.active_params_b, scale.basis)


# ------------------------------------------------------------------ ollama ---


def test_ollama_reports_the_exact_parameter_count(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    scale = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert _scale_tuple(scale) == ("small", 3.213, None, "ollama")
    assert (scale.model, scale.source, scale.context, scale.profile) == (
        "llama3.2:3b",
        "ollama",
        131_072,
        "small",
    )
    assert json.loads(net.requests[0].content) == {"model": "llama3.2:3b"}


def test_ollama_parameter_size_when_no_exact_count(net):
    body = {"details": {"parameter_size": "8.0B"}, "model_info": {}}
    net.add("POST", "http://ollama.test:11434/api/show", body)
    scale = detect_model_scale("house:latest", source=OLLAMA)
    assert _scale_tuple(scale) == ("medium", 8.0, None, "ollama")
    assert "details.parameter_size" in scale.detail


def test_ollama_moe_expert_counts_estimate_the_active_size(net):
    net.add("POST", "http://ollama.test:11434/api/show", {"model_info": MOE_INFO})
    scale = detect_model_scale("house-moe:latest", source=OLLAMA)
    total = 30.532122624
    active = total * 8 / 128
    assert _scale_tuple(scale) == ("medium", 30.532, round(active, 3), "ollama-moe-estimate")
    assert scale.effective_params_b == round((total * active) ** 0.5, 3)
    assert scale.context == 40_960


def test_an_ollama_size_label_states_the_active_size(net):
    info = {**MOE_INFO, "general.size_label": "30B-A3B"}
    net.add("POST", "http://ollama.test:11434/api/show", {"model_info": info})
    scale = detect_model_scale("qwen3:30b-a3b", source=OLLAMA)
    assert _scale_tuple(scale) == ("medium", 30.532, 3.0, "ollama")
    assert scale.effective_params_b == round((30.532122624 * 3.0) ** 0.5, 3)


def test_a_public_url_that_answers_api_tags_is_probed_as_ollama(net):
    plain = Source(name="lab", kind="public", url="http://lab.test:11434/v1")
    net.add("GET", "http://lab.test:11434/api/tags", {"models": [{"name": "llama3.2:3b"}]})
    net.add("POST", "http://lab.test:11434/api/show", LLAMA_SHOW)
    scale = detect_model_scale("llama3.2:3b", source=plain)
    assert _scale_tuple(scale) == ("small", 3.213, None, "ollama")
    assert net.paths() == ["/api/tags", "/api/show"]
    assert "an ollama server" in scale.detail


# ------------------------------------------------- OpenAI-compatible public ---


def test_vllm_models_list_gives_context_and_root(net):
    net.add("GET", "https://rikyu.test/v1/models", KIMI_LIST)
    scale = detect_model_scale("kimi-k3", source=RIKYU)
    assert _scale_tuple(scale) == ("large", None, None, "family")
    assert (scale.context, scale.profile, scale.source) == (1_048_576, "large", "rikyu")
    assert net.paths() == ["/api/tags", "/v1/models"]
    assert net.requests[1].headers["authorization"] == f"Bearer {SECRET}"
    assert "authorization" not in net.requests[0].headers


def test_the_root_hf_path_sizes_an_aliased_model(net):
    entry = {"id": "chat", "root": "moonshotai/Kimi-K2-Instruct", "max_model_len": 131_072}
    net.add("GET", "https://rikyu.test/v1/models", {"data": [entry]})
    scale = detect_model_scale("chat", source=RIKYU)
    assert _scale_tuple(scale) == ("large", 1_000.0, 32.0, "family")
    assert scale.effective_params_b == round((1_000.0 * 32.0) ** 0.5, 3)


def test_a_published_parameter_field_is_exact(net):
    entry = {"id": "house-ft", "parameters": 7_615_616_512, "context_window": 32_768}
    net.add("GET", "https://rikyu.test/v1/models", {"data": [entry]})
    scale = detect_model_scale("house-ft", source=RIKYU)
    assert _scale_tuple(scale) == ("medium", 7.616, None, "endpoint")
    assert scale.context == 32_768


def test_a_bare_openai_list_falls_to_the_name_rule(net):
    listed = {"data": [{"id": "Llama-3.1-8B-Instruct", "owned_by": "org"}]}
    net.add("GET", "https://rikyu.test/v1/models", listed)
    scale = detect_model_scale("llama-3.1-8b-instruct", source=RIKYU)
    assert _scale_tuple(scale) == ("medium", 8.0, None, "name")
    assert "owned_by org" in scale.detail


def test_unknown_reads_medium_and_carries_the_notice(net):
    net.add("GET", "https://rikyu.test/v1/models", {"data": [{"id": "house-model"}]})
    scale = detect_model_scale("house-model", source=RIKYU)
    assert (scale.size_class, scale.basis, scale.profile) == ("unknown", "unknown", "medium")
    assert scale.notice() == (
        "model scale unknown for house-model@rikyu; "
        "set it with `r4 model scale set house-model small|medium|large`"
    )


# ---------------------------------------------------------------- platform ---


def test_platform_registry_huggingface_id_sizes_the_model(net):
    entry = _registry_entry(
        huggingface_id="Qwen/Qwen3-30B-A3B",
        capabilities={"max_position_embeddings": 40_960, "model_family": "qwen3"},
    )
    net.add("GET", "http://gw.test/gw/models", {"models": [entry]})
    scale = detect_model_scale("chat-default", source=PLATFORM)
    assert _scale_tuple(scale) == ("medium", 30.0, 3.0, "name")
    assert (scale.context, scale.source) == (40_960, "standalone")
    assert net.requests[0].headers["authorization"] == f"Bearer {SECRET}"


def test_a_registry_parameter_count_is_exact(net):
    entry = _registry_entry(name="big-local", param_count=70_553_706_496)
    net.add("GET", "http://gw.test/gw/models", {"models": [entry]})
    scale = detect_model_scale("big-local", source=PLATFORM)
    assert _scale_tuple(scale) == ("large", 70.554, None, "registry")


def test_platform_headers_come_from_the_client_without_a_source_key(net):
    # The client's own deployment: its login is the source's (2026-10-15: a
    # source on ANOTHER deployment never gets it, see test_source_isolation).
    keyless = Source(name="standalone", kind="platform", url="http://gw.test")
    http = SimpleNamespace(build_headers=lambda: {"Authorization": "Bearer oidc-token"})
    client = SimpleNamespace(
        platform_http=http, config=SimpleNamespace(gateway_url="http://gw.test")
    )
    net.add("GET", "http://gw.test/gw/models", {"models": [_registry_entry()]})
    detect_model_scale("chat-default", source=keyless, client=client)
    assert net.requests[0].headers["authorization"] == "Bearer oidc-token"


def test_an_unregistered_platform_model_is_not_cached(net):
    net.add("GET", "http://gw.test/gw/models", {"models": []})
    scale = detect_model_scale("llama3.2:3b", source=PLATFORM)
    assert _scale_tuple(scale) == ("small", 3.0, None, "name")
    assert "not registered" in scale.detail
    assert not cache_path().exists()


# ------------------------------------------- platform: straight at UMRS ---
# Integrator, 2026-10-10: a platform source may point straight at UMRS (the
# ``/umrs`` prefix, no gateway), where ``/gw/models`` is a 404; the probe fell
# through to the name rule and never read the registry there.

UMRS_DIRECT = Source(name="umrs", kind="platform", url="http://umrs.test:8000", api_key=SECRET)


def test_a_source_straight_at_umrs_is_probed_on_umrs_models(net):
    entry = _registry_entry(name="big-local", param_count=70_553_706_496)
    net.add("GET", "http://umrs.test:8000/umrs/models", {"models": [entry]})
    scale = detect_model_scale("big-local", source=UMRS_DIRECT)
    assert _scale_tuple(scale) == ("large", 70.554, None, "registry")
    assert net.paths() == ["/gw/models", "/umrs/models"]
    assert net.requests[1].headers["authorization"] == f"Bearer {SECRET}"
    assert "registry /umrs/models" in scale.detail


@pytest.mark.parametrize("status", [401, 500])
def test_only_a_404_on_the_gateway_path_tries_umrs(net, status):
    net.add("GET", "http://gw.test/gw/models", {}, status=status)
    scale = detect_model_scale("llama3.2:3b", source=PLATFORM)
    assert (net.paths(), scale.basis) == (["/gw/models"], "name")
    assert f"registry GET /gw/models: HTTP {status}" in scale.detail


def test_a_registry_missing_on_both_paths_falls_to_the_name_rule(net):
    scale = detect_model_scale("qwen3-30b-a3b", source=UMRS_DIRECT)
    assert (net.paths(), scale.size_class, scale.basis) == (
        ["/gw/models", "/umrs/models"],
        "medium",
        "name",
    )
    assert "registry GET /umrs/models: HTTP 404" in scale.detail


# --------------------------------------------------------------- verify_ssl ---
# Integrator, 2026-10-10: the probes built their own httpx client with the
# default verify=True, so a self-signed gateway the user had configured with
# ``verify_ssl`` off failed every probe and silently fell to the name rule.


@pytest.fixture
def verifies(monkeypatch):
    """The ``verify`` every probe's httpx client was built with."""
    seen: list[object] = []
    real = httpx.Client

    def recording(*args, **kwargs):
        seen.append(kwargs.get("verify", "unset"))
        return real(*args, **kwargs)

    monkeypatch.setattr(model_scale.httpx, "Client", recording)
    return seen


def test_a_self_signed_gateway_is_probed_with_verify_ssl_off(net, verifies):
    entry = _registry_entry(huggingface_id="meta-llama/Llama-3.2-3B-Instruct")
    net.add("GET", "https://gw.test/gw/models", {"models": [entry]})
    client = _client(gateway_url="https://gw.test", verify_ssl=False)
    scale = detect_model_scale("chat-default", client=client)
    # verifies: the H-10.2 mode probe's one client + the scale probe's one.
    assert (scale.size_class, scale.params_b, verifies) == ("small", 3.0, [False, False])


def test_every_public_probe_honours_verify_ssl(net, verifies):
    net.add("GET", "https://rikyu.test/v1/models", KIMI_LIST)
    client = _client(endpoint_url="https://rikyu.test/v1", verify_ssl=False)
    detect_model_scale("kimi-k3", client=client)
    assert (net.paths(), verifies) == (["/api/tags", "/v1/models"], [False, False])


def test_probes_verify_by_default(net, verifies):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    detect_model_scale("llama3.2:3b", source=OLLAMA, client=_client())
    detect_model_scale("glm-5.3", source=OLLAMA)  # no client: the active config
    assert verifies == [True, True]


def test_a_certificate_failure_names_verify_ssl_instead_of_falling_silently(net):
    failure = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate"
    )
    net.add("GET", "https://gw.test/gw/models", exc=failure)
    scale = detect_model_scale("chat-default", client=_client(gateway_url="https://gw.test"))
    assert scale.size_class == "unknown"
    assert "TLS certificate not verified (verify_ssl is on" in scale.detail


# ------------------------------------------------------------------ hosted ---


@pytest.mark.parametrize(
    ("source", "model", "size", "basis"),
    [
        (BEDROCK, "anthropic.claude-sonnet-4-v1:0", "large", "family"),
        (BEDROCK, "amazon.nova-pro-v1:0", "large", "hosted"),
        (BEDROCK, "meta.llama3-1-8b-instruct-v1:0", "medium", "name"),
        (ANTHROPIC, "claude-opus-4", "large", "family"),
        (ANTHROPIC, "house-alias", "large", "hosted"),
    ],
)
def test_hosted_providers_are_large_by_family_without_a_request(net, source, model, size, basis):
    scale = detect_model_scale(model, source=source)
    assert (scale.size_class, scale.basis, net.requests) == (size, basis, [])


# --------------------------------------------------------------- overrides ---


def test_a_source_override_beats_the_wildcard_and_the_probe(net):
    set_override("kimi-k3", "small", note="eval pin")
    set_override("kimi-k3", "medium", source="rikyu", params_b=1000, active_params_b=32)
    scale = detect_model_scale("kimi-k3", source=RIKYU)
    assert (scale.size_class, scale.basis, scale.params_b, scale.active_params_b) == (
        "medium",
        "override",
        1000.0,
        32.0,
    )
    assert unset_override("kimi-k3", source="rikyu") is True
    wildcard = detect_model_scale("kimi-k3", source=RIKYU)
    assert (wildcard.size_class, wildcard.basis, net.requests) == ("small", "override", [])
    assert "eval pin" in wildcard.detail


def test_an_override_without_a_class_is_sized_by_its_params(home):
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    entries = {"*/x": {"params_b": 30, "active_params_b": 3}, "*/y": "garbage"}
    overrides_path().write_text(json.dumps(entries))
    assert detect_model_scale("x", source=OLLAMA).size_class == "medium"
    assert detect_model_scale("y", source=OLLAMA).basis != "override"


def test_set_override_refuses_a_class_that_is_not_a_profile():
    with pytest.raises(ValueError):
        set_override("x", "unknown")
    assert unset_override("x") is False


def test_the_env_override_beats_the_file(monkeypatch, net):
    set_override("llama3.2:3b", "small")
    monkeypatch.setenv("R4_MODEL_SCALE", "large")
    scale = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert (scale.size_class, scale.basis, net.requests) == ("large", "env", [])
    monkeypatch.setenv("R4_MODEL_SCALE", "huge")
    assert detect_model_scale("llama3.2:3b", source=OLLAMA).basis == "override"


# ------------------------------------------------------------------- cache ---


def test_a_probe_result_is_cached_and_reused(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    first = detect_model_scale("llama3.2:3b", source=OLLAMA)
    model_scale.clear_scale_memo()  # a new process reads the disk cache
    second = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert (second, len(net.requests)) == (first, 1)
    assert list(load_cached_scales()) == ["ollama/llama3.2:3b"]


def test_a_stale_cache_entry_is_re_probed(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    detect_model_scale("llama3.2:3b", source=OLLAMA)
    data = json.loads(cache_path().read_text())
    data["ollama/llama3.2:3b"]["detected_at"] = time.time() - CACHE_TTL_S - 1
    cache_path().write_text(json.dumps(data))
    model_scale.clear_scale_memo()
    detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert len(net.requests) == 2


def test_refresh_re_probes(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    detect_model_scale("llama3.2:3b", source=OLLAMA)
    detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert len(net.requests) == 1
    detect_model_scale("llama3.2:3b", source=OLLAMA, refresh=True)
    assert len(net.requests) == 2


def test_overrides_are_never_cached(monkeypatch):
    set_override("kimi-k3", "large")
    detect_model_scale("kimi-k3", source=RIKYU)
    monkeypatch.setenv("R4_MODEL_SCALE", "small")
    detect_model_scale("kimi-k3", source=RIKYU)
    assert not cache_path().exists()


# -------------------------------------------------------- bounded, no raise ---


def test_every_probe_is_bounded_at_two_seconds(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    detect_model_scale("llama3.2:3b", source=OLLAMA)
    timeout = net.requests[0].extensions["timeout"]
    assert PROBE_TIMEOUT_S == 2.0
    assert set(timeout.values()) == {2.0}


def test_a_probe_timeout_falls_through_to_the_name_rule(net):
    slow = httpx.ReadTimeout("slow")
    net.add("POST", "http://ollama.test:11434/api/show", exc=slow)
    scale = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert _scale_tuple(scale) == ("small", 3.0, None, "name")
    assert "timed out after 2s" in scale.detail
    assert not cache_path().exists()


def test_an_unreachable_endpoint_is_asked_once(net):
    net.add("GET", "https://rikyu.test/api/tags", exc=httpx.ConnectError("refused"))
    scale = detect_model_scale("kimi-k3", source=RIKYU)
    assert (scale.size_class, net.paths()) == ("large", ["/api/tags"])
    assert "unreachable (ConnectError)" in scale.detail


@pytest.mark.parametrize(
    "reply",
    [
        {"exc": RuntimeError("boom")},
        {"body": {"model_info": "garbage", "details": []}},
        {"body": ["not", "an", "object"]},
        {"status": 500, "body": {}},
    ],
)
def test_a_broken_reply_never_raises(net, reply):
    net.add("POST", "http://ollama.test:11434/api/show", **reply)
    scale = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert (scale.size_class, scale.basis) == ("small", "name")


def test_an_internal_failure_still_answers_by_name(monkeypatch):
    def broken():
        raise OSError("disk")

    monkeypatch.setattr(model_scale, "get_source_store", broken)
    scale = detect_model_scale("qwen3-30b-a3b@somewhere")
    assert (scale.model, scale.size_class, scale.basis) == ("qwen3-30b-a3b", "medium", "name")
    assert "detection failed (OSError)" in scale.detail


# ----------------------------------------------------------------- secrets ---


def test_the_api_key_never_reaches_output_cache_or_logs(net, caplog):
    caplog.set_level(logging.DEBUG, logger="r4.model_scale")
    net.add("GET", "https://rikyu.test/v1/models", KIMI_LIST)
    found = detect_model_scale("kimi-k3", source=RIKYU)
    net.add("GET", "https://rikyu.test/v1/models", {}, status=401)
    failed = detect_model_scale("glm-5.3", source=RIKYU)
    texts = [repr(found), json.dumps(failed.to_dict()), cache_path().read_text(), caplog.text]
    assert net.requests[-1].headers["authorization"] == f"Bearer {SECRET}"
    assert [SECRET in text for text in texts] == [False] * 4
    assert "HTTP 401" in failed.detail


# ---------------------------------------------------------- source routing ---


def test_model_at_source_names_the_source(net):
    get_source_store().add(OLLAMA)
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    scale = detect_model_scale("llama3.2:3b@ollama")
    assert (scale.model, scale.source, scale.basis) == ("llama3.2:3b", "ollama", "ollama")


def test_an_unregistered_source_name_falls_to_the_name_rule(net):
    scale = detect_model_scale("llama3.2:3b", source="nowhere")
    assert (scale.source, scale.basis, net.requests) == ("nowhere", "name", [])
    assert "'nowhere' is not registered" in scale.detail


def _client(**config):
    base = {"model_routes": {}, "endpoint_url": None, "endpoint_provider": None}
    base.update(gateway_url="http://gw.test", api_key=None, endpoint_api_key=None)
    return SimpleNamespace(config=SimpleNamespace(**{**base, **config}))


def test_the_clients_route_map_picks_the_source(net):
    get_source_store().add(OLLAMA)
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    client = _client(model_routes={"llama3.2:3b": "ollama"})
    scale = detect_model_scale("llama3.2:3b", client=client)
    assert (scale.source, scale.basis) == ("ollama", "ollama")


def test_a_configured_public_endpoint_matches_its_registered_source(net):
    get_source_store().add(RIKYU)
    net.add("GET", "https://rikyu.test/v1/models", KIMI_LIST)
    client = _client(endpoint_url="https://rikyu.test/v1", endpoint_api_key="other")
    scale = detect_model_scale("kimi-k3", client=client)
    assert (scale.source, scale.context) == ("rikyu", 1_048_576)
    assert net.requests[-1].headers["authorization"] == f"Bearer {SECRET}"


def test_an_unregistered_public_endpoint_is_probed_as_configured(net):
    net.add("GET", "https://other.test/v1/models", KIMI_LIST)
    client = _client(endpoint_url="https://other.test", endpoint_api_key="cfg-key")
    scale = detect_model_scale("kimi-k3", client=client)
    assert (scale.source, scale.context) == ("public", 1_048_576)
    assert net.requests[-1].headers["authorization"] == "Bearer cfg-key"


def test_the_gateway_matches_a_registered_platform_source(net):
    get_source_store().add(PLATFORM)
    net.add("GET", "http://gw.test/gw/models", {"models": [_registry_entry()]})
    scale = detect_model_scale("chat-default", client=_client())
    assert (scale.source, scale.size_class, net.paths()) == (
        "standalone",
        "unknown",
        ["/gw/models"],
    )


def test_a_legacy_platform_route_uses_the_configured_gateway(net):
    net.add("GET", "http://gw.test/gw/models", {"models": []})
    client = _client(model_routes={"x-8b": "platform"}, endpoint_url="https://p.test")
    scale = detect_model_scale("x-8b", client=client)
    assert (scale.source, scale.size_class, net.paths()) == ("platform", "medium", ["/gw/models"])


# ---------------------------------------------------------- thread safety ---


def test_concurrent_detections_probe_once(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW, delay=0.05)
    results = []

    def detect():
        results.append(detect_model_scale("llama3.2:3b", source=OLLAMA))

    threads = [threading.Thread(target=detect) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (len(net.requests), len(results), len(set(results))) == (1, 8, 1)


# ------------------------------------------------------------ session glue ---


def test_a_session_detects_once_per_model(net):
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    get_source_store().add(OLLAMA)
    session = SimpleNamespace(client=_client(model_routes={"llama3.2:3b": "ollama"}))
    first = session_model_scale(session, "llama3.2:3b")
    model_scale.clear_scale_memo()
    assert session_model_scale(session, "llama3.2:3b") is first
    assert session_model_scale(session, "glm-5.3@nowhere").size_class == "large"
    assert len(net.requests) == 1


def test_a_session_re_detects_when_the_source_changes(net):
    get_source_store().add(OLLAMA)
    get_source_store().add(RIKYU)
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    net.add("GET", "https://rikyu.test/v1/models", {"data": [{"id": "llama3.2:3b"}]})
    session = SimpleNamespace(client=None)
    first = session_model_scale(session, "llama3.2:3b@ollama")
    second = session_model_scale(session, "llama3.2:3b@rikyu")
    assert (first.source, first.basis, second.source, second.basis) == (
        "ollama",
        "ollama",
        "rikyu",
        "name",
    )


def test_a_missing_model_name_never_raises():
    scale = detect_model_scale(None)  # type: ignore[arg-type]
    assert (scale.model, scale.size_class) == ("", "unknown")


def test_the_unknown_notice_is_shown_once_per_session():
    session = SimpleNamespace()
    unknown = ModelScale(model="house", source="lab", size_class="unknown")
    known = ModelScale(model="llama3.2:3b", source="ollama", size_class="small")
    assert take_session_notice(session, known) is None
    assert take_session_notice(session, unknown) == unknown.notice()
    assert take_session_notice(session, unknown) is None


@pytest.mark.parametrize(
    ("env", "size_class", "expected"),
    [
        (None, "unknown", "medium"),
        (None, "large", "large"),
        ("small", "large", "small"),  # R4_RAG_PROFILE still wins
        ("auto", "large", "large"),
        ("auto", "unknown", "medium"),
    ],
)
def test_the_rag_profile_for_a_detected_scale(monkeypatch, env, size_class, expected):
    if env:
        monkeypatch.setenv("R4_RAG_PROFILE", env)
    scale = ModelScale(model="m", source="s", size_class=size_class)
    assert rag_profile_name(scale) == expected


def test_a_scale_round_trips_and_ignores_unknown_keys():
    scale = ModelScale("m", "s", "medium", 30.0, 3.0, 9.487, 40_960, "name", "d", 1.0)
    assert ModelScale.from_dict({**scale.to_dict(), "future": 1}) == scale


# ------------------------------------------------ review fixes (2026-10-10) ---
# Adversarial review of wave D, each finding confirmed by two verifiers; every
# test below fails on the code it reviewed.


def _write_cache_rows(rows: dict) -> None:
    cache_path().parent.mkdir(parents=True, exist_ok=True)
    cache_path().write_text(json.dumps(rows))


def test_a_malformed_cache_row_never_stops_detection_or_hides_the_others(net):
    """r4-scale-router-1: one non-dict row (or a non-numeric detected_at)
    turned detection off for every model and crashed ``r4 model scale list``."""
    fresh = {"model": "qwen3:8b", "source": "ollama", "size_class": "medium"}
    _write_cache_rows(
        {
            "other/x": "garbage",
            "ollama/llama3.2:3b": {
                "model": "llama3.2:3b",
                "source": "ollama",
                "size_class": "small",
                "detected_at": "bad",
            },
            "ollama/qwen3:8b": {**fresh, "basis": "ollama", "detected_at": time.time()},
            "ollama/bad-size": {**fresh, "size_class": None, "detected_at": time.time()},
        }
    )
    assert list(load_cached_scales()) == ["ollama/qwen3:8b"]
    net.add("POST", "http://ollama.test:11434/api/show", LLAMA_SHOW)
    scale = detect_model_scale("llama3.2:3b", source=OLLAMA)
    assert (scale.basis, scale.size_class) == ("ollama", "small")
    healed = json.loads(cache_path().read_text())  # the bad rows are dropped on write
    assert sorted(healed) == ["ollama/llama3.2:3b", "ollama/qwen3:8b"]


def test_two_unregistered_gateways_serving_one_alias_never_share_an_entry(net):
    """r4-scale-router-2: the cache key was the literal 'platform', so a
    second deployment read the first one's class for 7 days."""
    small = _registry_entry(huggingface_id="meta-llama/Llama-3.2-3B-Instruct")
    large = _registry_entry(huggingface_id="moonshotai/Kimi-K3")
    net.add("GET", "http://a.test/gw/models", {"models": [small]})
    net.add("GET", "http://b.test/gw/models", {"models": [large]})
    first = detect_model_scale("chat-default", client=_client(gateway_url="http://a.test"))
    model_scale.clear_scale_memo()  # a new process: only the disk cache is shared
    second = detect_model_scale("chat-default", client=_client(gateway_url="http://b.test"))
    assert (first.size_class, second.size_class) == ("small", "large")
    assert (first.source, second.source) == ("platform", "platform")  # the human label
    assert [request.url.host for request in net.requests] == ["a.test", "b.test"]
    assert len(load_cached_scales()) == 2


def test_a_registered_source_keeps_its_name_as_the_cache_key(net):
    get_source_store().add(PLATFORM)
    net.add("GET", "http://gw.test/gw/models", {"models": [_registry_entry(param_count=7e9)]})
    detect_model_scale("chat-default", client=_client())
    assert list(load_cached_scales()) == ["standalone/chat-default"]


def test_a_bare_model_is_probed_where_the_chat_call_routes_it(net, monkeypatch):
    """r4-scale-router-4: the chat gate sends a bare model to a registered
    public source that serves it (auto-fallback); detection probed the
    platform registry instead and never asked ollama."""
    from r4 import model_routing

    lab = Source(name="lab", kind="public", url="http://lab.test:11434", provider="ollama")
    get_source_store().add(lab)
    monkeypatch.setattr(model_routing, "discover_public_source", lambda _model, **_kw: "lab")
    show = {"model_info": {"general.parameter_count": 70_000_000_000}}
    net.add("POST", "http://lab.test:11434/api/show", show)
    client = _client()
    route = model_routing.resolve_model_route(client.config, "house-model")
    scale = detect_model_scale("house-model", client=client)
    assert (scale.source, scale.size_class, scale.basis) == (route.source_name, "large", "ollama")
    assert net.paths() == ["/api/show"]


def test_an_unresolvable_route_still_answers_by_name(net):
    client = _client(model_routes={"qwen3-30b-a3b": "public"})  # no public endpoint
    scale = detect_model_scale("qwen3-30b-a3b", client=client)
    assert (scale.size_class, scale.basis, net.requests) == ("medium", "name", [])
    assert "route not resolved (R4Error)" in scale.detail


def _flaky_ollama(monkeypatch) -> list:
    """Refuses the first connection, then answers 70B."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            raise httpx.ConnectError("not up yet")
        return httpx.Response(200, json={"model_info": {"general.parameter_count": 70e9}})

    monkeypatch.setattr(model_scale, "_TRANSPORT", httpx.MockTransport(handler))
    return seen


def _advance_clock(monkeypatch, seconds: float) -> None:
    later = time.time() + seconds
    monkeypatch.setattr(model_scale, "_now", lambda: later)


def test_a_transient_failure_is_asked_again_after_a_short_while(monkeypatch):
    """r4-scale-router-5: a failed probe was memoised for the life of the
    process, pinning 'unknown' in a long-running bridge."""
    seen = _flaky_ollama(monkeypatch)
    first = detect_model_scale("house-model", source=OLLAMA)
    again = detect_model_scale("house-model", source=OLLAMA)
    assert (first.size_class, first.provisional, len(seen)) == ("unknown", True, 1)
    assert again == first  # within the failure TTL: no new request
    _advance_clock(monkeypatch, model_scale.FAILURE_TTL_S + 1)
    healed = detect_model_scale("house-model", source=OLLAMA)
    assert (healed.size_class, healed.basis, healed.provisional, len(seen)) == (
        "large",
        "ollama",
        False,
        2,
    )


def test_a_session_heals_a_provisional_scale_like_search_docs(monkeypatch):
    from r4.companion.retrieval_router import reader_fields

    get_source_store().add(OLLAMA)
    _flaky_ollama(monkeypatch)
    session = SimpleNamespace(client=None)
    first = session_model_scale(session, "house-model@ollama")
    _advance_clock(monkeypatch, model_scale.FAILURE_TTL_S + 1)
    second = session_model_scale(session, "house-model@ollama")
    assert (first.size_class, second.size_class) == ("unknown", "large")
    assert reader_fields("house-model@ollama")["profile"] == "large"
    assert session_model_scale(session, "house-model@ollama") is second  # settled now


def test_a_pin_set_mid_session_reaches_the_router_and_search_docs_alike(net):
    """r4-scale-router-7: search_docs read the new pin at once, the router
    kept the session's cached class for the rest of the session."""
    from r4.companion.retrieval_router import reader_fields

    session = SimpleNamespace(client=_client())
    before = session_model_scale(session, "house-model")
    set_override("house-model", "large")
    after = session_model_scale(session, "house-model")
    searched = reader_fields("house-model", client=session.client)["profile"]
    assert (before.size_class, after.size_class, searched) == ("unknown", "large", "large")
    unset_override("house-model")
    assert session_model_scale(session, "house-model").size_class == "unknown"


def test_endpoint_strings_reach_detail_and_cache_without_control_characters(net):
    """r4-scale-router-12: a hostile /v1/models entry wrote terminal escapes
    (OSC 52 clipboard, erase line, bidi override) into detail and the cache."""
    hostile = "\x1b]52;c;cm0gLXJmIH4K\x07evil\x1b[2K"
    entry = {"id": "m", "owned_by": hostile, "root": "org/x\u202ex\x9b31m", "max_model_len": 8192}
    net.add("GET", "https://rikyu.test/v1/models", {"data": [entry]})
    scale = detect_model_scale("m", source=RIKYU)
    cached = load_cached_scales()["rikyu/m"]
    for text in (scale.detail, cached.detail):
        assert [ch for ch in text if unicodedata.category(ch) in ("Cc", "Cf")] == []
        assert "evil" in text and "org/xx" in text


def test_display_text_keeps_one_line_of_printable_text():
    text = "a\nb\t\x1b[31mc\u200b d"
    assert model_scale.display_text(text) == "a b [31mc d"
    assert model_scale.display_text(text, 5) == "a b ["


class _Trickle(BaseHTTPRequestHandler):
    """A valid 200 JSON reply sent one byte every 1.5 s: each gap is under
    httpx's 2 s read timeout, so no per-phase timeout ever trips."""

    def do_GET(self):  # noqa: N802 - http.server's handler name
        body = json.dumps({"object": "list", "data": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for byte in body:
            if self.server.stop.wait(1.5):
                return
            self.wfile.write(bytes([byte]))
            self.wfile.flush()

    def log_message(self, *_args):
        pass


@pytest.fixture
def trickle(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(model_scale, "_TRANSPORT", None)  # a real local socket
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    server.daemon_threads = True
    server.stop = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.stop.set()
    server.shutdown()
    _join_probes()


def _join_probes() -> None:
    for thread in threading.enumerate():
        if thread.name == model_scale.PROBE_THREAD_NAME:
            thread.join(10)


def test_the_whole_probe_is_bounded_in_wall_clock_time(trickle):
    """r4-scale-router-3: httpx bounds each phase, not the request; a reply
    trickled a byte at a time held detection (and the turn) for ~57 s."""
    port = trickle.server_address[1]
    slow = Source(name="slow", kind="public", url=f"http://127.0.0.1:{port}/v1")
    started = time.monotonic()
    scale = detect_model_scale("llama3.2:3b", source=slow)
    elapsed = time.monotonic() - started
    assert elapsed < PROBE_TIMEOUT_S + 0.5
    assert (scale.size_class, scale.basis, scale.provisional) == ("small", "name", True)
    assert f"probe still running after {PROBE_TIMEOUT_S:g}s" in scale.detail


def test_a_probe_that_ends_after_the_bound_serves_the_next_detection(net, monkeypatch):
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        release.wait(10)
        return httpx.Response(200, json=LLAMA_SHOW)

    monkeypatch.setattr(model_scale, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(model_scale, "PROBE_TIMEOUT_S", 0.2)
    pending = detect_model_scale("house-model", source=OLLAMA)
    release.set()
    _join_probes()
    late = detect_model_scale("house-model", source=OLLAMA)
    assert (pending.size_class, pending.provisional) == ("unknown", True)
    assert (late.size_class, late.basis, late.provisional) == ("small", "ollama", False)
    assert list(load_cached_scales()) == ["ollama/house-model"]


_WRITER = """
import pathlib, sys, time
from r4.model_scale import set_override
go = pathlib.Path(sys.argv[2])
pathlib.Path(sys.argv[3]).touch()
while not go.exists():
    time.sleep(0.005)
for i in range(int(sys.argv[4])):
    set_override(f"{sys.argv[1]}-{i}", "large")
"""


def _start_writers(home, go, count: int) -> list:
    """Two r4 processes that each pin ``count`` models once ``go`` exists;
    returned once both are ready, so their writes overlap."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "XDG_CONFIG_HOME": str(home)}
    ready = [home / f"ready-{tag}" for tag in ("a", "b")]
    argv = [
        [sys.executable, "-c", _WRITER, tag, str(go), str(mark), str(count)]
        for tag, mark in zip("ab", ready)
    ]
    writers = [subprocess.Popen(args, env=env) for args in argv]
    deadline = time.monotonic() + 60
    while not all(mark.exists() for mark in ready) and time.monotonic() < deadline:
        time.sleep(0.01)
    return writers


def test_concurrent_r4_processes_never_lose_each_others_pins(tmp_path):
    """r4-scale-router-11: the lock was a threading.Lock, so two r4 processes
    writing model_scales.json lost each other's entries (155 of 300 kept)."""
    go, count = tmp_path / "go", 150
    writers = _start_writers(tmp_path, go, count)
    go.touch()
    assert [writer.wait(120) for writer in writers] == [0, 0]
    assert len(load_overrides_from(tmp_path)) == 2 * count


def load_overrides_from(home) -> dict:
    return json.loads((home / "r4" / "model_scales.json").read_text())
