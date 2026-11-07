"""Tests for tools/client_route_liveness.py (hardening plan H-10.2).

Small fixtures: an nginx server block, a UMRS route table and r4-like
sources. The tool must flag a planted dead path and pass a live one, in both
modes (edge: ``/gw`` through nginx; direct: ``/umrs`` against the app).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from tools import client_route_liveness as crl

NGINX = textwrap.dedent(
    """
    server {
        listen ${GATEWAY_PORT};
        location /health {
            default_type application/json;
            return 200 '{"status": "healthy"}';
        }
        location /gw/jobs/ {
            rewrite ^/gw/jobs/(.*)$ /umrs/jobs/$1 break;
            proxy_pass http://umrs;
        }
        location = /gw/jobs {
            rewrite ^ /umrs/jobs break;
            proxy_pass http://umrs;
        }
        location /gw/files/upload {
            proxy_pass http://umrs/umrs/files/upload;
        }
        location /gw/v1/ {
            return 404;
        }
        location ~ ^/gw/v1/(models)(/.*)?$ {
            rewrite ^/gw/v1/(.*)$ /umrs/$1 break;
            proxy_pass http://umrs;
        }
        location ~ ^/gw/knowledge/k2/(?<kg_path>.*)$ {
            return 308 /gw/knowledge/ingest/$kg_path$is_args$args;
        }
        location /gw/knowledge/ingest/ {
            rewrite ^/gw/knowledge/ingest/(.*)$ /umrs/k2/$1 break;
            proxy_pass http://umrs;
        }
        location /gw/stream/ {
            proxy_pass $upstream_url;
        }
        location @error401 {
            return 401;
        }
    }
    """
)

ROUTES = [
    {"path": "/umrs/jobs", "methods": ["GET"]},
    {"path": "/umrs/jobs", "methods": ["POST"]},
    {"path": "/umrs/jobs/{job_id}", "methods": ["GET"]},
    {"path": "/umrs/files/upload", "methods": ["POST"]},
    {"path": "/umrs/models", "methods": ["GET"]},
    {"path": "/umrs/k2/ingest", "methods": ["POST"]},
    {"path": "/umrs/grants/{grant_id}", "methods": ["DELETE"]},
]


def _check(source: str, rel: str = "client.py", modes=("edge", "direct")) -> crl.Report:
    paths = crl.scan_module(rel, textwrap.dedent(source))
    return crl.check(paths, crl.parse_nginx(NGINX), crl.load_routes(ROUTES), modes)


def _kinds(report: crl.Report) -> set[tuple[str, str]]:
    return {(f.kind, f.template) for f in report.findings}


def test_flags_planted_dead_path_and_passes_live_one():
    report = _check(
        """
        class Client:
            def list_jobs(self):
                return self.http.get(f"{self._config.gateway_url}{self._api_prefix}/jobs")

            def planted(self):
                return self.http.get(f"{self._config.gateway_url}{self._api_prefix}/nowhere/x")
        """
    )
    assert report.paths == 2
    assert _kinds(report) == {
        ("EDGE_NO_LOCATION", "{@api}/nowhere/x"),
        ("DIRECT_NO_ROUTE", "{@api}/nowhere/x"),
    }
    assert {f.mode for f in report.findings} == {"edge", "direct"}


def test_hard_coded_literals_are_checked_per_mode():
    report = _check(
        """
        def a(client):
            client.get("/gw/jobs")                # live at the edge, dead direct
            client.get("/umrs/jobs")              # live direct, no edge location
            client.post("/gw/jobs/x/no-such")     # dead upstream at the edge
        """
    )
    assert _kinds(report) == {
        ("DIRECT_GW_LITERAL", "/gw/jobs"),
        ("EDGE_NO_LOCATION", "/umrs/jobs"),
        ("EDGE_UPSTREAM_DEAD", "/gw/jobs/x/no-such"),
        ("DIRECT_GW_LITERAL", "/gw/jobs/x/no-such"),
    }


def test_gw_literal_through_the_job_client_translator_is_mode_aware():
    source = """
    class JobClient:
        def get(self, job_id):
            return self._request("GET", f"/gw/jobs/{job_id}")
    """
    (path,) = crl.scan_module("jobs.py", textwrap.dedent(source))
    assert path.translated and path.method == "GET"
    report = crl.check([path], crl.parse_nginx(NGINX), crl.load_routes(ROUTES))
    assert report.ok
    # The same literal in another module is not translated.
    assert not _check(source, rel="other.py").ok


def test_gw_literals_through_the_files_and_vision_url_translators():
    # 2026-10-20 (H-10.2): FilesClient/VisionClient._url (and FilesClient._json
    # delegating to it) map /gw/… → /umrs/… in direct mode, so their literals
    # check as translated, not as DIRECT_GW_LITERAL.
    source = """
    class FilesClient:
        def up(self, path):
            return self._url("/gw/files/upload")

        def bucket(self):
            return self._json("POST", "/gw/buckets", body={})
    """
    up, bucket = crl.scan_module("files.py", textwrap.dedent(source))
    assert up.translated and bucket.translated
    report = crl.check([up, bucket], crl.parse_nginx(NGINX), crl.load_routes(ROUTES))
    assert not [f for f in report.findings if f.kind == "DIRECT_GW_LITERAL"]

    vision_source = """
    class VisionClient:
        def submit(self):
            return self._url("/gw/vision/infer/submit")
    """
    (submit,) = crl.scan_module("vision.py", textwrap.dedent(vision_source))
    assert submit.translated


def test_method_mismatch_is_reported():
    report = _check(
        """
        def a(client, gid):
            client.delete(f"/umrs/grants/{gid}")
            client.post(f"/umrs/grants/{gid}")
        """,
        modes=("direct",),
    )
    assert [(f.kind, f.method) for f in report.findings] == [("DIRECT_METHOD", "POST")]


def test_nginx_location_selection_follows_nginx_order():
    locations = crl.parse_nginx(NGINX)
    assert crl.select_location(locations, "/gw/jobs").modifier == "="
    assert crl.select_location(locations, "/gw/jobs/1").pattern == "/gw/jobs/"
    # A regex location beats a shorter matching prefix (the /gw/v1 guard).
    assert crl.select_location(locations, "/gw/v1/models").modifier == "~"
    assert crl.select_location(locations, "/gw/v1/other").return_code == 404
    assert crl.select_location(locations, "/nowhere") is None
    assert all(not loc.pattern.startswith("@") for loc in locations)


def test_edge_resolution_rewrites_redirects_and_proxy_uris():
    locations = crl.parse_nginx(NGINX)
    assert crl.resolve_edge(locations, "/gw/jobs/abc").upstream == "/umrs/jobs/abc"
    assert crl.resolve_edge(locations, "/gw/files/upload").upstream == "/umrs/files/upload"
    assert crl.resolve_edge(locations, "/gw/knowledge/k2/ingest").upstream == "/umrs/k2/ingest"
    assert crl.resolve_edge(locations, "/gw/v1/models").upstream == "/umrs/models"
    assert crl.resolve_edge(locations, "/gw/v1/other").kind == "blocked"
    assert crl.resolve_edge(locations, "/health").kind == "edge_local"
    assert crl.resolve_edge(locations, "/gw/stream/x").kind == "dynamic"


def test_dynamic_upstream_is_unverified_not_a_finding():
    report = _check('def a(c):\n    c.get("/gw/stream/x")\n', modes=("edge",))
    assert report.ok and len(report.unverified) == 1


def test_builders_bindings_and_bases_compose_paths():
    source = """
    _BASE = "/gw/jobs"

    class K:
        def _jobs_url(self, path):
            return f"{self._client._api_prefix}/jobs/{path}"

        def one(self, job_id):
            return self.http.get(self._jobs_url(job_id))

        def two(self):
            prefix = self._client._api_prefix
            url = f"{self._config.gateway_url}{prefix}/jobs"
            return self.http.post(url)

        def three(self):
            return self.http.get(f"{_BASE}/x/zzz")
    """
    paths = crl.scan_module("k.py", textwrap.dedent(source))
    templates = {(p.template, p.method, p.open_tail) for p in paths}
    assert ("{@api}/jobs/{job_id}", "GET", False) in templates  # builder call
    assert ("{@api}/jobs", "POST", False) in templates  # prefix alias + url var
    assert ("/gw/jobs/x/zzz", "GET", False) in templates  # module constant inlined
    assert ("/gw/jobs", "?", True) in templates  # a *_BASE constant is an open prefix
    report = crl.check(paths, crl.parse_nginx(NGINX), crl.load_routes(ROUTES), ("edge",))
    assert _kinds(report) == {("EDGE_UPSTREAM_DEAD", "/gw/jobs/x/zzz")}


def test_retired_not_http_and_non_paths_are_skipped():
    source = '''
    """Docstring mentioning "/gw/nowhere" is not a call."""

    def _retired_surface(name):
        return lambda f: f

    def _helper(client):
        client.get("/gw/nowhere/a")

    _HANDLERS = {"a": _helper}

    @_retired_surface("thing")
    def _cmd_thing(app, args):
        _HANDLERS[args[0]](app.client)

    def _check_gateway_auth_middleware():
        for route in ("/gw/nowhere/b",):
            pass

    def live(client, path):
        if path.startswith("/gw/"):
            pass
        data_dir = "{ROOT}/umrs/demos_private/x"
        client.get("/gw/jobs")
    '''
    paths = crl.scan_module("_cli/doctor.py", textwrap.dedent(source))
    assert [(p.template, p.retired) for p in paths] == [
        ("/gw/nowhere/a", True),
        ("/gw/jobs", False),
    ]


def test_dual_probe_pair_checks_each_twin_in_its_own_mode():
    report = _check('_PATHS = ("/gw/jobs", "/umrs/jobs")\n')
    assert report.paths == 2 and report.ok


def test_cli_warn_exits_zero_and_strict_exits_one(tmp_path, capsys):
    (tmp_path / "server.conf").write_text(NGINX)
    (tmp_path / "routes.json").write_text(json.dumps({"routes": ROUTES}))
    src = tmp_path / "dead.py"
    src.write_text('def a(c):\n    c.get("/gw/nowhere/x")\n')
    base = [str(src), "--nginx", str(tmp_path / "server.conf")]
    base += ["--routes", str(tmp_path / "routes.json")]
    assert crl.main(base) == 0
    assert "EDGE_NO_LOCATION" in capsys.readouterr().out
    assert crl.main([*base, "--strict"]) == 1
    capsys.readouterr()
    assert crl.main([*base, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"] == {"EDGE_NO_LOCATION": 1, "DIRECT_GW_LITERAL": 1}


def _repo_report() -> tuple[list[crl.ClientPath], crl.Report]:
    _count, paths = crl.scan_sources(crl.default_sources())
    locations = crl.parse_nginx(crl.NGINX_CONF.read_text(encoding="utf-8"))
    routes = crl.load_routes(json.loads(crl.ROUTE_BASELINE.read_text())["routes"])
    return paths, crl.check(paths, locations, routes)


def test_repo_run_lists_known_offenders():
    """Live run over r4/src/r4. The `_cli/usage.py` `/umrs/usage/*` offenders
    were the cleanup C-2(iii) fix and are dropped per this docstring's own
    instruction (2026-10-19): the pin now asserts they STAY gone."""
    paths, report = _repo_report()
    assert len(paths) > 100
    flagged = {(f.file, f.template, f.kind) for f in report.findings}
    assert ("_cli/usage.py", "/umrs/usage/billing", "EDGE_NO_LOCATION") not in flagged
    assert report.retired > 0  # the @_retired_surface TUI handlers


def test_repo_run_lists_review_round_offenders():
    """Review round 2026-10-18: the tool resolves ModelClient._base_url, the
    nginx 301 on /gw/catalog and urllib ``Request(..., method=...)``. The
    provision and catalog offenders it found were fixed the same day
    (8d9380b2), so they are now regression pins; /gw/auth/whoami, the last
    of that review's rows, was fixed 2026-10-20 (the probe now answers from
    ``…/usage/me``, the live identity echo) — the whole class stays at zero.
    """
    _paths, report = _repo_report()
    flagged = {(f.file, f.template, f.kind, f.method) for f in report.findings}
    assert ("_cli/login.py", "/gw/auth/whoami", "EDGE_NO_LOCATION", "GET") not in flagged
    assert not flagged  # H-10.2 hard-fail state: the whole tree checks clean
    fixed = {
        ("models.py", "{@api}/models/{model_name}/provision"),
        ("knowledge.py", "{@api}/catalog"),
    }
    assert not {(f, t) for f, t, _k, _m in flagged} & fixed


def test_repo_run_resolves_journal_prefix_paths():
    """``journal_prefix(client)`` (companion/session_store.py) is the mode
    prefix in every module that imports it."""
    paths, _report = _repo_report()
    journal = {p.file for p in paths if p.template.startswith("{@api}/companion/sessions")}
    assert {"companion/session_store.py", "_cli/sessions.py"} <= journal


def test_repo_run_passes_live_paths_in_warn_mode():
    paths, report = _repo_report()
    loops = {(p.file, p.line) for p in paths if p.template == "{@api}/loops"}
    assert loops
    assert not loops & {(f.file, f.line) for f in report.findings}
    assert crl.main([]) == 0  # WARN mode never fails the run


def test_default_sources_exempt_venue_rail():
    rels = {p.relative_to(crl.R4_PKG).as_posix() for p in crl.default_sources()}
    assert "venue_rail.py" not in rels and "client.py" in rels
    assert Path(crl.R4_PKG / "venue_rail.py").exists()


MODELS_NGINX = textwrap.dedent(
    """
    server {
        location /gw/models/ {
            rewrite ^/gw/models/(.*)$ /umrs/models/$1 break;
            proxy_pass http://umrs;
        }
        location /gw/catalog/ {
            rewrite ^/gw/catalog/(.*)$ /umrs/catalog/$1 break;
            proxy_pass http://umrs;
        }
        location /gw/ {
            return 404;
        }
    }
    """
)
MODELS_ROUTES = [
    {"path": "/umrs/models/{model_id}", "methods": ["GET"]},
    {"path": "/umrs/endpoints/provision", "methods": ["POST"]},
    {"path": "/umrs/catalog/search", "methods": ["GET"]},
]


def _check_models(paths: list[crl.ClientPath]) -> crl.Report:
    locations = crl.parse_nginx(MODELS_NGINX)
    return crl.check(paths, locations, crl.load_routes(MODELS_ROUTES))


def test_self_property_base_url_resolves_into_the_call_site():
    source = """
    class ModelClient:
        @property
        def _base_url(self) -> str:
            return f"{self._config.gateway_url}{self._api_prefix}/models"

        def get(self, model_id):
            return self._http.get(f"{self._base_url}/{model_id}")

        def provision(self, model_name):
            return self._http.post(f"{self._base_url}/{model_name}/provision")
    """
    paths = crl.scan_module("models.py", textwrap.dedent(source))
    assert ("{@api}/models/{model_name}/provision", "POST") in {
        (p.template, p.method) for p in paths
    }
    report = _check_models(paths)
    dead = "{@api}/models/{model_name}/provision"
    assert _kinds(report) == {("EDGE_UPSTREAM_DEAD", dead), ("DIRECT_NO_ROUTE", dead)}


def test_prefix_function_counts_across_modules_and_unresolved_base_is_listed(tmp_path):
    (tmp_path / "store.py").write_text(
        "def journal_prefix(client):\n"
        '    return str(getattr(client, "_api_prefix", "") or "") or "/umrs"\n'
    )
    (tmp_path / "cli.py").write_text(
        "from store import journal_prefix\n\n"
        "def show(client, sid):\n"
        '    client.get(f"{journal_prefix(client)}/models/{sid}")\n'
        '    client.get(f"{client.base}/models/{sid}/x")\n'
        '    client.get(f"{client.base}/v1/chat/completions")\n'
    )
    count, paths = crl.scan_sources(sorted(tmp_path.glob("*.py")), root=tmp_path)
    assert count == 2
    assert [(p.file, p.template, p.method, p.unresolved) for p in paths][:1] == [
        ("cli.py", "{@api}/models/{sid}", "GET", False)
    ]
    report = _check_models(paths)
    assert (report.findings, report.paths) == ([], 1)
    # the /models path behind an unknown base is listed; /v1 (a model server's
    # OpenAI API) is not a platform family
    assert [u.split(" ")[1] for u in report.unverified] == ["{?}/models/{sid}/x"]


def test_slashless_uri_follows_the_nginx_auto_redirect():
    locations = crl.parse_nginx(MODELS_NGINX)
    res = crl.resolve_edge(locations, "/gw/catalog")
    assert (res.kind, res.upstream) == ("umrs", "/umrs/catalog/")
    assert res.location.startswith("301 to /gw/catalog/")
    report = _check_models(crl.scan_module("k.py", 'def a(c):\n    c.get("/gw/catalog")\n'))
    assert {f.kind for f in report.findings} == {"EDGE_UPSTREAM_DEAD", "DIRECT_GW_LITERAL"}
    # an exact location of the slashless name wins over the redirect
    exact = crl.parse_nginx(
        MODELS_NGINX.replace("server {", "server {\n location = /gw/catalog { return 410; }", 1)
    )
    assert crl.resolve_edge(exact, "/gw/catalog").kind == "blocked"


def test_method_keyword_is_read_and_head_rides_on_get():
    source = """
    import urllib.request

    def a(url):
        req = urllib.request.Request(f"{url}/gw/jobs/x", method="DELETE")
        return urllib.request.urlopen(req)

    def b(url):
        return urllib.request.urlopen(urllib.request.Request(f"{url}/gw/jobs/y", method="HEAD"))
    """
    paths = crl.scan_module("login.py", textwrap.dedent(source))
    assert [(p.template, p.method) for p in paths] == [
        ("/gw/jobs/x", "DELETE"),
        ("/gw/jobs/y", "HEAD"),
    ]
    report = crl.check(paths, crl.parse_nginx(NGINX), crl.load_routes(ROUTES), ("edge",))
    assert _kinds(report) == {("EDGE_METHOD", "/gw/jobs/x")}
