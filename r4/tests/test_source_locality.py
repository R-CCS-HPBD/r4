"""ADR-062 amendment (2026-10-15): derived source locality.

A source's locality is COMPUTED from its URL — never stored as user input
— and from exactly the host the HTTP client dials (2026-10-16: a URL whose
host is not plainly one host is public). The institutional allowlist is
the advertisement of the deployment that serves the gated project
(connect-info, cached on that deployment's platform source); the packaged
default covers Rikyu; a stored empty list is honoured as "this site has no
institutional providers" (it does NOT fall back to the packaged default).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:11434/v1", "local"),
        ("http://localhost:8080", "local"),
        ("http://[::1]:11434", "local"),
        ("http://127.1/v1", "local"),  # shorthand loopback
        ("https://api.rikyu.r-ccs.riken.jp/v1", "institutional"),
        ("https://api.rikyu.r-ccs.riken.jp:8443", "institutional"),
        ("https://api.openai.com/v1", "public"),
        ("https://api.anthropic.com", "public"),
        ("https://not-rikyu.r-ccs.riken.jp", "public"),
        ("https://bedrock.placeholder.internal", "public"),
        ("", "public"),
    ],
)
def test_classify_url(url, expected):
    from r4.source_locality import classify_url

    assert classify_url(url) == expected


def test_packaged_default_is_rikyu():
    from r4.source_locality import PACKAGED_INSTITUTIONAL_HOSTS

    assert "api.rikyu.r-ccs.riken.jp" in PACKAGED_INSTITUTIONAL_HOSTS


RIKYU = "api.rikyu.r-ccs.riken.jp"

# 2026-10-16 (review R4-1): the host classified must be the host the HTTP
# client dials. Each URL below once read as a trusted host while httpx
# dialled another name (or a parser other than httpx would): all are public.
PARSER_DIFFERENTIAL_URLS = (
    f"http://{RIKYU}%2e.127.0.0.1.nip.io:8080/v1",  # the reviewer's exploit
    f"https://{RIKYU}%2e.1.1.1.1.nip.io/v1",
    f"http://{RIKYU}%25.evil.example/v1",
    f"http://{RIKYU}%.evil.example/v1",
    f"http://{RIKYU}%2F.evil.example/v1",
    f"http://{RIKYU}%40evil.example/v1",  # an encoded userinfo '@'
    f"http://{RIKYU}%00/v1",
    "http://localhost%2e.evil.example:4000/v1",  # once "local"
    "http://127.0.0.1%2e.evil.example/v1",
    "http://127.0.0.1%00/v1",
    # userinfo: no inference endpoint needs it; parsers disagree on it
    f"http://evil.example@{RIKYU}/v1",
    f"http://a@evil.example@{RIKYU}/v1",
    f"http://evil.example\\@{RIKYU}/v1",  # WHATWG parsers read evil.example
    f"http://{RIKYU}:443@evil.example/v1",
    f"http://{RIKYU}@evil.example/v1",
    "http://user:pass@127.0.0.1:4000/v1",
    # not one plain host / not dialled over http(s)
    f"http://{RIKYU} .evil.example/v1",
    f"http://{RIKYU}\\.evil.example/v1",
    f"http://[{RIKYU}]/v1",
    "http://ａｐｉ.rikyu.r-ccs.riken.jp/v1",  # full-width: httpx refuses it
    f"ftp://{RIKYU}/v1",
    f"file://{RIKYU}/v1",
)


@pytest.mark.parametrize("url", PARSER_DIFFERENTIAL_URLS)
def test_parser_differential_urls_are_public(url):
    from r4.source_locality import classify_url

    assert classify_url(url) == "public"
    # …even when the site lists the look-alike's visible prefix.
    assert classify_url(url, hosts=frozenset({RIKYU, "localhost"})) == "public"


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://{RIKYU}./v1", "institutional"),  # trailing dot: the same FQDN
        (f"https://{RIKYU.upper()}/v1", "institutional"),
        ("https://api。rikyu。r-ccs。riken。jp/v1", "institutional"),  # httpx maps 。
        (f"{RIKYU}:443/v1", "institutional"),  # bare host[:port] reads as http
        ("http://[::1%25lo]:8080/v1", "local"),  # an IPv6 zone is not the address
        ("http://[::ffff:127.0.0.1]/v1", "local"),
        ("http://0x7f.0.0.1/v1", "local"),
        ("http://2130706433/v1", "local"),
        ("http://LOCALHOST./v1", "local"),
        ("http://ollama.localhost:11434/v1", "local"),
        ("127.0.0.1:11434", "local"),
    ],
)
def test_plain_spellings_still_classify(url, expected):
    from r4.source_locality import classify_url

    assert classify_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        f"https://{RIKYU}/v1",
        f"https://{RIKYU}./v1",
        "http://127.0.0.1:11434/v1",
        "http://[::1]:11434",
        "http://localhost:8080",
    ],
)
def test_the_classified_host_is_the_dialled_host(url):
    """A URL that is not public names exactly the host httpx dials."""
    import ipaddress

    import httpx

    from r4.source_context import dialed_host

    dialled = httpx.URL(url).raw_host.decode("ascii").lower().rstrip(".")
    try:
        dialled = str(ipaddress.ip_address(dialled))
    except ValueError:
        pass
    assert dialed_host(url) == dialled


GW = "https://univ.example"


def _add(source):
    from r4.source import get_source_store

    get_source_store().add(source)


def _platform(name="univ", url=GW, hosts=None):
    from r4.source import Source

    return Source(name=name, kind="platform", url=url, institutional_hosts=hosts)


class TestEffectiveHosts:
    """The allowlist is the one the GATED project's deployment advertised —
    the platform source at the client's gateway, never the active source
    (review R4-2, 2026-10-16)."""

    def test_no_source_for_the_gateway_uses_packaged(self):
        from r4.source_locality import effective_institutional_hosts

        assert effective_institutional_hosts(GW) == frozenset({RIKYU})

    def test_platform_source_without_advertisement_uses_packaged(self):
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(hosts=None))
        assert effective_institutional_hosts(GW) == frozenset({RIKYU})

    def test_empty_advertisement_is_honoured(self):
        """[] is a decision (no institutional hosts), not an absence."""
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(hosts=[]))
        assert effective_institutional_hosts(GW) == frozenset()
        assert effective_institutional_hosts(f"{GW}/") == frozenset()  # same deployment

    def test_advertised_list_replaces_packaged(self):
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(hosts=["LLM.Example.JP ", "other.example.jp"]))
        hosts = effective_institutional_hosts(GW)
        assert hosts == frozenset({"llm.example.jp", "other.example.jp"})

    def test_active_public_source_does_not_hide_the_sites_list(self):
        """The reviewer's case: the site advertised [] and a public source
        is ACTIVE — the site's [] still decides, Rikyu stays public."""
        from r4.source import Source, get_source_store
        from r4.source_locality import classify_url, effective_institutional_hosts

        _add(_platform(hosts=[]))
        _add(Source(name="ollama", kind="public", url="http://127.0.0.1:11434"))
        get_source_store().set_active("ollama")
        hosts = effective_institutional_hosts(GW)
        assert hosts == frozenset()
        assert classify_url(f"https://{RIKYU}/v1", hosts=hosts) == "public"

    def test_another_deployments_list_is_not_used(self):
        """An active platform source of ANOTHER deployment says nothing
        about the gated one."""
        from r4.source import get_source_store
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(name="other", url="https://other.example", hosts=["llm.other.jp"]))
        get_source_store().set_active("other")
        assert effective_institutional_hosts(GW) == frozenset({RIKYU})

    def test_env_gateway_still_uses_that_deployments_list(self, monkeypatch):
        """R4_GATEWAY_URL hides the stored active source; the gated
        deployment's list must still apply."""
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(hosts=["llm.univ.jp"]))
        monkeypatch.setenv("R4_GATEWAY_URL", GW)
        assert effective_institutional_hosts(GW) == frozenset({"llm.univ.jp"})

    def test_two_sources_of_one_deployment_intersect(self):
        """Fail closed: a host counts only when every stored list names it."""
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(name="univ", hosts=["a.univ.jp", "b.univ.jp"]))
        _add(_platform(name="univ-alt", url=f"{GW}/", hosts=["b.univ.jp"]))
        assert effective_institutional_hosts(GW) == frozenset({"b.univ.jp"})

    def test_a_public_source_never_carries_a_list(self):
        from r4.source import Source
        from r4.source_locality import effective_institutional_hosts

        _add(Source(name="evil", kind="public", url=GW, institutional_hosts=["evil.example"]))
        assert effective_institutional_hosts(GW) == frozenset({RIKYU})

    def test_non_string_entries_are_ignored(self):
        from r4.source_locality import _advertised_hosts

        src = SimpleNamespace(kind="platform", institutional_hosts=["A.jp", 5, None, " "])
        assert _advertised_hosts(src) == frozenset({"a.jp"})

    def test_default_gateway_is_the_one_this_process_targets(self, monkeypatch):
        from r4.source_locality import effective_institutional_hosts

        _add(_platform(hosts=[]))
        monkeypatch.setattr("r4.source_locality._current_gateway_url", lambda: GW)
        assert effective_institutional_hosts() == frozenset()

    def test_store_failure_uses_packaged(self, monkeypatch):
        from r4.source_locality import effective_institutional_hosts

        def boom(*a, **k):
            raise RuntimeError("config gone")

        monkeypatch.setattr("r4.source.get_platform_sources", boom)
        assert effective_institutional_hosts(GW) == frozenset({RIKYU})


class TestSourceLocality:
    def test_platform_sources_are_not_classified_public(self):
        """A platform source is the site's own governed route — the
        workstation gate only classifies direct-dial public sources."""
        from r4.source_locality import source_locality

        src = SimpleNamespace(kind="platform", url="https://umrs.example")
        assert source_locality(src) == "platform"

    def test_public_source_uses_the_url(self):
        from r4.source_locality import source_locality

        assert (
            source_locality(SimpleNamespace(kind="public", url="https://api.openai.com"))
            == "public"
        )
        assert (
            source_locality(SimpleNamespace(kind="public", url="https://api.rikyu.r-ccs.riken.jp"))
            == "institutional"
        )
        assert (
            source_locality(SimpleNamespace(kind="public", url="http://127.0.0.1:11434")) == "local"
        )

    def test_the_gated_deployments_list_applies(self):
        from r4.source_locality import source_locality

        _add(_platform(hosts=["llm.univ.jp"]))
        own = SimpleNamespace(kind="public", url="https://llm.univ.jp/v1")
        rikyu = SimpleNamespace(kind="public", url=f"https://{RIKYU}/v1")
        assert source_locality(own, gateway_url=GW) == "institutional"
        assert source_locality(rikyu, gateway_url=GW) == "public"


@pytest.mark.parametrize(
    "url,carries,shown",
    [
        ("http://localhost:4000/v1", False, "http://localhost:4000/v1"),
        ("http://user:s3cret@localhost:4000", True, "http://***@localhost:4000"),
        ("https://a@b@api.example/v1", True, "https://***@api.example/v1"),
        ("http://u:pa/ss@host/v1", True, "http://***@host/v1"),
        ("user:s3cret@127.0.0.1:9", True, "***@127.0.0.1:9"),
        ("", False, ""),
        (None, False, ""),
    ],
)
def test_userinfo_is_detected_and_never_shown(url, carries, shown):
    """The gate's refusal names a legacy route by its URL: a ``user:pass@``
    part is why it is public, and it is never echoed (2026-10-16)."""
    from r4.source_locality import carries_userinfo, display_url

    assert carries_userinfo(url) is carries
    assert display_url(url) == shown
