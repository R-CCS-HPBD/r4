#!/usr/bin/env python3
"""Client route liveness: r4 HTTP paths vs the edge and the UMRS app (H-10.2).

Hardening plan H-10.2 (2026-10-18). Statically extracts the HTTP paths the
r4 client builds (``r4/src/r4/**``: ``client.py``, the sub-clients, ``_cli/*``,
``companion/tools.py``, the TUI commands; ``venue_rail.py`` is exempt) and
checks each one in both modes the client speaks:

* **edge** (``_api_prefix == "/gw"``): the path must select a location in
  ``gateway/nginx/conf.d/server.conf`` (exact, longest prefix, then regex, as
  nginx does; a slashless URI whose slashed twin is a proxied prefix location
  takes nginx's 301 to it, which the r4 HTTP client follows), and the location's ``rewrite`` / ``proxy_pass`` must land on a
  mounted UMRS route (``/gw/<family>/…`` → ``/umrs/<family>/…``);
* **direct** (``_api_prefix == "/umrs"``): the path must match a mounted UMRS
  route. A hard-coded ``/gw/…`` literal is dead in this mode unless the call
  goes through a translator (``JobClient._route``).

What counts as a client path: a string or f-string whose text, after dropping
a leading base-URL placeholder (``{self._config.gateway_url}``), starts with
``/gw/``, ``/umrs/`` or the mode prefix (any expression that reads
``_api_prefix``, or a variable named ``prefix``). Placeholders become path
parameters; a placeholder glued to the end (``/grants{suffix}``) or a
trailing ``/{path}`` makes the template an open prefix. Local and module
string bindings are inlined, and same-module URL builders
(``def _knowledge_url(self, path): return f"{…}/knowledge/{path}"``) are
composed with their literal arguments. ``self.<name>`` placeholders resolve
through same-module properties / zero-argument methods that return a string
(``ModelClient._base_url``), and a call to a function whose return reads
``_api_prefix`` (``journal_prefix(client)``, found across all scanned
modules) is the mode prefix. A path whose leading base placeholder stays
unresolved is listed as unverified when its first segment is a UMRS family. Literals inside a slash handler that
``@_retired_surface`` replaced (and the helpers only it reaches), or passed
to ``_raise_retired_http``, are counted as retired, not checked.

The UMRS route table is ``tools/route_inventory.base.json`` (hermetic);
``--live`` builds it from the sidecar app like ``tools/route_inventory.py``.

Findings (each per call site and mode):
  EDGE_NO_LOCATION    no nginx location serves the path
  EDGE_BLOCKED        the location answers 4xx itself (e.g. the /gw/v1 guard)
  EDGE_UPSTREAM_DEAD  the location rewrites to an unmounted UMRS path
  EDGE_METHOD         upstream path mounted, but not for this HTTP method
  DIRECT_GW_LITERAL   hard-coded /gw path: 404 against UMRS directly
  DIRECT_NO_ROUTE     no mounted UMRS route for the path
  DIRECT_METHOD       path mounted, but not for this HTTP method

Enforcement: WARN (exit 0) by default; ``--strict`` exits 1 on any finding.
It goes hard-fail after cleanup C-2(iii)/(v) fix today's offenders (plan
H-10.2).

Usage:
    python tools/client_route_liveness.py            # text report
    python tools/client_route_liveness.py --json     # JSON report
    python tools/client_route_liveness.py --strict   # exit 1 on findings
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
R4_PKG = REPO_ROOT / "r4" / "src" / "r4"
NGINX_CONF = REPO_ROOT / "gateway" / "nginx" / "conf.d" / "server.conf"
ROUTE_BASELINE = REPO_ROOT / "tools" / "route_inventory.base.json"

#: Paths relative to ``r4/src/r4``. venue_rail.py speaks the venue rail's own
#: worker routes, not the platform edge (plan H-10.2).
EXEMPT_FILES = frozenset({"venue_rail.py"})
#: (file, top-level function) whose path literals are not HTTP calls.
NOT_HTTP: dict[tuple[str, str], str] = {
    ("_cli/doctor.py", "_check_gateway_auth_middleware"): "searches server.conf text",
}
#: (file, call name): ``/gw/…`` literals passed here are mapped to the mode
#: prefix (``JobClient._request`` → ``_route``; the ``_url`` helpers of
#: ``FilesClient``/``VisionClient`` map ``/gw/…`` → ``/umrs/…`` in direct
#: mode, 2026-10-20 plan H-10.2).
GW_TRANSLATING_CALLS = frozenset(
    {
        ("jobs.py", "_request"),
        ("files.py", "_url"),
        ("files.py", "_json"),  # delegates to _url
        ("vision.py", "_url"),
    }
)
RETIRED_DECORATORS = frozenset({"_retired_surface"})
RETIRED_CALLS = frozenset({"_raise_retired_http"})

PREFIX = "{@api}"
PREFIX_NAMES = frozenset({"prefix", "api_prefix"})
UNRESOLVED = "{?}"
#: First segments that, behind an unresolved base, are not platform paths:
#: ``{endpoint}/v1/chat/completions`` speaks a model server's OpenAI API.
FOREIGN_FAMILIES = frozenset({"v1"})
EDGE_ROOT = "/gw"
DIRECT_ROOT = "/umrs"
SAMPLE = "__p__"
OPEN_TAIL_NAMES = frozenset({"path", "suffix", "subpath", "rest", "tail"})
HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete"})
REQUEST_HELPERS = frozenset({"request", "_request", "_json", "stream", "_http_json"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
STRING_OPS = frozenset({"startswith", "endswith", "replace", "removeprefix", "split", "find"})
UMRS_UPSTREAMS = frozenset({"umrs", "a2l"})
SIDECAR_UPSTREAMS = frozenset({"auth_sidecar"})

FINDING_KINDS = (
    "EDGE_NO_LOCATION",
    "EDGE_BLOCKED",
    "EDGE_UPSTREAM_DEAD",
    "EDGE_METHOD",
    "DIRECT_GW_LITERAL",
    "DIRECT_NO_ROUTE",
    "DIRECT_METHOD",
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientPath:
    file: str
    line: int
    template: str  # e.g. "{@api}/jobs/{job_id}" or "/gw/rag/stats"
    method: str = "?"
    open_tail: bool = False
    translated: bool = False  # /gw literal mapped to the mode prefix
    dual: bool = False  # sits next to its /gw|/umrs twin (tries both)
    retired: bool = False
    unresolved: bool = False  # base placeholder not resolved: "{?}/<family>/…"


@dataclass(frozen=True)
class Finding:
    kind: str
    mode: str
    file: str
    line: int
    template: str
    method: str
    detail: str


@dataclass
class Report:
    files_scanned: int = 0
    paths: int = 0
    retired: int = 0
    unverified: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Extraction (r4 sources)
# ---------------------------------------------------------------------------


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _single_target(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        return target.id if isinstance(target, ast.Name) else None
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return None


def _bindings(nodes: Iterable[ast.AST]) -> dict[str, ast.expr | None]:
    """Name → its only assigned value (None when assigned more than once)."""
    out: dict[str, ast.expr | None] = {}
    for node in nodes:
        name = _single_target(node) if isinstance(node, ast.stmt) else None
        value = getattr(node, "value", None)
        if name is None or value is None:
            continue
        out[name] = None if name in out else value
    return out


def _owner_name(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return stmt.name
    return _single_target(stmt)


def _top_level_refs(tree: ast.Module) -> dict[str, list[str | None]]:
    """Name → owners (top-level def/assign names) of every load of it."""
    refs: dict[str, list[str | None]] = {}
    for stmt in tree.body:
        owner = _owner_name(stmt)
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and node.id != owner:
                refs.setdefault(node.id, []).append(owner)
    return refs


def _decorated_retired(stmt: ast.stmt) -> bool:
    decorators = getattr(stmt, "decorator_list", [])
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id in RETIRED_DECORATORS:
            return True
    return False


def _only_retired_owners(owners: list[str | None], retired: set[str]) -> bool:
    return bool(owners) and all(o in retired for o in owners)


def _retired_owners(tree: ast.Module) -> set[str]:
    """Top-level names reached only from ``@_retired_surface`` handlers."""
    retired = {_owner_name(s) or "" for s in tree.body if _decorated_retired(s)}
    names = {_owner_name(s) or "" for s in tree.body} - {""}
    refs = _top_level_refs(tree)
    grown = True
    while grown:
        new = {n for n in names - retired if _only_retired_owners(refs.get(n, []), retired)}
        retired |= new
        grown = bool(new)
    return retired - {""}


class _Module:
    """One parsed r4 module: parents, scopes, builders, retired owners."""

    def __init__(self, rel: str, tree: ast.Module, prefix_funcs: Iterable[str] = ()) -> None:
        self.rel = rel
        self.tree = tree
        self.parents = _parent_map(tree)
        self.module_bindings = _bindings(tree.body)
        self.retired = _retired_owners(tree)
        self.prefix_funcs = set(prefix_funcs) | prefix_functions(tree)
        self.attrs = _string_attrs(tree)
        self._func_bindings: dict[ast.AST, dict[str, ast.expr | None]] = {}
        self.builders: dict[str, ast.JoinedStr] = {}
        self.builders = self._find_builders()

    def enclosing(self, node: ast.AST, kinds: tuple[type, ...]) -> ast.AST | None:
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, kinds):
            cur = self.parents.get(cur)
        return cur

    def owner(self, node: ast.AST) -> str | None:
        cur = node
        while cur is not None and self.parents.get(cur) is not self.tree:
            cur = self.parents.get(cur)
        return _owner_name(cur) if isinstance(cur, ast.stmt) else None

    def lookup(self, name: str, node: ast.AST) -> ast.expr | None:
        func = self.enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if func is not None:
            scope = self._func_bindings.get(func)
            if scope is None:
                scope = self._func_bindings[func] = _bindings(ast.walk(func))
            if name in scope:
                return scope[name]
        return self.module_bindings.get(name)

    def _find_builders(self) -> dict[str, ast.JoinedStr]:
        """Private helpers that append their argument to a client path."""
        out: dict[str, ast.JoinedStr] = {}
        for func in ast.walk(self.tree):
            if not isinstance(func, ast.FunctionDef) or not func.name.startswith("_"):
                continue
            ret = _builder_return(func)
            base = _render(self, ast.JoinedStr(values=ret.values[:-1])) if ret else None
            if base is not None and normalize(base + "x") is not None:
                out[func.name] = ret  # type: ignore[assignment]
        return out


def _returns_prefix(func: ast.AST) -> bool:
    """A function whose return reads ``_api_prefix`` without building a path
    (``return str(getattr(client, "_api_prefix", "") or "") or "/umrs"``)."""
    values = [n.value for n in ast.walk(func) if isinstance(n, ast.Return) and n.value]
    return any(not isinstance(v, ast.JoinedStr) and "_api_prefix" in ast.unparse(v) for v in values)


def prefix_functions(tree: ast.Module) -> set[str]:
    """Module-level functions that return the mode prefix."""
    funcs = (s for s in tree.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return {f.name for f in funcs if _returns_prefix(f)}


def _string_return(func: ast.AST) -> ast.expr | None:
    """The str / f-string a ``(self)``-only method returns, else None."""
    if not isinstance(func, ast.FunctionDef) or len(func.args.args) != 1:
        return None
    rets = [n.value for n in ast.walk(func) if isinstance(n, ast.Return)]
    value = rets[0] if len(rets) == 1 else None
    return value if _is_string(value) else None


def _is_string(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return isinstance(node, ast.JoinedStr)


def _string_attrs(tree: ast.Module) -> dict[str, ast.expr | None]:
    """Method/property name → the string it returns (None when two classes
    of the module define it differently)."""
    out: dict[str, ast.expr | None] = {}
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for func in cls.body:
            value = _string_return(func)
            if value is not None:
                name = func.name  # type: ignore[attr-defined]
                out[name] = None if name in out else value
    return out


def _builder_return(func: ast.FunctionDef) -> ast.JoinedStr | None:
    """First f-string in ``func`` that ends with one of its own parameters
    (``return f"{…}/knowledge/{path}"``, ``url = f"{…}{prefix}/{path}"``)."""
    params = {a.arg for a in func.args.args}
    for node in ast.walk(func):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        last = node.values[-1]
        value = last.value if isinstance(last, ast.FormattedValue) else None
        if isinstance(value, ast.Name) and value.id in params:
            return node
    return None


def _placeholder(src: str) -> str:
    src = re.sub(r"(\.\w+\([^()]*\))+$", "", src)
    idents = re.findall(r"[A-Za-z_]\w*", src)
    return "{" + (idents[-1] if idents else "var") + "}"


def _render_name(mod: _Module, expr: ast.Name, depth: int) -> str | None:
    bound = mod.lookup(expr.id, expr)
    if bound is None:
        # An unbound ``prefix`` (a loop over the prefix candidates) is the mode prefix.
        return PREFIX if expr.id in PREFIX_NAMES else None
    rendered = _render(mod, bound, depth + 1) if depth < 3 else None
    if rendered is None and _reads_prefix(mod, bound):
        return PREFIX  # prefix = self._client._api_prefix / journal_prefix(…)
    return rendered


def _reads_prefix(mod: _Module, expr: ast.expr) -> bool:
    if isinstance(expr, ast.Call) and _call_name(expr) in mod.prefix_funcs:
        return True
    return "_api_prefix" in ast.unparse(expr)


def _self_attr(mod: _Module, expr: ast.expr) -> ast.expr | None:
    """``self.<name>`` / ``self.<name>()`` → the string that member returns."""
    target = expr.func if isinstance(expr, ast.Call) and not expr.args else expr
    if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
        return None
    return mod.attrs.get(target.attr) if target.value.id == "self" else None


def _render_known(mod: _Module, expr: ast.expr, depth: int) -> str | None:
    """Builders, prefix functions and string members of the module."""
    if isinstance(expr, ast.Call) and _call_name(expr) in mod.builders:
        built = _builder_text(mod, expr)
        if built is not None:
            return built
    if isinstance(expr, ast.Call) and _call_name(expr) in mod.prefix_funcs:
        return PREFIX
    member = _self_attr(mod, expr)
    return _render(mod, member, depth + 1) if member is not None and depth < 3 else None


def _render_expr(mod: _Module, expr: ast.expr, depth: int) -> str:
    src = ast.unparse(expr)
    known = _render_known(mod, expr, depth)
    if known is not None:
        return known
    if isinstance(expr, ast.Name):
        rendered = _render_name(mod, expr, depth)
        return rendered if rendered is not None else _placeholder(src)
    if "_api_prefix" in src:
        return PREFIX
    return _placeholder(src)


def _literal(text: str) -> str:
    """Literal braces (``"{ROOT}/umrs/demos"``) are not placeholders."""
    return text.replace("{", "\x01").replace("}", "\x02")


def _render(mod: _Module, node: ast.AST, depth: int = 0) -> str | None:
    """Text of a str / f-string node with placeholders, else None."""
    if isinstance(node, ast.Constant):
        return _literal(node.value) if isinstance(node.value, str) else None
    if not isinstance(node, ast.JoinedStr):
        return None
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(_literal(str(value.value)))
        elif isinstance(value, ast.FormattedValue):
            parts.append(_render_expr(mod, value.value, depth))
    return "".join(parts)


_LEADING_VAR = re.compile(
    r"^\{(?!@api\})[^}]*(?:url|base|gateway|host|server|endpoint|origin|uri)[^}]*\}",
    re.IGNORECASE,
)
_GLUED_TAIL = re.compile(r"(?<!/)\{[^}]*\}$")
_ROOTS = ("/gw/", "/umrs/", PREFIX + "/")


def _strip_open_tail(text: str) -> tuple[str, bool]:
    if _GLUED_TAIL.search(text):
        return _GLUED_TAIL.sub("", text), True
    tail = re.search(r"/\{([^}]*)\}$", text)
    if tail and tail.group(1) in OPEN_TAIL_NAMES:
        return text[: tail.start()], True
    if text.endswith("/"):
        return text.rstrip("/"), True
    return text, False


def _segment(seg: str) -> str:
    if "{" in seg and not re.fullmatch(r"\{[^}]*\}", seg):
        return "{" + re.sub(r"\W", "", seg) + "}"
    return seg


def _candidate_text(text: str) -> str | None:
    """Drop leading base-URL placeholders and the query; None if no root."""
    while _LEADING_VAR.match(text):
        text = _LEADING_VAR.sub("", text, count=1)
    text = text.split("?", 1)[0]
    if re.search(r"[\s*]", text):
        return None  # a message or a glob, not a path
    return text if text.startswith(_ROOTS) else None


_UNRESOLVED_BASE = re.compile(r"^(?:\{[^}]*\})+(/[A-Za-z][\w.-]*(?:/[^\s*]*)?)$")


def unresolved_text(text: str) -> str | None:
    """``{?}/<family>/…`` for a path behind a base placeholder that did not
    resolve (``f"{self.base}/jobs"``), else None."""
    match = _UNRESOLVED_BASE.match(text.split("?", 1)[0])
    if match is None or text.startswith(PREFIX):
        return None
    tail, _open = _strip_open_tail(match.group(1))
    return UNRESOLVED + "/".join(_segment(s) for s in tail.split("/"))


def normalize(text: str) -> tuple[str, bool] | None:
    """``(template, open_tail)`` for a client path text, else None."""
    candidate = _candidate_text(text)
    if candidate is None:
        return None
    text, open_tail = _strip_open_tail(candidate)
    head = PREFIX if text.startswith(PREFIX) else ""
    segments = [_segment(s) for s in text[len(head) :].split("/") if s]
    family = segments if head else segments[1:]
    if not family or family[0].startswith("{"):
        return None  # a bare root or a variable family: nothing to check
    return head + "/" + "/".join(segments), open_tail


def _method_constant(node: ast.expr | None) -> str | None:
    value = str(node.value).upper() if isinstance(node, ast.Constant) else ""
    return value if value in METHODS else None


def _method_from_call(call: ast.Call) -> str | None:
    """``http.get(…)``, ``request("POST", …)`` or any ``method="GET"`` keyword
    (``urllib.request.Request(url, method="GET")``)."""
    name = _call_name(call)
    if name in HTTP_VERBS and isinstance(call.func, ast.Attribute):
        return name.upper()
    keyword = next((k.value for k in call.keywords if k.arg == "method"), None)
    if keyword is not None:
        return _method_constant(keyword)
    first = call.args[0] if name in REQUEST_HELPERS and call.args else None
    return _method_constant(first)


def _passes_name(call: ast.AST, name: str) -> bool:
    if not isinstance(call, ast.Call):
        return False
    return any(isinstance(a, ast.Name) and a.id == name for a in call.args[:2])


def _method_via_name(mod: _Module, node: ast.AST) -> str:
    """``url = f"…"`` then ``http.get(url)``: the method of that call."""
    parent = mod.parents.get(node)
    name = _single_target(parent) if isinstance(parent, ast.stmt) else None
    func = mod.enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    if name is None or func is None:
        return "?"
    calls = [c for c in ast.walk(func) if _passes_name(c, name)]
    methods = [m for m in map(_method_from_call, calls) if m]  # type: ignore[arg-type]
    return methods[0] if methods else "?"


def _infer_method(mod: _Module, node: ast.AST) -> str:
    cur = mod.parents.get(node)
    for _ in range(3):
        if cur is None or isinstance(cur, ast.stmt):
            break
        if isinstance(cur, ast.Call):
            method = _method_from_call(cur)
            if method:
                return method
        cur = mod.parents.get(cur)
    return _method_via_name(mod, node)


def _skip_literal(mod: _Module, node: ast.AST) -> bool:
    parent = mod.parents.get(node)
    if isinstance(parent, (ast.JoinedStr, ast.FormattedValue, ast.Compare, ast.Expr)):
        return True
    if isinstance(parent, ast.Call) and _call_name(parent) in STRING_OPS:
        return True
    owner = mod.owner(node)
    return owner is not None and (mod.rel, owner) in NOT_HTTP


def _twin(template: str) -> str:
    if template.startswith("/gw/"):
        return "/umrs/" + template[4:]
    return "/gw/" + template[6:] if template.startswith("/umrs/") else ""


def _is_dual(mod: _Module, node: ast.AST, template: str) -> bool:
    parent = mod.parents.get(node)
    if not isinstance(parent, (ast.Tuple, ast.List)):
        return False
    texts = {_render(mod, elt) for elt in parent.elts}
    return _twin(template) in texts


def _flags(mod: _Module, node: ast.AST) -> tuple[bool, bool]:
    """``(translated, retired)`` from the call the literal is passed to."""
    parent = mod.parents.get(node)
    call = _call_name(parent) if isinstance(parent, ast.Call) else ""
    translated = (mod.rel, call) in GW_TRANSLATING_CALLS
    retired = call in RETIRED_CALLS or mod.owner(node) in mod.retired
    return translated, retired


_BASE_HINT = re.compile(r"base|endpoint", re.IGNORECASE)


def _is_base(mod: _Module, node: ast.AST) -> bool:
    """A base URL others extend (``_A2L_BASE = "/gw/a2l"``, ``base_url=…``)."""
    parent = mod.parents.get(node)
    if isinstance(parent, ast.keyword):
        return bool(parent.arg and _BASE_HINT.search(parent.arg))
    if isinstance(parent, ast.Return):
        func = mod.enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        return func is not None and bool(_BASE_HINT.search(func.name))
    name = _single_target(parent) if isinstance(parent, ast.stmt) else None
    return bool(name and _BASE_HINT.search(name))


def _unresolved_path(mod: _Module, node: ast.AST, text: str | None) -> ClientPath | None:
    template = unresolved_text(text) if text is not None else None
    if template is None or _flags(mod, node)[1]:
        return None  # not a path, or inside a retired handler
    line = getattr(node, "lineno", 0)
    return ClientPath(mod.rel, line, template, _infer_method(mod, node), unresolved=True)


def _make_path(mod: _Module, node: ast.AST, text: str | None) -> ClientPath | None:
    norm = normalize(text) if text is not None else None
    if norm is None:
        return _unresolved_path(mod, node, text)
    template, open_tail = norm
    open_tail = open_tail or _is_base(mod, node)
    translated, retired = _flags(mod, node)
    return ClientPath(
        file=mod.rel,
        line=getattr(node, "lineno", 0),
        template=template,
        method=_infer_method(mod, node),
        open_tail=open_tail,
        translated=translated and template.startswith("/gw/"),
        dual=_is_dual(mod, node, template),
        retired=retired,
    )


def _builder_text(mod: _Module, call: ast.Call) -> str | None:
    ret = mod.builders.get(_call_name(call))
    if ret is None or len(call.args) != 1:
        return None
    base = _render(mod, ast.JoinedStr(values=ret.values[:-1]))
    arg = call.args[0]
    text = _render(mod, arg)
    if text is None and not isinstance(arg, ast.Starred):
        text = _render_expr(mod, arg, 0)  # a variable argument is a path parameter
    return base + text if base is not None and text is not None else None


def scan_module(rel: str, source: str, prefix_funcs: Iterable[str] = ()) -> list[ClientPath]:
    """Every client path one r4 module builds."""
    return _scan_tree(rel, ast.parse(source), prefix_funcs)


def _scan_tree(rel: str, tree: ast.Module, prefix_funcs: Iterable[str]) -> list[ClientPath]:
    mod = _Module(rel, tree, prefix_funcs)
    out: list[ClientPath] = []
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Call) and not isinstance(mod.parents.get(node), ast.FormattedValue):
            path = _make_path(mod, node, _builder_text(mod, node))
        elif isinstance(node, (ast.Constant, ast.JoinedStr)) and not _skip_literal(mod, node):
            path = _make_path(mod, node, _render(mod, node))
        else:
            path = None
        if path is not None:
            out.append(path)
    return out


def default_sources(root: Path = R4_PKG) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py") if p.relative_to(root).as_posix() not in EXEMPT_FILES
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a file outside r4/src/r4 (fixtures, ad-hoc runs)
        return path.name


def scan_sources(paths: Iterable[Path], root: Path = R4_PKG) -> tuple[int, list[ClientPath]]:
    """Two passes: the prefix functions of every module (``journal_prefix``
    is imported across the package), then the paths of each."""
    trees = {_rel(p, root): ast.parse(p.read_text(encoding="utf-8")) for p in paths}
    prefix_funcs = set().union(*map(prefix_functions, trees.values()))
    out = [cp for rel, tree in trees.items() for cp in _scan_tree(rel, tree, prefix_funcs)]
    return len(trees), out


# ---------------------------------------------------------------------------
# NGINX model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    modifier: str  # "=", "^~", "~", "~*" or "" (prefix)
    pattern: str
    line: int
    rewrites: tuple[tuple[str, str], ...] = ()
    proxy_pass: str | None = None
    return_code: int | None = None
    return_target: str | None = None

    def label(self) -> str:
        mod = f"{self.modifier} " if self.modifier else ""
        return f"location {mod}{self.pattern} (server.conf:{self.line})"


_TOKEN = re.compile(
    r"""\#[^\n]*|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[{};]|(?:\$\{[^}]*\}|[^\s;{}'"\#])+"""
)


def _tokens(text: str) -> list[tuple[str, int]]:
    newlines = [i for i, ch in enumerate(text) if ch == "\n"]
    out = []
    for match in _TOKEN.finditer(text):
        tok = match.group(0)
        if tok.startswith("#"):
            continue
        if tok[0] in "'\"":
            tok = tok[1:-1]
        out.append((tok, bisect_right(newlines, match.start()) + 1))
    return out


@dataclass
class _Directive:
    name: str
    args: list[str]
    line: int
    children: list[_Directive] | None = None


def _directive(words: list[tuple[str, int]], children=None) -> _Directive:
    return _Directive(words[0][0], [w for w, _ in words[1:]], words[0][1], children)


def _parse_block(tokens: list[tuple[str, int]], i: int) -> tuple[list[_Directive], int]:
    """Directives up to the matching ``}`` (or the end); returns the next index."""
    out: list[_Directive] = []
    words: list[tuple[str, int]] = []
    while i < len(tokens) and tokens[i][0] != "}":
        tok = tokens[i][0]
        i += 1
        if tok not in ("{", ";"):
            words.append(tokens[i - 1])
            continue
        children = None
        if tok == "{":
            children, i = _parse_block(tokens, i)
        if words:
            out.append(_directive(words, children))
        words = []
    return out, i + 1


def _walk(directives: list[_Directive]) -> Iterable[_Directive]:
    for d in directives:
        yield d
        if d.children:
            yield from _walk(d.children)


def _child_args(d: _Directive, name: str) -> list[list[str]]:
    return [c.args for c in d.children or [] if c.name == name and c.args]


def _return_of(d: _Directive) -> tuple[int | None, str | None]:
    """``(code, redirect target)`` of the location's own ``return``."""
    rets = _child_args(d, "return")
    ret = rets[0] if rets else [""]
    code = int(ret[0]) if ret[0].isdigit() else None
    redirect = code is not None and 300 <= code < 400 and len(ret) > 1
    return code, ret[1] if redirect else None


def _location(d: _Directive) -> Location | None:
    modifier, pattern = (d.args[0], d.args[1]) if len(d.args) == 2 else ("", d.args[0])
    if pattern.startswith("@"):
        return None
    rewrites = tuple((a[0], a[1]) for a in _child_args(d, "rewrite") if len(a) > 1)
    proxies = _child_args(d, "proxy_pass")
    code, target = _return_of(d)
    proxy = proxies[0][0] if proxies else None
    return Location(modifier, pattern, d.line, rewrites, proxy, code, target)


def parse_nginx(text: str) -> list[Location]:
    directives, _ = _parse_block(_tokens(text), 0)
    out = []
    for d in _walk(directives):
        if d.name == "location" and d.args:
            loc = _location(d)
            if loc is not None:
                out.append(loc)
    return out


def _py_regex(pattern: str) -> str:
    return pattern.replace("(?<", "(?P<").replace("(?P<=", "(?<=").replace("(?P<!", "(?<!")


def _regex_match(loc: Location, path: str) -> re.Match[str] | None:
    flags = re.IGNORECASE if loc.modifier == "~*" else 0
    return re.search(_py_regex(loc.pattern), path, flags)


def _selects(loc: Location, path: str, stage: str) -> bool:
    if stage == "exact":
        return loc.modifier == "=" and loc.pattern == path
    if stage == "prefix":
        return loc.modifier in ("", "^~") and path.startswith(loc.pattern)
    return loc.modifier in ("~", "~*") and _regex_match(loc, path) is not None


def _first(locations: list[Location], path: str, stage: str) -> Location | None:
    return next((x for x in locations if _selects(x, path, stage)), None)


def _longest_prefix(locations: list[Location], path: str) -> Location | None:
    prefixes = [x for x in locations if _selects(x, path, "prefix")]
    return max(prefixes, key=lambda x: len(x.pattern), default=None)


def select_location(locations: list[Location], path: str) -> Location | None:
    """nginx order: exact, longest prefix (``^~`` stops), first regex, prefix."""
    exact = _first(locations, path, "exact")
    if exact is not None:
        return exact
    best = _longest_prefix(locations, path)
    if best is not None and best.modifier == "^~":
        return best
    return _first(locations, path, "regex") or best


def _group(match: re.Match[str] | None, name: str) -> str:
    if match is None:
        return ""
    try:
        return match.group(int(name) if name.isdigit() else name) or ""
    except IndexError:  # nginx variable that is not a capture ($args, $is_args)
        return ""


def _expand(template: str, match: re.Match[str] | None) -> str:
    return re.sub(r"\$(\w+)", lambda m: _group(match, m.group(1)), template)


def _apply_rewrite(loc: Location, path: str) -> str | None:
    for regex, replacement in loc.rewrites:
        match = re.search(_py_regex(regex), path)
        if match:
            return _expand(replacement, match)
    return None


def _proxy_parts(proxy: str) -> tuple[str, str]:
    """``(host, uri)`` of ``http://host[/uri]``."""
    rest = proxy.split("://", 1)[-1]
    host, slash, uri = rest.partition("/")
    return host, (slash + uri if slash else "")


@dataclass(frozen=True)
class EdgeResult:
    kind: str  # umrs | no_location | blocked | edge_local | sidecar | dynamic | other
    upstream: str = ""
    location: str = ""


def _upstream(loc: Location, path: str) -> EdgeResult:
    proxy = loc.proxy_pass or ""
    if not proxy or "$" in proxy:
        return EdgeResult("dynamic", "", loc.label())
    host, uri = _proxy_parts(proxy)
    rewritten = _apply_rewrite(loc, path)
    if rewritten is None:
        rewritten = uri + path[len(loc.pattern) :] if uri and loc.modifier == "" else path
    if host in SIDECAR_UPSTREAMS:
        return EdgeResult("sidecar", rewritten, loc.label())
    kind = "umrs" if host in UMRS_UPSTREAMS else "other"
    return EdgeResult(kind, rewritten, loc.label())


def _auto_redirect(locations: list[Location], path: str) -> Location | None:
    """nginx answers ``/a/b`` with 301 to ``/a/b/`` when ``location /a/b/``
    proxies and no location is named exactly ``/a/b`` (wins over shorter
    prefixes and regexes); httpx follows it (``r4._http``)."""
    named = [x for x in locations if x.modifier in ("", "^~", "=")]
    if any(x.pattern == path for x in named):
        return None
    return next((x for x in named if x.pattern == path + "/" and x.proxy_pass), None)


def resolve_edge(locations: list[Location], path: str, depth: int = 0) -> EdgeResult:
    slashed = _auto_redirect(locations, path) if depth < 3 else None
    if slashed is not None:
        res = resolve_edge(locations, path + "/", depth + 1)
        return EdgeResult(res.kind, res.upstream, f"301 to {path}/, then {res.location}")
    loc = select_location(locations, path)
    if loc is None:
        return EdgeResult("no_location")
    if loc.return_code is None:
        return _upstream(loc, path)
    if loc.return_target and depth < 3:
        match = _regex_match(loc, path) if loc.modifier.startswith("~") else None
        return resolve_edge(locations, _expand(loc.return_target, match), depth + 1)
    kind = "edge_local" if loc.return_code < 300 else "blocked"
    return EdgeResult(kind, str(loc.return_code), loc.label())


# ---------------------------------------------------------------------------
# UMRS route table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    path: str
    methods: frozenset[str]

    @property
    def segments(self) -> list[str]:
        return [s for s in self.path.split("/") if s]


def load_routes(rows: Iterable[dict[str, Any]]) -> list[Route]:
    merged: dict[str, set[str]] = {}
    for row in rows:
        merged.setdefault(row["path"], set()).update(row.get("methods") or ["GET"])
    return [Route(path, frozenset(methods)) for path, methods in sorted(merged.items())]


def _segments_match(route: list[str], path: list[str], prefix_ok: bool) -> bool:
    for i, rseg in enumerate(route):
        if rseg.endswith(":path}"):
            return i < len(path) or prefix_ok
        if i >= len(path):
            return prefix_ok
        if not (rseg.startswith("{") or rseg == path[i] or path[i] == SAMPLE):
            return False
    return len(route) == len(path)


def _route_hits(routes: list[Route], path: str, open_tail: bool) -> list[Route]:
    segs = [s for s in path.split("/") if s]
    return [r for r in routes if _segments_match(r.segments, segs, open_tail)]


def match_route(routes: list[Route], path: str, method: str, open_tail: bool) -> str:
    """``ok`` | ``no_route`` | ``method`` for a concrete path."""
    hits = _route_hits(routes, path, open_tail)
    if not hits:
        return "no_route"
    if method == "?" or open_tail:
        return "ok"
    allowed = set().union(*(r.methods for r in hits))
    if "GET" in allowed:
        allowed.add("HEAD")  # Starlette answers HEAD on every GET route
    return "ok" if method in allowed else "method"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _concrete(template: str, root: str) -> str:
    text = template.replace(PREFIX, root, 1)
    return re.sub(r"\{[^}]*\}", SAMPLE, text)


def _finding(cp: ClientPath, kind: str, mode: str, detail: str) -> Finding:
    return Finding(kind, mode, cp.file, cp.line, cp.template, cp.method, detail)


def _edge_target(cp: ClientPath) -> str | None:
    if cp.dual and cp.template.startswith("/umrs/"):
        return None  # the /umrs twin of a try-both pair is the direct leg
    return _concrete(cp.template, EDGE_ROOT)


def _resolve_open(locations: list[Location], path: str, open_tail: bool) -> EdgeResult:
    result = resolve_edge(locations, path)
    if result.kind != "no_location" or not open_tail:
        return result
    deeper = resolve_edge(locations, path + "/" + SAMPLE)
    suffix = "/" + SAMPLE
    if deeper.upstream.endswith(suffix):
        deeper = EdgeResult(deeper.kind, deeper.upstream[: -len(suffix)], deeper.location)
    return deeper


def _edge_route_finding(cp: ClientPath, res: EdgeResult, routes: list[Route]) -> Finding | None:
    verdict = match_route(routes, res.upstream, cp.method, cp.open_tail)
    if verdict == "no_route":
        detail = f"{res.location} sends it to {res.upstream}, which no UMRS route serves"
        return _finding(cp, "EDGE_UPSTREAM_DEAD", "edge", detail)
    if verdict == "method":
        detail = f"{res.upstream} matches a UMRS route, but none for {cp.method}"
        return _finding(cp, "EDGE_METHOD", "edge", detail)
    return None


def check_edge(
    cp: ClientPath, locations: list[Location], routes: list[Route], report: Report
) -> Finding | None:
    path = _edge_target(cp)
    if path is None:
        return None
    res = _resolve_open(locations, path, cp.open_tail)
    if res.kind == "no_location":
        return _finding(cp, "EDGE_NO_LOCATION", "edge", f"no nginx location serves {path}")
    if res.kind == "blocked":
        return _finding(cp, "EDGE_BLOCKED", "edge", f"{res.location} returns {res.upstream}")
    if res.kind in ("dynamic", "other"):
        report.unverified.append(f"{cp.file}:{cp.line} {path} -> {res.location}")
        return None
    if res.kind != "umrs":
        return None
    return _edge_route_finding(cp, res, routes)


def check_direct(cp: ClientPath, routes: list[Route]) -> Finding | None:
    if cp.template.startswith("/gw/") and not cp.translated:
        if cp.dual:
            return None
        detail = "hard-coded /gw path; route it through _api_prefix for direct-UMRS mode"
        return _finding(cp, "DIRECT_GW_LITERAL", "direct", detail)
    path = _concrete(cp.template, DIRECT_ROOT)
    if cp.translated:
        path = DIRECT_ROOT + path[len(EDGE_ROOT) :]
    verdict = match_route(routes, path, cp.method, cp.open_tail)
    if verdict == "no_route":
        return _finding(cp, "DIRECT_NO_ROUTE", "direct", f"no mounted UMRS route for {path}")
    if verdict == "method":
        detail = f"{path} matches a UMRS route, but none for {cp.method}"
        return _finding(cp, "DIRECT_METHOD", "direct", detail)
    return None


def _family_of(path: str, root: str) -> str | None:
    segs = [s for s in path.split("/") if s]
    return segs[1] if len(segs) > 1 and segs[0] == root else None


def _families(locations: list[Location], routes: list[Route]) -> set[str]:
    """First path segments the platform serves (``jobs``, ``models``, …)."""
    named = [x.pattern for x in locations if not x.modifier.startswith("~")]
    found = [_family_of(r.path, "umrs") for r in routes]
    found += [_family_of(pattern, "gw") for pattern in named]
    return {f for f in found if f}


def _note_unresolved(cp: ClientPath, families: set[str], report: Report) -> None:
    family = cp.template[len(UNRESOLVED) :].split("/")[1]
    if family in families - FOREIGN_FAMILIES:
        detail = "base URL placeholder not resolved; path not checked"
        report.unverified.append(f"{cp.file}:{cp.line} {cp.template} -> {detail}")


def check(
    paths: list[ClientPath],
    locations: list[Location],
    routes: list[Route],
    modes: tuple[str, ...] = ("edge", "direct"),
) -> Report:
    families = _families(locations, routes)
    report = Report(paths=sum(1 for cp in paths if not cp.unresolved))
    for cp in paths:
        if cp.unresolved:
            _note_unresolved(cp, families, report)
        elif cp.retired:
            report.retired += 1
        else:
            report.findings.extend(_check_one(cp, locations, routes, modes, report))
    return report


def _check_one(
    cp: ClientPath,
    locations: list[Location],
    routes: list[Route],
    modes: tuple[str, ...],
    report: Report,
) -> list[Finding]:
    found = [
        check_edge(cp, locations, routes, report) if "edge" in modes else None,
        check_direct(cp, routes) if "direct" in modes else None,
    ]
    return [f for f in found if f is not None]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _live_rows() -> list[dict[str, Any]]:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from route_inventory import build_inventory  # type: ignore[import-not-found]

    return build_inventory()


def run(args: argparse.Namespace) -> Report:
    sources = [Path(p) for p in args.paths] or default_sources()
    count, paths = scan_sources(sources)
    locations = parse_nginx(Path(args.nginx).read_text(encoding="utf-8"))
    rows = _live_rows() if args.live else json.loads(Path(args.routes).read_text())["routes"]
    modes = ("edge", "direct") if args.mode == "both" else (args.mode,)
    report = check(paths, locations, load_routes(rows), modes)
    report.files_scanned = count
    return report


def _print_text(report: Report, strict: bool) -> None:
    mode = "STRICT" if strict else "WARN"
    print(f"Client route liveness ({mode}; plan H-10.2)")
    print(f"  files scanned : {report.files_scanned}")
    print(f"  client paths  : {report.paths} ({report.retired} retired, not checked)")
    print(
        f"  unverified    : {len(report.unverified)} (dynamic/non-UMRS upstream, unresolved base)"
    )
    print(f"  findings      : {len(report.findings)} {report.counts()}")
    for kind in FINDING_KINDS:
        rows = [f for f in report.findings if f.kind == kind]
        if rows:
            print(f"\n[{kind}] {len(rows)}")
        for f in sorted(rows, key=lambda x: (x.file, x.line)):
            print(f"  r4/src/r4/{f.file}:{f.line}  {f.method:6} {f.template}  — {f.detail}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("paths", nargs="*", help="r4 source files (default: all of r4/src/r4)")
    parser.add_argument("--json", action="store_true", help="JSON report")
    parser.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    parser.add_argument("--live", action="store_true", help="build routes from the UMRS app")
    parser.add_argument("--mode", choices=("both", "edge", "direct"), default="both")
    parser.add_argument("--nginx", default=str(NGINX_CONF))
    parser.add_argument("--routes", default=str(ROUTE_BASELINE))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run(args)
    if args.json:
        data = asdict(report)
        data["counts"] = report.counts()
        print(json.dumps(data, indent=2))
    else:
        _print_text(report, args.strict)
    return 1 if args.strict and not report.ok else 0


if __name__ == "__main__":
    sys.exit(main())
