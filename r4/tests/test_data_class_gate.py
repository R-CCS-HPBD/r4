"""D-37 client seam: ADR-062 — sensitive projects never dial public routes.

Gap rows D-37/D-51: the server-side gates (venue start + the lease
proxy) are authoritative; this is the workstation's fail-fast refusal —
the request never leaves the machine. No active project / unknown
class / unreachable platform → no gate (never blocks, never invents).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.client import Client
from r4.errors import R4Error


def _config(**over):
    cfg = SimpleNamespace(
        model="test-model",
        gateway_url="https://gw.example",
        api_key="jwt",
        endpoint_url=None,
        endpoint_provider=None,
        model_routes={},
    )
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


def _client(**over):
    return Client(config=_config(**over))


class _FakePM:
    def __init__(self, current):
        self._current = current

    def get_current(self):
        return self._current


def _patch_pm(monkeypatch, current):
    monkeypatch.setattr("r4.project.get_project_manager", lambda: _FakePM(current))


class TestPublicRouteRefusal:
    def test_sensitive_refuses_public_route_by_name(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        client = _client(endpoint_url="https://api.public.example/v1")
        with pytest.raises(R4Error) as exc:
            client._normalize_route({"model": "gpt-x"})
        message = str(exc.value)
        assert "ADR-062" in message
        assert "api.public.example" in message  # the target is named
        assert "sensitive" in message

    def test_platform_route_never_gated(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        client = _client()  # no public endpoint → platform route
        payload, route = client._normalize_route({"model": "test-model"})
        assert route.kind == "platform"
        assert payload["model"] == "test-model"

    def test_nonsensitive_allows_public(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "non_sensitive")
        client = _client(endpoint_url="https://api.public.example/v1")
        _payload, route = client._normalize_route({"model": "gpt-x"})
        assert route.kind == "public"

    def test_unknown_class_allows_public(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "")
        client = _client(endpoint_url="https://api.public.example/v1")
        _payload, route = client._normalize_route({"model": "gpt-x"})
        assert route.kind == "public"


class TestDataClassLookup:
    """The platform row is nested (S2, 2026-10-15): GET /gw/projects/{id}
    answers ``{"status": "ok", "project": {...}}`` — the gate once read the
    top level, found nothing and never fired."""

    def test_reads_data_class_from_nested_project_row(self, monkeypatch):
        _patch_pm(monkeypatch, "p1")
        client = _client()
        monkeypatch.setattr(
            client,
            "get",
            lambda path, **kw: {"status": "ok", "project": {"data_class": "sensitive"}},
        )
        assert client._project_data_class() == "sensitive"
        assert client._data_class_cache["p1"][1] == "sensitive"

    def test_flat_shape_is_not_read(self, monkeypatch):
        """A top-level data_class is NOT consulted: only the server's real
        envelope counts (a forged flat field must not widen the gate)."""
        _patch_pm(monkeypatch, "p1")
        client = _client()
        monkeypatch.setattr(client, "get", lambda path, **kw: {"data_class": "non_sensitive"})
        assert client._project_data_class() == ""

    def test_ttl_cache_coalesces_fetches(self, monkeypatch):
        _patch_pm(monkeypatch, "p1")
        client = _client()
        calls = []

        def fake_get(path, **kw):
            calls.append(path)
            assert path == "/gw/projects/p1"
            return {"status": "ok", "project": {"data_class": "sensitive"}}

        monkeypatch.setattr(client, "get", fake_get)
        assert client._project_data_class() == "sensitive"
        assert client._project_data_class() == "sensitive"
        assert len(calls) == 1  # one fetch per TTL window
        client._data_class_cache.clear()
        client._project_data_class()
        assert len(calls) == 2

    def test_no_active_project_is_empty(self, monkeypatch):
        _patch_pm(monkeypatch, None)
        client = _client()
        assert client._project_data_class() == ""

    def test_platform_unreachable_never_blocks(self, monkeypatch):
        _patch_pm(monkeypatch, "p1")

        def boom(path, **kw):
            raise RuntimeError("gw down")

        client = _client()
        monkeypatch.setattr(client, "get", boom)
        assert client._project_data_class() == ""

    def test_absent_row_never_invents(self, monkeypatch):
        _patch_pm(monkeypatch, "p1")
        client = _client()
        monkeypatch.setattr(
            client, "get", lambda path, **kw: {"status": "ok", "project": {"data_class": ""}}
        )
        assert client._project_data_class() == ""


class TestLocalityGate:
    """ADR-062 amendment (2026-10-15): the workstation gate classifies the
    target — loopback models are local, allowlisted institutional providers
    (Rikyu by default; the site's connect-info list replaces it) are
    institutional. Only a PUBLIC target is refused for a sensitive project."""

    def _sensitive(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")

    def test_loopback_ollama_is_local_and_allowed(self, monkeypatch):
        """The project's gateway is REMOTE (https://gw.example): on the
        workstation a loopback model is local for any project — the
        amendment's remote-gateway clause binds the server gates."""
        self._sensitive(monkeypatch)
        client = _client(endpoint_url="http://127.0.0.1:11434/v1")
        _payload, route = client._normalize_route({"model": "llama3.2:3b"})
        assert route.kind == "public"  # resolved, not refused

    def test_localhost_name_is_local_and_allowed(self, monkeypatch):
        self._sensitive(monkeypatch)
        client = _client(endpoint_url="http://localhost:8080/v1")
        _payload, route = client._normalize_route({"model": "m"})
        assert route.kind == "public"

    def test_rikyu_is_institutional_and_allowed(self, monkeypatch):
        self._sensitive(monkeypatch)
        client = _client(endpoint_url="https://api.rikyu.r-ccs.riken.jp/v1")
        _payload, route = client._normalize_route({"model": "kimi-k3"})
        assert route.kind == "public"

    def test_commercial_remains_refused(self, monkeypatch):
        self._sensitive(monkeypatch)
        client = _client(endpoint_url="https://api.openai.com/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "gpt-5"})

    def test_rikyu_suffix_lookalike_is_public_and_refused(self, monkeypatch):
        self._sensitive(monkeypatch)
        client = _client(endpoint_url="https://not-rikyu.r-ccs.riken.jp/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "m"})

    def test_site_allowlist_replaces_the_packaged_default(self, monkeypatch):
        """A site advertising [] (no institutional providers) makes even the
        Rikyu host public — membership is the site's list, never a label."""
        self._sensitive(monkeypatch)
        _register_platform("https://gw.example", [])
        client = _client(endpoint_url="https://api.rikyu.r-ccs.riken.jp/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "kimi-k3"})

    def test_site_allowlist_admits_its_own_host(self, monkeypatch):
        self._sensitive(monkeypatch)
        _register_platform("https://gw.example", ["llm.example.ac.jp"])
        # The site's own institutional service is admitted…
        client = _client(endpoint_url="https://llm.example.ac.jp/v1")
        _payload, route = client._normalize_route({"model": "m"})
        assert route.kind == "public"
        # …while the packaged default no longer is.
        rikyu = _client(endpoint_url="https://api.rikyu.r-ccs.riken.jp/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            rikyu._normalize_route({"model": "m"})

    def test_lookalike_host_is_refused(self, monkeypatch):
        """Review R4-1 (2026-10-16): httpx dials the WHOLE name
        ``api.rikyu…%2e.127.0.0.1.nip.io`` — a public host — so the gate
        must not read it as Rikyu."""
        self._sensitive(monkeypatch)
        url = "http://api.rikyu.r-ccs.riken.jp%2e.127.0.0.1.nip.io:8080/v1"
        client = _client(endpoint_url=url)
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "kimi-k3"})

    def test_refusal_names_the_project(self, monkeypatch):
        """The widening hint is runnable: it names the gated project, not a
        ``<project>`` placeholder."""
        self._sensitive(monkeypatch)
        monkeypatch.setattr(Client, "_gate_project_id", lambda self: "p-sens")
        client = _client(endpoint_url="https://api.openai.com/v1")
        with pytest.raises(R4Error) as exc:
            client._normalize_route({"model": "gpt-5"})
        assert "r4 project data-class set p-sens non_sensitive" in str(exc.value)
        assert "<project>" not in str(exc.value)


def _register_platform(url, hosts, name="univ"):
    from r4.source import Source, get_source_store

    get_source_store().add(Source(name=name, kind="platform", url=url, institutional_hosts=hosts))


class TestAllowlistIsTheGatedDeployments:
    """Review R4-2 (2026-10-16): the allowlist comes from the platform
    source of the deployment that serves the gated project — the client's
    gateway — never from whichever source is ACTIVE."""

    UNIV = "https://univ.example"
    RIKYU_URL = "https://api.rikyu.r-ccs.riken.jp/v1"

    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.delenv("R4_GATEWAY_URL", raising=False)
        monkeypatch.delenv("R4_SOURCE", raising=False)
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")

    def _univ_says_no_institutional_with_public_active(self):
        from r4.source import Source, get_source_store

        _register_platform(self.UNIV, [])
        store = get_source_store()
        store.add(Source(name="ollama", kind="public", url="http://127.0.0.1:11434"))
        store.set_active("ollama")

    def test_site_empty_list_holds_while_a_public_source_is_active(self):
        self._univ_says_no_institutional_with_public_active()
        client = _client(gateway_url=self.UNIV, endpoint_url=self.RIKYU_URL)
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "kimi-k3"})

    def test_site_empty_list_holds_under_an_env_gateway(self, monkeypatch):
        self._univ_says_no_institutional_with_public_active()
        monkeypatch.setenv("R4_GATEWAY_URL", self.UNIV)
        client = _client(gateway_url=self.UNIV, endpoint_url=self.RIKYU_URL)
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "kimi-k3"})

    def test_site_own_host_admitted_while_another_source_is_active(self):
        from r4.source import get_source_store

        _register_platform(self.UNIV, ["llm.univ.ac.jp"])
        _register_platform("https://other.example", ["llm.other.jp"], name="other")
        get_source_store().set_active("other")
        client = _client(gateway_url=self.UNIV, endpoint_url="https://llm.univ.ac.jp/v1")
        _payload, route = client._normalize_route({"model": "m"})
        assert route.kind == "public"  # resolved, not refused
        other = _client(gateway_url=self.UNIV, endpoint_url="https://llm.other.jp/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            other._normalize_route({"model": "m"})


def _add_public_source(**fields):
    from r4.source import Source, get_source_store

    get_source_store().add(Source(kind="public", **fields))


class TestTheDialledEndpointIsClassified:
    """Review 2026-10-16: the gate classifies where the request really goes.
    A provider object that picks its own endpoint (Bedrock: the AWS SDK and
    its config decide) is public whatever URL its source was registered
    with; a provider object that dials another URL than the route's is
    classified on that URL too."""

    @pytest.fixture(autouse=True)
    def _sensitive(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        monkeypatch.setattr("r4.providers.factory._detect_local_ollama", lambda: None)

    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:9/v1", "https://api.rikyu.r-ccs.riken.jp/v1"]
    )
    def test_bedrock_source_is_public_whatever_its_url(self, url):
        _add_public_source(
            name="br", provider="bedrock", url=url, aws_region="us-east-1", api_key="k"
        )
        with pytest.raises(R4Error, match="ADR-062") as exc:
            _client()._resolve_gated_route("anthropic.claude-x@br")
        assert "chooses its own endpoint" in str(exc.value)

    def test_bedrock_source_never_dials_on_the_blocking_path(self, monkeypatch):
        from r4.providers.bedrock import BedrockProvider

        def dialled(self, *args, **kwargs):
            raise AssertionError("a sensitive project dialled Bedrock")

        monkeypatch.setattr(BedrockProvider, "chat", dialled)
        _add_public_source(
            name="br",
            provider="bedrock",
            url="http://127.0.0.1:9/v1",
            aws_region="us-east-1",
            api_key="k",
        )
        with pytest.raises(R4Error, match="ADR-062"):
            _client()._request({"model": "anthropic.claude-x@br", "messages": []})

    def test_bedrock_source_refused_on_the_collecting_stream(self):
        _add_public_source(
            name="br",
            provider="bedrock",
            url="http://127.0.0.1:9/v1",
            aws_region="us-east-1",
            api_key="k",
        )
        with pytest.raises(R4Error, match="ADR-062"):
            _client()._stream_request_collect(
                {"model": "anthropic.claude-x@br", "messages": []}, []
            )

    def test_ollama_provider_on_loopback_stays_local(self):
        _add_public_source(name="ol", provider="ollama", url="http://127.0.0.1:11434")
        assert _client()._resolve_gated_route("llama3@ol").kind == "public"

    def test_openai_compatible_provider_on_rikyu_stays_institutional(self):
        _add_public_source(name="rk", url="https://api.rikyu.r-ccs.riken.jp/v1", api_key="k")
        assert _client()._resolve_gated_route("kimi-k3@rk").kind == "public"

    def test_a_provider_dialling_elsewhere_is_classified_on_its_own_url(self, monkeypatch):
        """The provider registry is cached per client while the route is read
        from the source store on every call: a provider still bound to a
        public URL must not ride a loopback route's verdict."""
        from r4.providers.openai_compat import OpenAICompatibleProvider

        _add_public_source(name="ol", url="http://127.0.0.1:11434/v1")
        client = _client()
        stale = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", api_key="k")
        monkeypatch.setattr(client, "_provider_for_route", lambda route: stale)
        with pytest.raises(R4Error, match="ADR-062"):
            client._resolve_gated_route("m@ol")

    def test_providers_report_the_url_they_dial(self):
        from r4.providers.bedrock import BedrockProvider
        from r4.providers.ollama import OllamaProvider
        from r4.providers.openai_compat import OpenAICompatibleProvider

        compat = OpenAICompatibleProvider(base_url="https://h.example/v1", api_key="k")
        assert compat.dial_url == "https://h.example"
        assert OllamaProvider(base_url="http://127.0.0.1:11434/").dial_url == (
            "http://127.0.0.1:11434"
        )
        assert BedrockProvider(region="us-east-1").dial_url is None

    def test_userinfo_refusal_says_why_and_hides_the_password(self):
        """A loopback address with ``user:pass@`` is public: the refusal says
        why (it lists loopback models as allowed) and never echoes the
        credentials."""
        client = _client(endpoint_url="http://user:s3cret@localhost:4000/v1")
        with pytest.raises(R4Error) as exc:
            client._normalize_route({"model": "m"})
        message = str(exc.value)
        assert "user name or password" in message
        assert "s3cret" not in message
        assert "localhost:4000" in message


class TestStreamingPathsGated:
    """Every chat path resolves through the gate — streaming turns included
    (the companion's default turn shape rides the collecting stream)."""

    def _sensitive_public_client(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        client = _client(endpoint_url="https://api.public.example/v1")

        def never_dial(route):
            raise AssertionError("a sensitive project dialled a public route")

        monkeypatch.setattr(client, "_http_for_route", never_dial)
        monkeypatch.setattr(client, "_provider_for_route", lambda route: None)
        return client

    def test_stream_request_refuses_before_dial(self, monkeypatch):
        client = self._sensitive_public_client(monkeypatch)
        stream = client._stream_request({"model": "gpt-x", "messages": []})
        with pytest.raises(R4Error, match="ADR-062"):
            next(stream)

    def test_stream_request_collect_refuses_before_dial(self, monkeypatch):
        client = self._sensitive_public_client(monkeypatch)
        with pytest.raises(R4Error, match="ADR-062"):
            client._stream_request_collect({"model": "gpt-x", "messages": []}, [])

    def test_raw_sse_stream_refuses_before_dial(self, monkeypatch):
        client = self._sensitive_public_client(monkeypatch)
        with pytest.raises(R4Error, match="ADR-062"):
            next(client.stream_chat_chunks([], model="gpt-x"))

    def test_stream_request_strips_source_on_platform_route(self, monkeypatch):
        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        client = _client()
        seen = {}

        class _Http:
            def iter_sse_content(self, url, *, json_body, **kw):
                seen["model"] = json_body["model"]
                yield "ok"

        monkeypatch.setattr(client, "_http_for_route", lambda route: _Http())
        monkeypatch.setattr(client, "_provider_for_route", lambda route: None)
        monkeypatch.setattr(client, "_headers_for_route", lambda route, **kw: {})
        assert list(client._stream_request({"model": "test-model", "messages": []})) == ["ok"]
        assert seen["model"] == "test-model"
