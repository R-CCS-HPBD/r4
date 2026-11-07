"""SL-CONF T0 — deployment truth over the stand-alone files themselves.

Plan §3c.4: assertions over `deploy/standalone/` (compose, env, site, script)
that need no stack and run in seconds. Each pin is a defect the live
bring-up and the WS-09 parity pass found, or the contract that kept it from
being worse:

- an env-file value never reached a container (``--env-file`` only interpolates
  ``${...}``): the dev step settings (validation report §11, L1), and three
  hardened "secure values set explicitly" that no container received;
- the host-access contract (§3c.5 #3): hardened publishes the gateway and the
  ZeroMQ planes only, all on loopback;
- uploaded code stays refused outside dev (ADR-074);
- the bundled text embedder is the default, and nothing downloads at runtime;
- §3c.5's bring-up defects: nginx's always-on LITELLM upstream and SeaweedFS's
  IAM flags;
- Postgres under an init, so a killed healthcheck cannot crash it (live
  2026-10-10, ADR-093 eval ingest);
- ADR-093's retrieval knobs reach their services with their documented
  defaults (every knob the retrieval code reads, each default the code's
  own, plus the eval-only query-prefix switch), the cross-encoder is off
  until staged and no profile names a model by hub id, and the script
  carries `migrate`, `reranker pull` and a reranker doctor line (C16);
- the orphan repair (MD-17) is a dry run unless `--apply`, runs in the umrs
  container per explicit project and fails on a module error; doctor hints
  the ollama slot count without failing, and the default stays 1 (MD-11);
- the one-command local path (onboarding plan §2 Phase 1 and §5 Phase 4-C,
  2026-10-15): the quickstart chain and its order, the D-7 RAM table, memory
  in MiB with a tolerance, the macOS podman machine running and sized to the
  need, WSL2 systemd and .wslconfig, host tools and ports, the docker overlay
  and the engine rule, the GPU overlays and a broken NVML, the loopback-only
  native ollama, a short context window never registered silently, the
  profile and engine recorded in the gitignored env/.state, `register`
  piping a 30-day token on stdin only and never running `source use` or
  `auth login`, a smoke test that passes only on real ingest and retrieval,
  the ./r4-local wrapper, and the README and chapter 29 statements;
- project data classes (2026-10-16, wave-2 review spec F1 / DOC-1):
  `register` creates the stack's project row through the gateway
  (POST /gw/projects, token on curl's stdin) before it prints the class and
  the narrowing command for that same project, the compose default reaches
  umrs as non_sensitive, and the manual explains the classes in plain words
  with the operator variables and their packaged default documented.
  The scripts' functions are called from bash with fixed inputs and stub
  engines; nothing reaches this checkout's env/local.env or env/.state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
STANDALONE = ROOT / "deploy" / "standalone"
PROFILES = ("hardened", "dev")


def _compose(name: str = "compose.yaml") -> dict:
    return yaml.safe_load((STANDALONE / name).read_text())


def _env(name: str) -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in (STANDALONE / "env" / name).read_text().splitlines()
        if re.match(r"^[A-Z_][A-Z0-9_]*=", line)
    )
    return dict(pairs)


def _compose_text(profile: str) -> str:
    files = ["compose.yaml"] + (["compose.dev.yaml"] if profile == "dev" else [])
    return "\n".join((STANDALONE / f).read_text() for f in files)


def _core_services() -> dict[str, dict]:
    return {n: s for n, s in _compose()["services"].items() if not s.get("profiles")}


# ── every value an env file sets reaches something ──────────────────────────


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_value_reaches_a_container_or_the_script(profile):
    compose = _compose_text(profile)
    script = (STANDALONE / "standalone.sh").read_text()
    settings = {**_env("base.env"), **_env(f"profile.{profile}.env")}
    decorative = [
        key
        for key in settings
        if not re.search(r"\$\{" + key + r"[:}?-]", compose)
        and not re.search(r"\b" + key + r"\b", script)
    ]
    assert decorative == [], f"{profile}: set but never delivered: {decorative}"


def test_the_dev_overlay_delivers_uploaded_code_only_to_umrs():
    dev_env = _compose("compose.dev.yaml")["services"]["umrs"]["environment"]
    assert dev_env["UMRS_STEP_UPLOADED_CODE"].startswith("${UMRS_STEP_UPLOADED_CODE")
    assert _env("profile.dev.env")["UMRS_STEP_EXECUTOR"] == "inproc"


# ── uploaded code stays refused outside dev (ADR-074) ───────────────────────


def test_hardened_never_relaxes():
    hardened = _env("profile.hardened.env")
    assert hardened["UMRS_AUTH_MODE"] == "strict"
    assert hardened.get("UMRS_ZMQ_AUTH_TYPE") != "none"
    for knob in (
        "UMRS_STEP_UPLOADED_CODE",
        "GATEWAY_ALLOW_INSECURE_DEV_AUTH",
        "UMRS_ZMQ_SESSION_IDENTITY",
        "UMRS_ZMQ_CURVE_ALLOW_ANY_CLIENT_KEY",
    ):
        assert knob not in hardened, f"hardened sets {knob}"


def test_uploaded_code_is_not_enabled_for_both_profiles():
    """site.yaml and compose.yaml apply to hardened too."""
    umrs_env = _compose()["services"]["umrs"]["environment"]
    assert "UMRS_STEP_UPLOADED_CODE" not in umrs_env
    site = yaml.safe_load((STANDALONE / "site.yaml").read_text())
    assert not (site.get("step_executor") or {}).get("uploaded_code")


@pytest.mark.parametrize("profile", PROFILES)
def test_both_profiles_name_their_step_executor(profile):
    assert _env(f"profile.{profile}.env").get("UMRS_STEP_EXECUTOR")


# ── the host-access contract (§3c.5 #3) ─────────────────────────────────────


def _published(services: dict[str, dict]) -> set[str]:
    return {str(port) for svc in services.values() for port in svc.get("ports") or []}


def test_hardened_publishes_the_gateway_and_zeromq_only():
    assert _published(_core_services()) == {
        "127.0.0.1:8080:8080",
        "127.0.0.1:5555:5555",
        "127.0.0.1:5556-5563:5556-5563",
    }


@pytest.mark.parametrize("name", ["compose.yaml", "compose.dev.yaml"])
def test_every_published_port_is_loopback(name):
    ports = _published(_compose(name)["services"])
    assert ports and all(port.startswith("127.0.0.1:") for port in ports), ports


# ── models: the bundled embedder, nothing downloaded at runtime ─────────────


@pytest.mark.parametrize("service", ["umrs", "rag"])
def test_the_text_embedder_defaults_to_the_bundle_offline(service):
    env = _compose()["services"][service]["environment"]
    assert env["UMRS_KNOWLEDGE_EMBED_MODEL"] == "${UMRS_KNOWLEDGE_EMBED_MODEL:-}"
    assert (env["HF_HUB_OFFLINE"], env["TRANSFORMERS_OFFLINE"]) == ("1", "1")
    assert "UMRS_KNOWLEDGE_EMBED_MODEL" not in _env("base.env")


def test_the_image_bundles_the_embedder_by_default():
    dockerfile = (ROOT / "umrs" / "docker" / "Dockerfile.server").read_text()
    assert "ARG UMRS_BUNDLE_TEXT_EMBEDDER=1" in dockerfile


def test_the_user_config_points_at_the_embedder_the_image_bundles():
    """The stand-alone user config's model_name must be a directory the
    containers can load offline. The bundle is staged at BUILD time into
    /opt/umrs/models/e5-base-v2 (Dockerfile.server, umrs's
    BUNDLED_TEXT_EMBEDDER_DIR); /models/e5-base-v2 was M0's hand-staged
    volume path — nothing stages it, and the live 2026-10-15 run died on it
    (the rag service's config validation dropped the only experience:
    'db_model_store is empty')."""
    cfg = json.loads((STANDALONE / "user_files" / "scientist.json").read_text())
    modalities = cfg["experiences"][0]["modalities"]
    (text,) = (m for m in modalities if m["modality_name"] == "text")
    assert text["model"]["model_name"] == "/opt/umrs/models/e5-base-v2"
    dockerfile = (ROOT / "umrs" / "docker" / "Dockerfile.server").read_text()
    assert "/opt/umrs/models/e5-base-v2" in dockerfile
    code = (ROOT / "umrs" / "src" / "umrs" / "knowledge" / "text_embedder.py").read_text()
    assert 'BUNDLED_TEXT_EMBEDDER_DIR = "/opt/umrs/models/e5-base-v2"' in code


# ── secrets: everything compose demands, bootstrap generates ────────────────


def test_every_required_secret_is_generated_by_bootstrap():
    compose = _compose_text("dev")
    required = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", compose))
    example = (STANDALONE / "env" / "secrets.env.example").read_text()
    script = (STANDALONE / "standalone.sh").read_text()
    missing = sorted(
        key
        for key in required
        if not re.search(rf"^#?\s*{key}=", example, re.M) or key not in script
    )
    assert required and missing == []


# ── §3c.5's bring-up defects ────────────────────────────────────────────────


def test_nginx_litellm_upstream_is_an_always_on_service():
    """`upstream litellm` resolves at config-parse time: under `core` only,
    a profiled `models` host killed nginx (§3c.5 #1)."""
    env = _compose()["services"]["gateway-nginx"]["environment"]
    host = re.fullmatch(r"\$\{LITELLM_HOST:-([\w-]+)\}", env["LITELLM_HOST"]).group(1)
    assert host in _core_services()


def test_seaweedfs_loads_its_iam_config():
    """`-s3.config=` is not a flag; the pair is `-s3.iam -s3.iam.config=` (§3c.5 #2)."""
    command = _compose()["services"]["seaweedfs"]["command"]
    assert "-s3.iam " in command and "-s3.iam.config=" in command
    assert "-s3.config=" not in command


# ── the database survives a healthcheck timeout (live 2026-10-10) ───────────


def test_postgres_runs_under_an_init_so_orphans_never_reach_the_postmaster():
    """Under ingest load the healthcheck's ``sh`` timed out and was killed; the
    orphaned pg_isready was re-parented to PID 1, the postmaster, which reaps
    any child dying on a signal as a crashed backend and restarted every
    session into crash recovery, once a minute. An init as PID 1 reaps them."""
    postgres = _compose()["services"]["postgres"]
    assert postgres.get("init") is True
    timeout = int(str(postgres["healthcheck"]["timeout"]).rstrip("s"))
    assert timeout >= 10


def test_bulk_ingest_can_skip_llm_extraction_without_an_image_change():
    umrs = _compose()["services"]["umrs"]["environment"]
    assert umrs["UMRS_KNOWLEDGE_LLM_EXTRACT"] == "${UMRS_KNOWLEDGE_LLM_EXTRACT:-1}"


# ── ADR-093 retrieval: every knob mapped, the reranker off until staged ─────

# The knob -> default each service must receive. An env file only
# interpolates, so a knob missing here would never reach the container. The
# lists are checked against the code (a scan and a behaviour probe below), so
# a knob added to the code without a mapping fails here, not on the stack.
UMRS_RETRIEVAL_KNOBS = {
    "UMRS_RAG_FINAL_K_MAX": "4",
    "UMRS_RAG_POOL": "50",
    "UMRS_RAG_PER_ARTIFACT": "2",
    "UMRS_RAG_LEXICAL": "auto",
    "UMRS_RAG_MERGE": "off",
    "UMRS_RAG_TAIL_CHARS": "200",
    "UMRS_RAG_RERANK_DEFAULT": "off",
    "UMRS_RAG_RERANK_MODE": "replace",
    "UMRS_RAG_RERANKER_URL": "http://rag:8081",
    "UMRS_RAG_RERANKER_TIMEOUT": "10",
    "UMRS_RAG_NULL_P": "0.05",
    "UMRS_RAG_ANSWER_MAX_TOKENS": "400",
    "UMRS_RAG_DELTA": "0.03",
    "UMRS_RAG_GAMMA": "0.015",
    "UMRS_RAG_Z_SUFFICIENT": "3.0",
    "UMRS_RAG_Z_PARTIAL": "2.0",
    "UMRS_RAG_LEX_RATIO": "0.5",
    "UMRS_RAG_LEX_COVERAGE": "0.5",
    "UMRS_RAG_LEX_HIT_IDF": "3.0",
    "UMRS_RAG_LEX_MIN_TERMS": "2",
    "UMRS_RAG_RERANK_P_MIN": "0.10",
    "UMRS_RAG_RERANK_WINDOW": "4.0",
    "UMRS_RAG_RERANK_GAP": "2.0",
    "UMRS_RAG_RERANK_P_SUFFICIENT": "0.5",
    "UMRS_RAG_PACK_ORDER": "rank",
    "UMRS_RAG_PACK_INSTRUCTION": "soft",
    "UMRS_RAG_FUSION": "rrf",
    "UMRS_RAG_W_DENSE": "1.0",
    "UMRS_RAG_W_LEX": "1.0",
    "UMRS_RAG_W_IDENT": "0.5",
    "UMRS_RAG_PROFILE": "auto",
    "UMRS_RAG_GRAPH_LANE": "auto",
    # The graph lane's multi-hop gate: auto stays off until it is set to 1.
    "UMRS_RAG_GRAPH_LANE_MEASURED": "0",
    "UMRS_KNOWLEDGE_CHUNK_SCHEME": "fixed-v0",
}
RAG_KNOBS = {
    "UMRS_RAG_RERANKER_MODEL": "",
    "UMRS_RAG_RERANKER_THREADS": "2",
    # Eval-only: `b0` reproduces B0's `passage: query:` encoding (ADR-093).
    "UMRS_RAG_QUERY_PREFIX": "fixed",
}
ALL_KNOBS = [("umrs", k, v) for k, v in UMRS_RETRIEVAL_KNOBS.items()] + [
    ("rag", k, v) for k, v in RAG_KNOBS.items()
]
# Settings that name weights a process loads; each must be empty or a path.
WEIGHT_KNOBS = ("UMRS_RAG_RERANKER_MODEL", "UMRS_KNOWLEDGE_EMBED_MODEL", "UMRS_RAG_EMBEDDING_MODEL")

UMRS_SRC = ROOT / "umrs" / "src" / "umrs"
# Where the umrs container reads retrieval knobs: the core, the verb, compose
# on the pack, and the chunker. The rag container: the cross-encoder and the
# query encoder.
UMRS_KNOB_SOURCES = (
    *sorted((UMRS_SRC / "knowledge" / "retrieval").glob("*.py")),
    UMRS_SRC / "api" / "rag_retrieve.py",
    UMRS_SRC / "api" / "rag_spine.py",
    UMRS_SRC / "knowledge" / "chunking.py",
)
RAG_KNOB_SOURCES = (UMRS_SRC / "rag" / "search.py",)
# Names those modules read that are wiring, mapped as literals, not knobs.
WIRING = {"UMRS_RAG_DIRECT_URL", "UMRS_KNOWLEDGE_QUERY_ENCODER_URL"}


def _knobs_read(paths) -> set[str]:
    found = {
        name
        for path in paths
        for name in re.findall(r'"(UMRS_(?:RAG|KNOWLEDGE)_[A-Z0-9_]+)"', path.read_text())
    }
    return found - WIRING


def test_every_knob_the_retrieval_code_reads_is_pinned_for_its_service():
    """README step 4 says every ADR-093 knob is mapped: a knob the code reads
    but compose does not map is a silent no-op in env/local.env."""
    assert _knobs_read(UMRS_KNOB_SOURCES) == set(UMRS_RETRIEVAL_KNOBS)
    rag_service = (UMRS_SRC / "api" / "rag_service.py").read_text()
    assert '"UMRS_RAG_QUERY_PREFIX"' in rag_service
    assert _knobs_read(RAG_KNOB_SOURCES) | {"UMRS_RAG_QUERY_PREFIX"} == set(RAG_KNOBS)


@pytest.mark.parametrize(("service", "knob", "default"), ALL_KNOBS)
def test_every_retrieval_knob_reaches_its_service_with_its_default(service, knob, default):
    env = _compose()["services"][service]["environment"]
    assert env.get(knob) == f"${{{knob}:-{default}}}"


def _reranker_settings(rerank) -> tuple[str, float]:
    """(base url, timeout) the umrs rerank lane builds its client with."""
    built: list[tuple[str, float]] = []
    original = rerank.http_reranker
    rerank.http_reranker = lambda base, timeout: built.append((base, timeout))
    try:
        rerank.reranker_from_env()
    finally:
        rerank.http_reranker = original
    return built[0]


def _umrs_probes() -> dict:
    """knob -> a call that reads it as the umrs process does."""
    cutoff = pytest.importorskip("umrs.knowledge.retrieval.cutoff")
    fusion = pytest.importorskip("umrs.knowledge.retrieval.fusion")
    lexical = pytest.importorskip("umrs.knowledge.retrieval.lexical")
    expand = pytest.importorskip("umrs.knowledge.retrieval.expand")
    rerank = pytest.importorskip("umrs.knowledge.retrieval.rerank")
    verb = pytest.importorskip("umrs.api.rag_retrieve")
    spine = pytest.importorskip("umrs.api.rag_spine")
    chunking = pytest.importorskip("umrs.knowledge.chunking")
    profiles = pytest.importorskip("umrs.knowledge.retrieval.profiles")
    graph_lane = pytest.importorskip("umrs.knowledge.retrieval.graph_lane")
    probes = {name: (lambda n=name: cutoff.constant(n)) for name in cutoff._DEFAULTS}
    probes.update(dict.fromkeys(fusion.WEIGHT_ENV.values(), fusion.default_weights))
    probes.update(
        {
            "UMRS_RAG_FUSION": fusion.fusion_method,
            "UMRS_RAG_FINAL_K_MAX": verb.site_k_max,
            "UMRS_RAG_POOL": verb.site_pool,
            "UMRS_RAG_PER_ARTIFACT": verb.site_per_artifact,
            "UMRS_RAG_LEXICAL": lambda: lexical.lexical_enabled("auto"),
            "UMRS_RAG_MERGE": lambda: expand.merge_enabled("auto"),
            "UMRS_RAG_TAIL_CHARS": expand.tail_chars,
            "UMRS_RAG_RERANK_DEFAULT": lambda: rerank.rerank_enabled("auto"),
            "UMRS_RAG_RERANK_MODE": rerank.rerank_mode,
            "UMRS_RAG_RERANKER_URL": lambda: _reranker_settings(rerank),
            "UMRS_RAG_RERANKER_TIMEOUT": lambda: _reranker_settings(rerank),
            "UMRS_RAG_ANSWER_MAX_TOKENS": spine.answer_max_tokens,
            "UMRS_RAG_PACK_ORDER": spine.pack_order,
            "UMRS_RAG_PACK_INSTRUCTION": spine.pack_instruction,
            "UMRS_KNOWLEDGE_CHUNK_SCHEME": chunking.configured_scheme,
            "UMRS_RAG_PROFILE": profiles.site_profile,
            "UMRS_RAG_GRAPH_LANE": graph_lane.graph_lane_mode,
            "UMRS_RAG_GRAPH_LANE_MEASURED": graph_lane.lane_measured,
        }
    )
    return probes


def _rag_probes() -> dict:
    """knob -> a call that reads it as the rag process does."""
    search = pytest.importorskip("umrs.rag.search")
    service = pytest.importorskip("umrs.api.rag_service")
    return {
        "UMRS_RAG_RERANKER_MODEL": lambda: search.CrossEncoderReranker().model_name,
        "UMRS_RAG_RERANKER_THREADS": search.reranker_threads,
        "UMRS_RAG_QUERY_PREFIX": service.query_prefix_mode,
    }


@pytest.mark.parametrize(("service", "knob", "default"), ALL_KNOBS)
def test_every_mapped_default_behaves_as_the_codes_own_default(monkeypatch, service, knob, default):
    """Mapping the compose default must change nothing against the code's
    default: a pinned value that drifted from the code would silently retune
    the stack on `up`."""
    direct = _compose()["services"]["umrs"]["environment"]["UMRS_RAG_DIRECT_URL"]
    monkeypatch.setenv("UMRS_RAG_DIRECT_URL", direct)
    probe = (_umrs_probes() if service == "umrs" else _rag_probes())[knob]
    monkeypatch.delenv(knob, raising=False)
    unset = probe()
    monkeypatch.setenv(knob, default)
    assert probe() == unset


def test_the_mapped_defaults_read_the_standalone_model_small_and_rikyus_large(monkeypatch):
    """ADR-093 D-14: with the compose defaults the stack's llama3.2:3b reads
    with the small profile (the values above, unchanged) and no graph lane;
    rikyu's kimi-k3 and glm-5.3 read with the large one. The graph lane stays
    off there too until the maintainer records its multi-hop probe
    (UMRS_RAG_GRAPH_LANE_MEASURED=1; review profiles-graph-5, 2026-10-10)."""
    profiles = pytest.importorskip("umrs.knowledge.retrieval.profiles")
    graph_lane = pytest.importorskip("umrs.knowledge.retrieval.graph_lane")
    env = _compose()["services"]["umrs"]["environment"]
    for knob in ("UMRS_RAG_PROFILE", "UMRS_RAG_GRAPH_LANE", "UMRS_RAG_GRAPH_LANE_MEASURED"):
        monkeypatch.setenv(knob, _effective(env[knob]))
    assert profiles.resolve_profile("llama3.2:3b")[0] == "small"
    assert graph_lane.resolve_gate(lambda: "llama3.2:3b")[0] == "off"
    for rikyu in ("kimi-k3", "glm-5.3"):
        assert profiles.resolve_profile(rikyu)[0] == "large"
        assert graph_lane.resolve_gate(lambda m=rikyu: m)[0] == "off"


@pytest.mark.parametrize("rikyu", ["kimi-k3", "glm-5.3"])
def test_a_measured_graph_lane_turns_on_for_rikyus_large_extraction_model(monkeypatch, rikyu):
    """Once the probe passes, the maintainer sets one knob and auto turns the
    lane on for a large extraction model, never for the stack's 3B."""
    graph_lane = pytest.importorskip("umrs.knowledge.retrieval.graph_lane")
    env = _compose()["services"]["umrs"]["environment"]
    monkeypatch.setenv("UMRS_RAG_GRAPH_LANE", _effective(env["UMRS_RAG_GRAPH_LANE"]))
    monkeypatch.setenv("UMRS_RAG_GRAPH_LANE_MEASURED", "1")
    assert graph_lane.resolve_gate(lambda: rikyu)[0] == "on"
    assert graph_lane.resolve_gate(lambda: "llama3.2:3b")[0] == "off"


def test_the_reranker_is_off_by_default_and_never_downloads():
    rag = _compose()["services"]["rag"]
    assert rag["environment"]["UMRS_RAG_RERANKER_MODEL"] == "${UMRS_RAG_RERANKER_MODEL:-}"
    assert rag["environment"]["HF_HUB_OFFLINE"] == "1"
    assert "umrs_models:/models:ro" in rag["volumes"]


def test_the_rerank_lane_defaults_to_the_rag_service():
    umrs = _compose()["services"]["umrs"]["environment"]
    url = re.fullmatch(r"\$\{UMRS_RAG_RERANKER_URL:-(.+)\}", umrs["UMRS_RAG_RERANKER_URL"])
    assert url and url.group(1) == umrs["UMRS_RAG_DIRECT_URL"]


def _weight_settings(profile: str) -> dict[str, str]:
    """Every value a profile gives a weight knob: compose literals and
    defaults, then the env files."""
    found: dict[str, str] = {}
    for name in ["compose.yaml"] + (["compose.dev.yaml"] if profile == "dev" else []):
        for service in _compose(name)["services"].values():
            env = service.get("environment") or {}
            found.update({k: str(env[k]) for k in WEIGHT_KNOBS if k in env})
    settings = {**_env("base.env"), **_env(f"profile.{profile}.env")}
    found.update({k: settings[k] for k in WEIGHT_KNOBS if k in settings})
    return found


def _effective(value: str) -> str:
    default = re.fullmatch(r"\$\{\w+:-(.*)\}", value)
    return (default.group(1) if default else value).strip()


@pytest.mark.parametrize("profile", PROFILES)
def test_no_profile_names_a_model_by_hub_id(profile):
    """A hub id never loads under HF_HUB_OFFLINE=1 and is refused by name; a
    profile that set one would ship a reranker or embedder that cannot work."""
    values = {k: _effective(v) for k, v in _weight_settings(profile).items()}
    hub_ids = {k: v for k, v in values.items() if v and not v.startswith("/")}
    assert "UMRS_RAG_RERANKER_MODEL" in values
    assert hub_ids == {}


def _function(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.M | re.S)
    assert match, f"standalone.sh has no {name}()"
    return re.sub(r"\s*\\\n\s*", " ", match.group(1))  # join continued lines


def test_reranker_pull_reuses_the_embedder_staging_code():
    script = (STANDALONE / "standalone.sh").read_text()
    assert 'RERANKER_REPO="cross-encoder/ms-marco-MiniLM-L6-v2"' in script
    assert 'RERANKER_DIR="ms-marco-MiniLM-L6-v2"' in script
    assert "_stage_hf_model" in _function(script, "cmd_reranker")
    assert "_stage_hf_model" in _function(script, "cmd_embedder")
    assert "-m umrs.knowledge.text_embedder stage" in _function(script, "_stage_hf_model")
    staged = _function(script, "model_dir_is_staged")
    assert "config.json" in staged and "model.safetensors" in staged
    assert re.search(r"^\s+reranker\)\s+shift; cmd_reranker", script, re.M)


def test_migrate_runs_the_bootstrap_command_against_the_running_postgres():
    """One command for both (run_migrations). Its URL is built by the
    container's sh from its own DB_* environment (2026-10-15): bootstrap no
    longer exports the secrets into the script's shell, and the password
    never reaches the host's process list."""
    script = (STANDALONE / "standalone.sh").read_text()
    body = _function(script, "cmd_migrate")
    run = _function(script, "run_migrations")
    assert "run_migrations" in _function(script, "cmd_bootstrap")
    assert "run_migrations" in body
    assert "python -m umrs.migrations --db-url" in run
    assert "${DB_PASSWORD}" in run and "POSTGRES_PASSWORD" not in run
    assert "--status) args+=(--status)" in body
    assert "pg_isready" in body
    assert re.search(r"^\s+migrate\)\s+shift; cmd_migrate", script, re.M)


def test_doctor_reports_the_reranker():
    script = (STANDALONE / "standalone.sh").read_text()
    assert "doctor_reranker" in _function(script, "cmd_doctor")
    probe = _function(script, "doctor_reranker")
    assert "printenv UMRS_RAG_RERANKER_MODEL" in probe  # a forgotten `up` shows
    assert "model.safetensors" in probe


def test_blas_threads_match_each_model_containers_cpu_quota():
    """torch sizes its pool from the host's cores, not the cgroup quota; in the
    2-CPU rag container 6 threads made a short query take up to 31 s and the
    encoder timeout refuse 125 of 188 eval questions (live 2026-10-10)."""
    services = _compose()["services"]
    for name in ("umrs", "rag"):
        service = services[name]
        quota = str(int(float(service["cpus"])))
        env = service["environment"]
        assert env.get("OMP_NUM_THREADS") == quota, name
        assert env.get("MKL_NUM_THREADS") == quota, name


# ── ADR-093 MD-17 / MD-11 (decided 2026-10-11) ──────────────────────────────


def test_orphan_repair_is_a_dry_run_by_default_and_dispatched():
    script = (STANDALONE / "standalone.sh").read_text()
    body = _function(script, "cmd_orphan_repair")
    assert "python -m umrs.knowledge.orphan_repair --project" in body
    assert "--apply) apply=(--apply)" in body  # only an explicit flag writes
    assert "compose exec -T umrs" in body  # the running server, its own DB env
    assert "--db-url" not in body and "PASSWORD" not in body
    assert re.search(r"^\s+orphan-repair\)\s+shift; cmd_orphan_repair", script, re.M)
    assert "orphan-repair [--apply] [--project P]" in script.split("set -euo pipefail")[0]


def test_doctor_hints_ollama_parallel_slots_without_failing():
    script = (STANDALONE / "standalone.sh").read_text()
    doctor = _function(script, "cmd_doctor")
    assert re.search(r"^\s*doctor_ollama_parallel\s*$", doctor, re.M)
    assert "doctor_ollama_parallel ||" not in doctor  # a hint, never a failure
    hint = _function(script, "doctor_ollama_parallel")
    assert "OLLAMA_NUM_PARALLEL" in hint and "MD-11" in hint
    assert "printenv OLLAMA_NUM_PARALLEL" in hint  # the container's real value


def test_the_parallel_default_stays_one_and_the_recommendation_is_documented():
    """MD-11 is a recommendation for capable hosts, not a new default: two
    slots at 32k need ~9.6 GiB, over the default 8g chat-memory limit."""
    assert _env("base.env")["OLLAMA_NUM_PARALLEL"] == "1"
    base = (STANDALONE / "env" / "base.env").read_text()
    readme = (STANDALONE / "README.md").read_text()
    for text in (base, readme):
        assert "OLLAMA_NUM_PARALLEL=2" in text and "MD-11" in text


_STUB_ENGINE = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  *" exec -T umrs true") exit "${STUB_UP_RC:-0}" ;;
  *"orphan_repair --project bad"*) echo "boom" >&2; exit 3 ;;
  *orphan_repair*) echo '{"applied": false, "found": {"rowless_chunks": 0}}' ;;
esac
exit 0
"""


def _run_orphan_repair(tmp_path, *args, up_rc=0):
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(_STUB_ENGINE)
    stub.chmod(0o755)
    log = tmp_path / "engine.log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STANDALONE_ENGINE": "docker",
        "STANDALONE_MODEL_HOST": "container",
        # This test's own state and per-host env, never the checkout's.
        "STANDALONE_STATE_FILE": str(tmp_path / "state"),
        "STANDALONE_LOCAL_ENV": str(tmp_path / "local.env"),
        "STANDALONE_GPU": "off",
        "STUB_LOG": str(log),
        "STUB_UP_RC": str(up_rc),
    }
    env.pop("STANDALONE_PROFILE", None)
    proc = subprocess.run(
        ["bash", str(STANDALONE / "standalone.sh"), "orphan-repair", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = [line for line in log.read_text().splitlines() if "orphan_repair" in line]
    return proc, calls


def test_orphan_repair_dry_runs_the_default_project(tmp_path):
    proc, calls = _run_orphan_repair(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 1
    assert calls[0].endswith(
        "exec -T umrs python -m umrs.knowledge.orphan_repair --project default"
    )
    assert '"applied": false' in proc.stdout  # the JSON report reaches the operator


def test_orphan_repair_applies_each_named_project(tmp_path):
    proc, calls = _run_orphan_repair(tmp_path, "--apply", "--project", "p1", "--project=p2")
    assert proc.returncode == 0, proc.stderr
    assert [c.split("orphan_repair ", 1)[1] for c in calls] == [
        "--project p1 --apply",
        "--project p2 --apply",
    ]


@pytest.mark.parametrize(
    "args", [("--yes",), ("--project",), ("--project", "--apply"), ("--project=",)]
)
def test_orphan_repair_refuses_bad_arguments_before_touching_the_stack(tmp_path, args):
    proc, calls = _run_orphan_repair(tmp_path, *args)
    assert proc.returncode != 0
    assert "usage: standalone.sh orphan-repair" in proc.stderr
    assert calls == []
    assert (tmp_path / "engine.log").read_text() == ""


def test_orphan_repair_fails_on_a_module_error_and_stops(tmp_path):
    proc, calls = _run_orphan_repair(tmp_path, "--project", "bad", "--project", "p2")
    assert proc.returncode != 0
    assert "orphan repair failed for project 'bad'" in proc.stderr
    assert len(calls) == 1  # p2 never ran


def test_orphan_repair_refuses_when_umrs_is_down(tmp_path):
    proc, calls = _run_orphan_repair(tmp_path, up_rc=1)
    assert proc.returncode != 0
    assert "umrs is not running" in proc.stderr
    assert calls == []


# ── the one-command local path (onboarding plan §2 Phase 1, 2026-10-15) ─────
# `./r4-local up` = `standalone.sh quickstart`. standalone.sh is sourced for
# its functions (it dispatches only when executed), each test with its own
# env/.state and env/local.env, stub engines on PATH, and no network.

SCRIPT = STANDALONE / "standalone.sh"
LIB = STANDALONE / "lib"
R4_LOCAL = ROOT / "r4-local"
# Settings of the developer's own shell that would change what a test sees,
# or send a write into their real config: every STANDALONE_*, R4_*, UMRS_*
# (UMRS_CONFIG wins over XDG_CONFIG_HOME in r4) and OLLAMA_* variable.
_HOST_PREFIXES = ("STANDALONE_", "R4_", "UMRS_", "OLLAMA_")
_HOST_SETTINGS = ("ASSUME_YES",)
TOKEN = "eyJTEST.not-a-real-token.SECRET-7d1c"
# A container engine a test did not stub must never be the host's own.
_NO_ENGINE = 'echo "engine stub: $(basename "$0") $*" >&2; exit 97\n'


def _isolated_env(tmp_path: Path, extra: dict | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _HOST_SETTINGS and not k.startswith(_HOST_PREFIXES)
    }
    env.update(
        STANDALONE_STATE_FILE=str(tmp_path / "state"),
        STANDALONE_LOCAL_ENV=str(tmp_path / "local.env"),
        STANDALONE_SECRETS_FILE=str(tmp_path / "secrets"),
        STANDALONE_GPU="off",
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
        PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
    )
    env.update(extra or {})
    (tmp_path / "bin").mkdir(exist_ok=True)
    for engine in ("docker", "podman"):
        if not (tmp_path / "bin" / engine).exists():
            _stub(tmp_path, engine, _NO_ENGINE)
    return env


def _bash(tmp_path: Path, code: str, **extra: str) -> subprocess.CompletedProcess:
    """CODE after sourcing standalone.sh: its functions, no dispatch."""
    return subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"\n{code}'],
        env=_isolated_env(tmp_path, extra),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _out(tmp_path: Path, code: str, **extra: str) -> str:
    proc = _bash(tmp_path, code, **extra)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _defined(names: str) -> str:
    """Bash that stops (exit 99) unless each function exists, so a refusal
    can never pass because the function is missing."""
    return f'for f in {names}; do [ "$(type -t "$f")" = function ] || exit 99; done; '


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _kv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _code(path: Path) -> str:
    """A script without its comment lines (they may name what the code avoids)."""
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _lib_function(lib: str, name: str) -> str:
    text = (LIB / f"{lib}.sh").read_text()
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", text, re.M | re.S) or re.search(
        rf"^{name}\(\) \{{ (.*) \}}$", text, re.M
    )
    assert match, f"lib/{lib}.sh has no {name}()"
    return match.group(1)


# ── the chain ───────────────────────────────────────────────────────────────

QUICKSTART_CHAIN = [
    "qs_args",
    "qs_profile",
    "qs_model_host_intent",
    "qs_preflight",
    "qs_choose_model",
    "qs_ollama",
    "qs_bootstrap",
    "qs_up",
    "qs_client",
    "qs_register",
    "qs_smoke",
]


def test_quickstart_runs_its_chain_in_order(tmp_path):
    """preflight -> model -> ollama -> bootstrap -> up -> client -> register -> smoke."""
    stubs = "\n".join(f'{step}() {{ echo {step} >> "$LOG"; }}' for step in QUICKSTART_CHAIN)
    _out(tmp_path, f"{stubs}\ncmd_quickstart --yes", LOG=str(tmp_path / "log"))
    assert (tmp_path / "log").read_text().split() == QUICKSTART_CHAIN


def test_the_steps_call_the_verbs_they_name():
    assert "cmd_preflight" in _lib_function("quickstart", "qs_preflight")
    assert "cmd_bootstrap" in _lib_function("quickstart", "qs_bootstrap")
    assert "cmd_up" in _lib_function("quickstart", "qs_up")
    assert "install_client" in _lib_function("quickstart", "qs_client")
    assert "cmd_register" in _lib_function("quickstart", "qs_register")
    assert "smoke_test" in _lib_function("quickstart", "qs_smoke")


def test_quickstart_never_destroys_data():
    for lib in ("quickstart", "client", "preflight"):
        code = _code(LIB / f"{lib}.sh")
        assert "cmd_reset" not in code and "down -v" not in code, lib


@pytest.mark.parametrize(
    "args, expect",
    [
        ("--model qwen2.5:7b", "model=qwen2.5:7b yes=0"),
        ("--model=llama3.2:3b --yes", "model=llama3.2:3b yes=1"),
        ("-y", "model= yes=1"),
    ],
)
def test_quickstart_reads_its_flags(tmp_path, args, expect):
    out = _out(tmp_path, f'qs_args {args}; echo "model=$QS_MODEL yes=${{ASSUME_YES:-0}}"')
    assert out.strip() == expect


@pytest.mark.parametrize("args", ["--model", "--model --yes", "--model=", "--reset"])
def test_quickstart_refuses_bad_flags(tmp_path, args):
    proc = _bash(tmp_path, f"qs_args {args}")
    assert proc.returncode != 0
    assert "usage: standalone.sh quickstart" in proc.stderr


def test_quickstart_uses_dev_and_says_so_in_one_line(tmp_path):
    proc = _bash(tmp_path, 'qs_profile; echo "profile=$STANDALONE_PROFILE"')
    assert proc.returncode == 0, proc.stderr
    assert "profile=dev" in proc.stdout
    assert [line for line in proc.stdout.splitlines() if "profile: dev" in line][0]
    assert "D-6" in proc.stdout


def test_an_exported_profile_wins_over_the_quickstart_default(tmp_path):
    out = _out(
        tmp_path, 'qs_profile; echo "profile=$STANDALONE_PROFILE"', STANDALONE_PROFILE="hardened"
    )
    assert "profile=hardened" in out


def test_an_existing_stack_without_a_record_stays_hardened(tmp_path):
    """Installs from before env/.state existed have secrets and no record:
    the quickstart must not switch them to dev (C1-C-R1)."""
    (tmp_path / "secrets").write_text("POSTGRES_PASSWORD=dummy\n")
    out = _out(tmp_path, 'qs_profile; echo "profile=$STANDALONE_PROFILE"')
    assert "profile=hardened" in out
    assert "an existing stack with no recorded profile" in out


def test_an_existing_stacks_volume_keeps_it_hardened_too(tmp_path):
    _stub(
        tmp_path,
        "docker",
        'case "$*" in "volume inspect ai4s-standalone_pg_data") exit 0 ;; esac\nexit 1\n',
    )
    out = _out(tmp_path, 'qs_profile; echo "profile=$STANDALONE_PROFILE"')
    assert "profile=hardened" in out


def test_a_new_stack_records_its_profile_before_its_first_secret(tmp_path):
    """A rerun after a failed first bring-up (secrets written, nothing
    recorded by up) keeps the quickstart's dev."""
    code = "qs_profile; stack_healthy() { return 1; }; cmd_bootstrap() { echo BOOTSTRAP; }; qs_bootstrap"
    assert "BOOTSTRAP" in _out(tmp_path, code)
    assert _kv(tmp_path / "state") == {"profile": "dev"}
    (tmp_path / "secrets").write_text("POSTGRES_PASSWORD=dummy\n")
    assert "profile=dev" in _out(tmp_path, 'qs_profile; echo "profile=$STANDALONE_PROFILE"')


_GATEWAY_STATIC_HEALTH = (
    'curl() { echo \'{"status": "healthy", "service": "gateway"}\'; }; '
    "load_env() { :; }; profile_args() { :; }; "
    "cmd_bootstrap() { echo BOOTSTRAP; }; cmd_migrate() { echo MIGRATE; }; STANDALONE_PROFILE=dev; "
)


def test_a_static_gateway_health_is_not_a_healthy_stack(tmp_path):
    """nginx answers /health itself: umrs must answer too (C1-C-R11)."""
    (tmp_path / "secrets").write_text("x=1\n")
    code = f'SECRETS="{tmp_path / "secrets"}"; ' + _GATEWAY_STATIC_HEALTH
    out = _out(tmp_path, code + "umrs_answers() { return 1; }; qs_bootstrap")
    assert "BOOTSTRAP" in out and "skipped" not in out


def test_a_healthy_rerun_applies_pending_migrations(tmp_path):
    (tmp_path / "secrets").write_text("x=1\n")
    code = f'SECRETS="{tmp_path / "secrets"}"; ' + _GATEWAY_STATIC_HEALTH
    out = _out(tmp_path, code + "umrs_answers() { return 0; }; qs_bootstrap")
    assert "skipped" in out and "MIGRATE" in out and "BOOTSTRAP" not in out


def test_bootstrap_never_exports_the_secrets(tmp_path):
    """JWT_SECRET mints admin tokens; uv, make and r4 run after bootstrap in
    the quickstart's shell (C1-C-R13)."""
    secrets = tmp_path / "secrets"
    secrets.write_text("JWT_SECRET=never-in-env\nPOSTGRES_USER=umrs\nUMRS_SECRET_KEY=x\n")
    _stub(tmp_path, "docker", "exit 0\n")
    code = (
        f'SECRETS="{secrets}"; render_secret_files() {{ set -a; . "$SECRETS"; set +a; }}; '
        "cmd_preflight() { :; }; compose() { :; }; cmd_bootstrap >/dev/null; "
        'echo "shell=${JWT_SECRET:-}"; echo "child=$(env | grep -c ^JWT_SECRET= || true)"'
    )
    out = _out(tmp_path, code, STANDALONE_ENGINE="docker")
    assert "shell=\n" in out and "child=0" in out
    script = SCRIPT.read_text()
    assert "( render_secret_files )" in _function(script, "cmd_bootstrap")
    assert script.count('. "$SECRETS"') == 1


# ── the model: the D-7 RAM table, written to env/local.env only ─────────────

GIB = 1024


@pytest.mark.parametrize(
    "mem_mib, vram_mib, unified, model",
    [
        (7_800, 0, 0, "llama3.2:3b"),  # an 8 GB laptop
        (11_000, 0, 0, "llama3.2:3b"),
        (15_620, 0, 0, "qwen2.5:7b"),  # a "16 GB" Linux host reads ~15.3 GiB
        (24 * GIB, 0, 0, "qwen2.5:7b"),  # CPU-only: the memory, not the speed
        (24 * GIB, 0, 1, "qwen2.5:14b"),  # Apple silicon, unified memory
        (16 * GIB, 12 * GIB, 0, "qwen2.5:14b"),  # >= 12 GB VRAM
        (16 * GIB, 12_282, 0, "qwen2.5:14b"),  # a 12 GB card that reports 12282 MiB
        (16 * GIB, 8 * GIB, 0, "qwen2.5:7b"),
        (15_667, 11_264, 0, "qwen2.5:7b"),  # an 11 GB card is not a 12 GB one
        (15_667, 11_178, 0, "qwen2.5:7b"),  # a GTX 1080 Ti as nvidia-smi reports it
        (47_000, 0, 0, "qwen3:30b"),  # a "48 GB" Linux host reads ~47 GiB
        (45_056, 0, 0, "qwen2.5:7b"),  # a 44 GiB VM is not a 48 GB host
        (63_488, 0, 0, "qwen3:30b"),  # rccs-wk, 62 GB, broken NVML
    ],
)
def test_the_ram_table_picks_the_model(tmp_path, mem_mib, vram_mib, unified, model):
    assert _out(tmp_path, f"pick_model {mem_mib} {vram_mib} {unified}") == model


@pytest.mark.parametrize(
    "mem_mib, vram_mib, model, slots, mem",
    [
        (63_488, 0, "qwen3:30b", "2", "28g"),
        (15_620, 0, "llama3.2:3b", "1", "8g"),  # two slots would starve the platform
        (32 * GIB, 0, "qwen2.5:14b", "1", "18g"),
        (24 * GIB, 0, "qwen2.5:7b", "2", "12g"),
    ],
)
def test_the_container_ollama_is_sized_with_the_model(
    tmp_path, mem_mib, vram_mib, model, slots, mem
):
    out = _out(
        tmp_path,
        f's=$(pick_slots {mem_mib} {vram_mib} {model}); echo "$s $(model_mem {model} "$s")"',
    )
    assert out.split() == [slots, mem]


def _choose_model(tmp_path: Path, mem_mib: int, extra_code: str = "", **env: str) -> str:
    code = f"""
host_mem_mib() {{ echo {mem_mib}; }}
vram_mib() {{ echo 0; }}
gpu_problem() {{ echo 'nvidia-smi failed: Failed to initialize NVML: Driver/library version mismatch'; }}
gpu_choice() {{ echo none; }}
{extra_code}
qs_choose_model
"""
    return _out(tmp_path, code, **env)


def test_the_model_choice_goes_to_local_env_never_base_env(tmp_path):
    base = (STANDALONE / "env" / "base.env").read_bytes()
    out = _choose_model(tmp_path, 63_488)
    assert "qwen3:30b (RAM table D-7)" in out
    assert _kv(tmp_path / "local.env") == {
        "STANDALONE_CHAT_MODEL": "qwen3:30b",
        "STANDALONE_CHAT_MEM": "28g",
        "OLLAMA_NUM_PARALLEL": "2",
    }
    assert _kv(tmp_path / "state")["auto_STANDALONE_CHAT_MODEL"] == "qwen3:30b"
    assert (STANDALONE / "env" / "base.env").read_bytes() == base


def test_a_value_you_set_always_wins(tmp_path):
    (tmp_path / "local.env").write_text(
        "STANDALONE_CHAT_MODEL=llama3.2:3b\nSTANDALONE_CHAT_MEM=10g\n"
    )
    out = _choose_model(tmp_path, 63_488)
    assert "llama3.2:3b (set by you; kept)" in out
    assert _kv(tmp_path / "local.env")["STANDALONE_CHAT_MODEL"] == "llama3.2:3b"
    assert _kv(tmp_path / "local.env")["STANDALONE_CHAT_MEM"] == "10g"


def test_an_exported_model_wins_and_is_not_written(tmp_path):
    out = _choose_model(tmp_path, 63_488, STANDALONE_CHAT_MODEL="qwen2.5:7b")
    assert "qwen2.5:7b (set by you; kept)" in out
    assert "STANDALONE_CHAT_MODEL" not in _kv(tmp_path / "local.env")


def test_model_flag_overrides_and_stays_yours(tmp_path):
    _choose_model(tmp_path, 15_620, 'QS_MODEL="gemma3:4b"')
    assert _kv(tmp_path / "local.env")["STANDALONE_CHAT_MODEL"] == "gemma3:4b"
    out = _choose_model(tmp_path, 63_488)  # a later run without --model
    assert "gemma3:4b (set by you; kept)" in out


def test_a_choice_quickstart_made_is_remade_after_a_ram_upgrade(tmp_path):
    _choose_model(tmp_path, 15_620)
    assert _kv(tmp_path / "local.env")["STANDALONE_CHAT_MODEL"] == "qwen2.5:7b"
    _choose_model(tmp_path, 63_488)
    assert _kv(tmp_path / "local.env")["STANDALONE_CHAT_MODEL"] == "qwen3:30b"


_DESKTOP_DOCKER = """case "$*" in
  *MemTotal*) echo @BYTES@ ;;
  *OperatingSystem*) echo "Docker Desktop" ;;
esac
exit 0
"""


def test_docker_desktop_sizes_the_model_from_its_vm(tmp_path):
    """A 64 GB Mac with a 16 GB Docker Desktop VM gets the VM's model, not
    qwen3:30b with a 28g limit (C0-C-PORT-02, C1-C-R8, C2-C-R2-02)."""
    _stub(tmp_path, "docker", _DESKTOP_DOCKER.replace("@BYTES@", str(16 * 1024**3)))
    code = (
        "uname() { echo Darwin; }; sysctl() { echo 68719476736; }; "
        "vram_mib() { echo 0; }; gpu_problem() { :; }; GPU_CHOICE=none; ENGINE=docker; "
        "qs_choose_model"
    )
    out = _out(tmp_path, code, STANDALONE_MODEL_HOST="container")
    assert "memory 16.0 GiB (Docker Desktop's VM)" in out
    assert _kv(tmp_path / "local.env") == {
        "STANDALONE_CHAT_MODEL": "qwen2.5:7b",
        "STANDALONE_CHAT_MEM": "8g",
        "OLLAMA_NUM_PARALLEL": "1",
    }


def test_docker_desktop_on_linux_checks_its_vm_against_the_floor(tmp_path):
    _stub(tmp_path, "docker", _DESKTOP_DOCKER.replace("@BYTES@", str(8 * 1024**3)))
    proc = _bash(tmp_path, "is_wsl() { return 1; }; ENGINE=docker; preflight_linux")
    assert proc.returncode != 0
    assert "Docker Desktop VM 8.0 GiB < required 16 GB" in proc.stderr


def test_apple_silicon_is_read_from_the_hardware_not_the_shell(tmp_path):
    """Under Rosetta `uname -m` says x86_64 on an M-series Mac (C0-C-PORT-08)."""
    code = (
        'uname() { case "$1" in -m) echo x86_64 ;; *) echo Darwin ;; esac; }; '
        'sysctl() { case "$*" in *hw.optional.arm64*) echo 1 ;; *memsize*) echo 34359738368 ;; esac; }; '
        "vram_mib() { echo 0; }; gpu_problem() { :; }; GPU_CHOICE=none; qs_choose_model"
    )
    out = _out(tmp_path, code, STANDALONE_MODEL_HOST="native")
    assert "qwen2.5:14b (RAM table D-7)" in out


def test_a_model_set_in_base_env_is_kept(tmp_path):
    """The README before 2026-10-15 said to set the model in env/base.env;
    such a value is the user's and wins (C1-C-R7)."""
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    base = (STANDALONE / "env" / "base.env").read_text()
    edited = base.replace("STANDALONE_CHAT_MODEL=llama3.2:3b", "STANDALONE_CHAT_MODEL=qwen2.5:14b")
    edited = edited.replace("STANDALONE_CHAT_MEM=8g", "STANDALONE_CHAT_MEM=14g")
    assert "STANDALONE_CHAT_MODEL=qwen2.5:14b" in edited and "STANDALONE_CHAT_MEM=14g" in edited
    (env_dir / "base.env").write_text(edited)
    out = _choose_model(tmp_path, 15_700, f'ENV_DIR="{env_dir}"')
    assert "qwen2.5:14b (set by you; kept)" in out
    local = _kv(tmp_path / "local.env")
    assert "STANDALONE_CHAT_MODEL" not in local and "STANDALONE_CHAT_MEM" not in local


def test_the_shipped_base_defaults_are_base_envs_own(tmp_path):
    shipped = _out(tmp_path, 'printf "%s" "$SHIPPED_BASE_DEFAULTS"').split()
    base = _env("base.env")
    assert len(shipped) == 3
    for pair in shipped:
        key, value = pair.split("=", 1)
        assert base[key] == value, key


# ── memory, GPU and host facts ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "mem_kb, need_gb, ok_",
    [
        (15_994_000, 16, True),  # a 16 GB host: 15.25 GiB, refused before 2026-10-15
        (15_100_000, 16, True),  # within the 10 % tolerance
        (12_000_000, 16, False),
        (7_900_000, 8, True),
    ],
)
def test_the_ram_floor_is_compared_in_mib_with_a_tolerance(tmp_path, mem_kb, need_gb, ok_):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {mem_kb} kB\nMemFree:  1 kB\n")
    code = f'if mem_at_least "$(meminfo_mib {meminfo})" {need_gb}; then echo pass; else echo refuse; fi'
    assert _out(tmp_path, _defined("mem_at_least meminfo_mib") + code).strip() == (
        "pass" if ok_ else "refuse"
    )


def test_a_wsl2_vm_below_the_floor_gets_the_wslconfig_fix(tmp_path):
    proc = _bash(
        tmp_path, "is_wsl() { return 0; }; wslconfig_path() { :; }; preflight_ram 12000 RAM 16"
    )
    assert proc.returncode != 0
    assert "the WSL2 VM has 11.7 GiB" in proc.stderr
    assert "[wsl2]" in proc.stderr and "memory=16GB" in proc.stderr
    assert "wsl --shutdown" in proc.stderr


@pytest.mark.parametrize(
    "systemd, desktop, verdict",
    [("1", "0", "ok"), ("0", "1", "warn"), ("0", "0", "die"), ("1", "1", "ok")],
)
def test_wsl2_systemd_is_only_a_warning_under_docker_desktop(tmp_path, systemd, desktop, verdict):
    assert _out(tmp_path, f"wsl_systemd_verdict {systemd} {desktop}") == verdict


def test_wsl2_without_systemd_under_docker_desktop_goes_on(tmp_path):
    code = "wsl_has_systemd() { return 1; }; docker_is_desktop() { return 0; }; ENGINE=docker; preflight_wsl"
    proc = _bash(tmp_path, code)
    assert proc.returncode == 0, proc.stderr
    assert "WSL2 without systemd: fine for Docker Desktop" in proc.stderr


def test_wsl2_without_systemd_for_podman_stops_with_the_fix(tmp_path):
    code = "wsl_has_systemd() { return 1; }; ENGINE=podman; preflight_wsl"
    proc = _bash(tmp_path, code)
    assert proc.returncode != 0
    assert "systemd=true" in proc.stderr and "wsl --shutdown" in proc.stderr


_BROKEN_NVML = "echo 'Failed to initialize NVML: Driver/library version mismatch'; exit 18\n"


def test_a_broken_nvml_is_a_cpu_host(tmp_path):
    _stub(tmp_path, "nvidia-smi", _BROKEN_NVML)
    out = _out(tmp_path, 'echo "$(vram_mib)|$(gpu_problem)"; nvsmi_works || echo no-gpu')
    assert out.splitlines() == [
        "0|nvidia-smi failed: Failed to initialize NVML: Driver/library version mismatch",
        "no-gpu",
    ]


def test_vram_is_the_largest_gpu(tmp_path):
    _stub(tmp_path, "nvidia-smi", 'case "$*" in *query-gpu*) printf "12288\\n24576\\n" ;; esac\n')
    assert _out(tmp_path, "vram_mib") == "24576"


@pytest.mark.parametrize(
    "facts, choice",
    [
        # engine nvsmi nvidia-driver cdi-spec engine-cdi override
        ("docker 0 1 1 1 auto", "none"),  # broken NVML: skipped cleanly
        ("docker 1 1 0 0 auto", "nvidia"),
        ("docker 1 0 1 1 auto", "cdi"),  # Docker with CDI enabled in the daemon
        ("docker 1 0 1 0 auto", "none"),  # Docker < 28.2, CDI off: the overlay fails up
        ("podman 1 1 1 0 auto", "cdi"),  # podman resolves CDI itself
        ("podman 1 0 0 0 auto", "none"),
        ("docker 1 1 1 1 off", "none"),
        ("podman 0 0 0 0 nvidia", "nvidia"),
    ],
)
def test_the_gpu_overlay_choice(tmp_path, facts, choice):
    assert _out(tmp_path, f"gpu_overlay_choice {facts}") == choice


def test_the_gpu_overlays_hand_the_gpu_to_the_model_container_only():
    nvidia = _compose("compose.gpu-nvidia.yaml")["services"]
    cdi = _compose("compose.gpu-cdi.yaml")["services"]
    assert set(nvidia) == set(cdi) == {"models"}
    devices = nvidia["models"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]
    assert cdi["models"]["devices"] == ["nvidia.com/gpu=all"]


_NVSMI_OK = 'echo "$*" >> "$NVSMI_LOG"; case "$*" in *query-gpu*) echo 24576 ;; esac\n'


def _probe(tmp_path: Path, docker: str, *extra_stubs: str) -> str:
    """probe_gpu_choice on Linux, Docker, the model in its container."""
    _stub(tmp_path, "docker", docker)
    _stub(tmp_path, "nvidia-smi", _NVSMI_OK)
    for name in extra_stubs:
        _stub(tmp_path, name, "exit 0\n")
    code = (
        "uname() { echo Linux; }; cdi_spec_present() { return 0; }; ENGINE=docker; probe_gpu_choice"
    )
    return _out(
        tmp_path,
        code,
        STANDALONE_GPU="auto",
        STANDALONE_MODEL_HOST="container",
        NVSMI_LOG=str(tmp_path / "nvsmi.log"),
        PATH=_minimal_path(tmp_path),  # this host has the toolkit's hook on PATH
    )


def test_docker_without_cdi_enabled_never_gets_the_cdi_overlay(tmp_path):
    """Docker < 28.2 has CDI off: CDISpecDirs is [] and a CDI device fails
    `compose up` (C0-C-PORT-04)."""
    docker = 'case "$*" in *CDISpecDirs*) echo "[]" ;; *Runtimes*) echo "runc " ;; esac\n'
    assert _probe(tmp_path, docker) == "none"


def test_docker_with_cdi_enabled_gets_the_cdi_overlay(tmp_path):
    docker = 'case "$*" in *CDISpecDirs*) echo \'["/etc/cdi","/run/cdi"]\' ;; *Runtimes*) echo "runc " ;; esac\n'
    assert _probe(tmp_path, docker) == "cdi"


def test_the_toolkit_hook_alone_hands_docker_the_gpu(tmp_path):
    """moby serves `driver: nvidia` whenever nvidia-container-runtime-hook is
    on PATH, with no runtime registered (C0-C-PORT-04)."""
    docker = 'case "$*" in *CDISpecDirs*) echo "[]" ;; *Runtimes*) echo "runc " ;; esac\n'
    assert _probe(tmp_path, docker, "nvidia-container-runtime-hook") == "nvidia"


def test_the_gpu_is_probed_once_per_process(tmp_path):
    """Each load_env used to probe again in a subshell (C0-C-PORT-07)."""
    log = tmp_path / "nvsmi.log"
    _stub(tmp_path, "nvidia-smi", _NVSMI_OK)
    _stub(tmp_path, "docker", 'case "$*" in *Runtimes*) echo "runc nvidia" ;; esac\nexit 0\n')
    code = (
        "uname() { echo Linux; }; ENGINE=docker; load_env; load_env; "
        'qs_usable_vram container >/dev/null; echo "choice=$GPU_CHOICE"'
    )
    out = _out(
        tmp_path, code, STANDALONE_GPU="auto", STANDALONE_MODEL_HOST="container", NVSMI_LOG=str(log)
    )
    assert "choice=nvidia" in out
    assert [c for c in log.read_text().splitlines() if c == "-L"] == ["-L"]


def test_gpu_and_host_gateway_choices_in_local_env_are_honoured(tmp_path):
    """The overlays say env/local.env overrides; it must (C2-C-R2-04)."""
    (tmp_path / "local.env").write_text(
        "STANDALONE_GPU=nvidia\nSTANDALONE_HOST_GATEWAY=off\nSTANDALONE_MODEL_HOST=container\n"
    )
    _stub(tmp_path, "docker", "exit 0\n")
    code = "uname() { echo Linux; }; detect_engine; load_env; overlay_names"
    out = _out(tmp_path, code, STANDALONE_ENGINE="docker", STANDALONE_GPU="")
    assert "compose.gpu-nvidia.yaml" in out
    assert "compose.docker.yaml" not in out


@pytest.mark.parametrize(
    "os_id, line",
    [
        ("fedora", "sudo dnf install -y curl nmap-ncat"),
        ("ubuntu", "sudo apt-get install -y curl netcat-openbsd"),
        ("arch", "sudo pacman -S --needed curl openbsd-netcat"),
    ],
)
def test_missing_host_tools_get_the_exact_install_line(tmp_path, os_id, line):
    assert _out(tmp_path, f"tools_fix_line {os_id} curl nc") == line


def test_preflight_names_a_missing_tool(tmp_path):
    code = 'HOST_TOOLS="python3 r4-no-such-tool-xyz"; os_id() { echo fedora; }; preflight_tools'
    proc = _bash(tmp_path, code)
    assert proc.returncode != 0
    assert "missing host tools: r4-no-such-tool-xyz" in proc.stderr
    assert "sudo dnf install -y r4-no-such-tool-xyz" in proc.stderr


# ── the macOS podman machine: running, and sized to what is needed ──────────

_STUB_PODMAN = """printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  "machine inspect") [ -n "$NO_MACHINE" ] && exit 125; exit 0 ;;
  "machine inspect --format {{.Resources.Memory}}") echo "${VM_MEM:-16384}" ;;
  "machine inspect --format {{.Resources.CPUs}}") echo 4 ;;
  "machine inspect --format {{.Resources.DiskSize}}") echo 100 ;;
  info) [ -n "$NOT_RUNNING" ] && exit 125; exit 0 ;;
esac
exit 0
"""


def _macos(tmp_path: Path, model_host: str = "native", **env: str) -> tuple:
    _stub(tmp_path, "podman", _STUB_PODMAN)
    log = tmp_path / "podman.log"
    log.write_text("")
    code = f"model_host() {{ echo {model_host}; }}; host_mem_mib() {{ echo 16384; }}; ENGINE=podman; preflight_macos"
    proc = _bash(tmp_path, code, STUB_LOG=str(log), **env)
    return proc, log.read_text().splitlines()


def test_a_stopped_podman_machine_is_found_by_podman_info(tmp_path):
    proc, calls = _macos(tmp_path, NOT_RUNNING="1")
    assert proc.returncode != 0
    assert "the podman machine is not running" in proc.stderr
    assert "podman machine start" in proc.stderr
    assert "info" in calls and "machine start" not in calls  # never unasked


def test_a_stopped_podman_machine_is_started_with_consent(tmp_path):
    proc, calls = _macos(tmp_path, NOT_RUNNING="1", ASSUME_YES="1")
    assert "machine start" in calls


def test_the_podman_machine_fix_names_the_size_actually_needed(tmp_path):
    """With the model on the host the VM needs 8 GB, not the 16 GB floor."""
    proc, calls = _macos(tmp_path, VM_MEM="4096")
    assert proc.returncode != 0
    assert "podman machine set --memory 8192 --cpus 4" in proc.stderr
    assert not any(c.startswith("machine set") for c in calls)


def test_a_missing_podman_machine_is_created_from_host_ram_with_consent(tmp_path):
    proc, calls = _macos(tmp_path, NO_MACHINE="1", ASSUME_YES="1")
    init = [c for c in calls if c.startswith("machine init")]
    assert init and init[0].startswith("machine init --memory 8192 --cpus ")
    assert "machine start" in calls


@pytest.mark.parametrize(
    "host_mib, need, size",
    [(16_384, 8, "8192 6 120"), (32_768, 16, "16384 6 120")],
)
def test_the_podman_machine_is_sized_from_the_host(tmp_path, host_mib, need, size):
    assert _out(tmp_path, f"podman_vm_size {host_mib} {need} 10 200") == size


def test_a_mac_that_cannot_spare_the_memory_is_refused(tmp_path):
    code = _defined("podman_vm_size") + "podman_vm_size 16384 16 10 200 || echo refused"
    assert _out(tmp_path, code) == "refused\n"


_STUB_MAC_PODMAN = """printf '%s\\n' "$*" >> "$STUB_LOG"
m="$STUB_DIR/machine"
case "$*" in
  "compose version") [ "$(cat "$m" 2>/dev/null)" = running ] || { echo "Error: machine podman-machine-default is not running but in state stopped" >&2; exit 125; }
    [ -n "$NO_PROVIDER" ] && { echo "Error: looking up compose provider failed" >&2; exit 125; }
    echo "Docker Compose version v2.29.7" ;;
  "machine inspect") [ -f "$m" ] || exit 125 ;;
  "machine init"*) echo stopped > "$m" ;;
  "machine start") echo running > "$m" ;;
  info) [ "$(cat "$m" 2>/dev/null)" = running ] || exit 125 ;;
  "machine inspect --format {{.Resources.Memory}}") echo 16384 ;;
  "machine inspect --format {{.Resources.CPUs}}") echo 4 ;;
  "machine inspect --format {{.Resources.DiskSize}}") echo 100 ;;
esac
exit 0
"""

_MAC = (
    'uname() { case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac; }; '
    'sysctl() { case "$*" in *memsize*) echo 34359738368 ;; *ncpu*) echo 8 ;; *) echo 1 ;; esac; }; '
    "preflight_tools() { :; }; preflight_disk() { :; }; preflight_ports() { :; }; "
)


def _mac_first_run(tmp_path: Path, machine: str, docker: str = _NO_ENGINE, **env: str) -> tuple:
    """`cmd_preflight` on a Mac with podman (and, by default, no usable
    docker CLI) and nothing recorded: the path a first `./r4-local up`
    takes, through detect_engine."""
    _stub(tmp_path, "podman", _STUB_MAC_PODMAN)
    _stub(tmp_path, "docker", docker)
    log = tmp_path / "podman.log"
    log.write_text("")
    if machine:
        (tmp_path / "machine").write_text(machine)
    code = _MAC + 'model_host() { echo container; }; cmd_preflight; echo "engine=$ENGINE"'
    proc = _bash(tmp_path, code, STUB_LOG=str(log), STUB_DIR=str(tmp_path), **env)
    return proc, log.read_text().splitlines()


def test_a_first_run_starts_a_stopped_podman_machine(tmp_path):
    """`podman compose version` fails while the machine is stopped: that must
    not end the run before preflight offers to start it (C0-C-PORT-01)."""
    proc, calls = _mac_first_run(tmp_path, "stopped", ASSUME_YES="1")
    assert proc.returncode == 0, proc.stderr
    assert "engine=podman" in proc.stdout
    assert "machine start" in calls


def test_a_first_run_creates_a_missing_podman_machine(tmp_path):
    proc, calls = _mac_first_run(tmp_path, "", ASSUME_YES="1")
    assert proc.returncode == 0, proc.stderr
    assert [c for c in calls if c.startswith("machine init --memory 16384 --cpus 6")]
    assert "machine start" in calls


def test_a_first_run_without_consent_prints_the_start_command(tmp_path):
    proc, calls = _mac_first_run(tmp_path, "stopped")
    assert proc.returncode != 0
    assert "Start it:  podman machine start" in proc.stderr
    assert "machine start" not in calls


# Docker Desktop installed but not started: its CLI and compose plugin
# answer, the daemon does not.
_DESKTOP_STOPPED = (
    'case "$*" in "compose version") echo "Docker Compose version v2.29.7"; exit 0 ;; esac\n'
    'echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock" >&2; exit 1\n'
)


def _desktop_app(tmp_path: Path) -> dict[str, str]:
    """Docker Desktop installed: its app bundle, at STANDALONE_DESKTOP_APP
    (an override for exactly this; /Applications is not fakeable hermetically)."""
    app = tmp_path / "Docker.app"
    app.mkdir(exist_ok=True)
    return {"STANDALONE_DESKTOP_APP": str(app)}


def test_a_stopped_docker_desktop_is_named_before_a_podman_machine_is_made(tmp_path):
    """Docker Desktop not started and podman with no machine: not a 16 GB
    podman machine made unasked-for under --yes, nor 'Create it: podman
    machine init' to a Docker Desktop user (C3-C-NEW-01)."""
    proc, calls = _mac_first_run(
        tmp_path, "", docker=_DESKTOP_STOPPED, ASSUME_YES="1", **_desktop_app(tmp_path)
    )
    assert proc.returncode != 0
    assert [c for c in calls if c.startswith("machine")] == ["machine inspect"]
    for named in ("Start Docker Desktop", "STANDALONE_ENGINE=podman"):
        assert named in proc.stderr, named
    assert "podman machine init" not in proc.stderr


def test_the_named_way_to_podman_still_makes_the_machine(tmp_path):
    """STANDALONE_ENGINE=podman, as the refusal above says: preflight then
    creates the machine (C3-C-NEW-01)."""
    env = {"ASSUME_YES": "1", "STANDALONE_ENGINE": "podman"}
    env.update(_desktop_app(tmp_path))
    proc, calls = _mac_first_run(tmp_path, "", docker=_DESKTOP_STOPPED, **env)
    assert proc.returncode == 0, proc.stderr
    assert [c for c in calls if c.startswith("machine init --memory 16384")]


def test_an_existing_podman_machine_is_used_while_docker_desktop_is_stopped(tmp_path):
    """A machine someone made is a choice of podman; the run says Docker
    Desktop is not running and how to pick it instead."""
    proc, calls = _mac_first_run(
        tmp_path, "stopped", docker=_DESKTOP_STOPPED, ASSUME_YES="1", **_desktop_app(tmp_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert "engine=podman" in proc.stdout
    assert "machine start" in calls
    assert "Docker Desktop" in proc.stderr and "STANDALONE_ENGINE=docker" in proc.stderr


def test_a_bare_docker_cli_is_not_docker_desktop(tmp_path):
    """A docker CLI with compose that is not Docker Desktop (brew's CLI used
    with podman, or colima) must not pose as a stopped Desktop: with --yes the
    missing podman machine is created, not a refusal naming `open -a Docker`
    (C3-C-RECHECK-01). No app bundle anywhere: STANDALONE_DESKTOP_APP pins a
    path that does not exist, whatever the host has in /Applications."""
    no_app = {"STANDALONE_DESKTOP_APP": str(tmp_path / "no-Docker.app")}
    proc, calls = _mac_first_run(tmp_path, "", docker=_DESKTOP_STOPPED, ASSUME_YES="1", **no_app)
    assert proc.returncode == 0, proc.stderr
    assert "engine=podman" in proc.stdout
    assert [c for c in calls if c.startswith("machine init --memory 16384")]
    assert "Docker Desktop" not in proc.stderr


def test_a_bare_docker_cli_with_a_stopped_machine_only_warns_honestly(tmp_path):
    """Same host, a stopped podman machine: it is used, and no message claims
    Docker Desktop is installed (C3-C-RECHECK-01)."""
    no_app = {"STANDALONE_DESKTOP_APP": str(tmp_path / "no-Docker.app")}
    proc, calls = _mac_first_run(
        tmp_path, "stopped", docker=_DESKTOP_STOPPED, ASSUME_YES="1", **no_app
    )
    assert proc.returncode == 0, proc.stderr
    assert "engine=podman" in proc.stdout
    assert "machine start" in calls
    assert "Docker Desktop" not in proc.stderr


def test_podman_on_a_mac_without_a_compose_provider_gets_the_fix(tmp_path):
    proc, _ = _mac_first_run(tmp_path, "running", NO_PROVIDER="1")
    assert proc.returncode != 0
    assert "finds no compose provider" in proc.stderr
    assert "brew install docker-compose" in proc.stderr


# ── ports ───────────────────────────────────────────────────────────────────

_PS = "ai4s-standalone-umrs-1\t127.0.0.1:5555->5555/tcp, 127.0.0.1:5556-5563->5556-5563/tcp\n"


@pytest.mark.parametrize(
    "port, owner",
    [("5555", "ai4s-standalone-umrs-1"), ("5560", "ai4s-standalone-umrs-1"), ("8080", "")],
)
def test_port_owner_reads_engine_ps_lines_with_ranges(tmp_path, port, owner):
    assert _out(tmp_path, f"printf '{_PS}' | port_owner {port}").strip() == owner


@pytest.mark.parametrize(
    "holder, engine, kind",
    [
        ("docker ai4s-standalone-gateway-nginx-1", "docker", "ours"),
        ("podman ai4s-standalone-gateway-nginx-1", "docker", "other-engine"),
        ("docker umrs-k8s-control-plane", "docker", "foreign"),
    ],
)
def test_a_port_holder_is_this_stack_or_not(tmp_path, holder, engine, kind):
    assert _out(tmp_path, f'holder_kind "{holder}" {engine} ai4s-standalone') == kind


def _with_listener(tmp_path: Path, code: str, **env: str) -> subprocess.CompletedProcess:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        return _bash(tmp_path, code.replace("@PORT@", str(port)), **env)


@pytest.mark.parametrize(
    "holder, ok_, says",
    [
        ("", False, "in use by another program"),
        ("docker ai4s-standalone-gateway-nginx-1", True, "free or held by this stack"),
        ("podman ai4s-standalone-gateway-nginx-1", False, "this stack under podman"),
    ],
)
def test_the_required_ports_are_free_or_this_stacks(tmp_path, holder, ok_, says):
    code = f'REQUIRED_PORTS="@PORT@"; port_holder() {{ printf "%s" "{holder}"; }}; ENGINE=docker; preflight_ports'
    proc = _with_listener(tmp_path, code)
    assert (proc.returncode == 0) is ok_
    assert says in proc.stdout + proc.stderr


def test_quickstart_moves_a_busy_dev_port_in_local_env(tmp_path):
    code = (
        'DEV_PORT_VARS="STANDALONE_POSTGRES_PORT:@PORT@"; REQUIRED_PORTS=""; '
        'port_holder() { echo "docker umrs-k8s-control-plane"; }; ENGINE=docker; '
        "QS_MOVE_DEV_PORTS=1 preflight_ports; echo moved-from=@PORT@"
    )
    proc = _with_listener(tmp_path, code, STANDALONE_PROFILE="dev")
    assert proc.returncode == 0, proc.stderr
    old = int(proc.stdout.split("moved-from=")[1])
    new = int(_kv(tmp_path / "local.env")["STANDALONE_POSTGRES_PORT"])
    assert new > old
    assert _kv(tmp_path / "state")["auto_STANDALONE_POSTGRES_PORT"] == str(new)


def test_other_verbs_refuse_a_busy_dev_port_with_the_fix(tmp_path):
    code = (
        'DEV_PORT_VARS="STANDALONE_POSTGRES_PORT:@PORT@"; REQUIRED_PORTS=""; '
        'port_holder() { echo "docker umrs-k8s-control-plane"; }; ENGINE=docker; preflight_ports'
    )
    proc = _with_listener(tmp_path, code, STANDALONE_PROFILE="dev")
    assert proc.returncode != 0
    assert "Put STANDALONE_POSTGRES_PORT=" in proc.stderr
    assert not (tmp_path / "local.env").exists()


def _dev_ports(tmp_path: Path) -> dict[str, str]:
    """Each dev variable's published port: env/local.env, else its default."""
    host = (LIB / "host.sh").read_text()
    pairs = re.search(r'^DEV_PORT_VARS="([^"]+)"', host, re.M).group(1).split()
    local = _kv(tmp_path / "local.env")
    return {var: local.get(var, default) for var, default in (p.split(":") for p in pairs)}


@pytest.mark.parametrize(
    "busy, moved",
    [
        ("8081", {"STANDALONE_SIDECAR_PORT": "8083"}),
        ("8081 8082", {"STANDALONE_UMRS_HTTP_PORT": "8083", "STANDALONE_SIDECAR_PORT": "8084"}),
    ],
)
def test_a_moved_dev_port_never_lands_on_another_stack_port(tmp_path, busy, moved):
    """8081 held by another program moved the sidecar to 8082, which umrs
    publishes: `up` could not bind (C1-C-R2, C2-C-R2-03)."""
    code = (
        f'port_busy() {{ case " {busy} " in *" $1 "*) return 0 ;; esac; return 1; }}; '
        "port_holder() { :; }; ENGINE=docker; QS_MOVE_DEV_PORTS=1 preflight_dev_ports"
    )
    _out(tmp_path, code, STANDALONE_PROFILE="dev")
    ports = list(_dev_ports(tmp_path).values())
    assert len(set(ports)) == len(ports), ports
    assert _kv(tmp_path / "local.env") == moved


def test_every_dev_port_is_movable_by_its_variable():
    host = (LIB / "host.sh").read_text()
    pairs = re.search(r'^DEV_PORT_VARS="([^"]+)"', host, re.M).group(1).split()
    dev = (STANDALONE / "compose.dev.yaml").read_text()
    for pair in pairs:
        var, default = pair.split(":")
        assert f'"127.0.0.1:${{{var}:-{default}}}:{default}"' in dev, var
    assert len(pairs) == len(_published(_compose("compose.dev.yaml")["services"]))


# ── the engine: docker overlay, selection rule, recorded state ──────────────


@pytest.mark.parametrize(
    "explicit, recorded, docker_ok, podman_ok, engine",
    [
        ("", "", "1", "1", "docker"),  # this host: Docker Engine and podman
        ("", "", "0", "1", "podman"),
        ("", "podman", "1", "1", "podman"),  # the engine holding the volumes
        ("docker", "podman", "1", "1", "docker"),  # STANDALONE_ENGINE overrides
        ("", "", "0", "0", ""),
    ],
)
def test_the_engine_rule(tmp_path, explicit, recorded, docker_ok, podman_ok, engine):
    out = _out(tmp_path, f'choose_engine "{explicit}" "{recorded}" {docker_ok} {podman_ok}')
    assert out == engine


def test_the_docker_overlay_maps_the_host_for_every_service_that_dials_it():
    """podman maps host.containers.internal itself; Docker needs host-gateway.
    umrs and rag reach registry rows (a native ollama) and MCP runtimes."""
    overlay = _compose("compose.docker.yaml")["services"]
    services = _compose()["services"]
    umrs_image = {n for n, s in services.items() if "UMRS_IMAGE" in str(s.get("image", ""))}
    assert umrs_image == {"umrs", "rag"}
    assert set(overlay) == umrs_image
    for name, service in overlay.items():
        assert service == {"extra_hosts": ["host.containers.internal:host-gateway"]}, name


def _file_args(tmp_path: Path, **env: str) -> str:
    _stub(tmp_path, "podman", "exit 0\n")
    _stub(tmp_path, "docker", "exit 0\n")
    return _out(tmp_path, 'detect_engine; load_env; printf "%s\\n" "${FILE_ARGS[@]}"', **env)


@pytest.mark.parametrize(
    "env, applied",
    [
        ({"STANDALONE_ENGINE": "docker"}, True),
        ({"STANDALONE_ENGINE": "podman"}, False),
        ({"STANDALONE_ENGINE": "podman", "STANDALONE_HOST_GATEWAY": "on"}, True),
        ({"STANDALONE_ENGINE": "docker", "STANDALONE_HOST_GATEWAY": "off"}, False),
    ],
)
def test_the_docker_overlay_is_selected_by_the_engine(tmp_path, env, applied):
    assert ("compose.docker.yaml" in _file_args(tmp_path, **env)) is applied


def test_the_recorded_engine_is_used_when_none_is_exported(tmp_path):
    (tmp_path / "state").write_text("engine=podman\n")
    _stub(tmp_path, "docker", "exit 0\n")  # docker answers, but the volumes are podman's
    _stub(tmp_path, "podman", "exit 0\n")
    assert _out(tmp_path, 'detect_engine; echo "$ENGINE"') == "podman\n"


_DOCKER_DOWN = (
    'case "$*" in "compose version") exit 0 ;; esac\n'
    'echo "Cannot connect to the Docker daemon" >&2; exit 1\n'
)


@pytest.mark.parametrize(
    "state, env, origin",
    [
        ("engine=docker\n", {}, "recorded for this stack (env/.state)"),
        ("", {"STANDALONE_ENGINE": "docker"}, "chosen by STANDALONE_ENGINE"),
    ],
)
def test_a_recorded_or_exported_engine_must_answer(tmp_path, state, env, origin):
    """Not a ✓ now and a 'pull failed' later (C0-C-PORT-05, C1-C-R12)."""
    (tmp_path / "state").write_text(state)
    _stub(tmp_path, "docker", _DOCKER_DOWN)
    proc = _bash(tmp_path, 'detect_engine; echo "engine=$ENGINE"', **env)
    assert proc.returncode != 0
    assert f"docker is {origin}, but its daemon does not answer" in proc.stderr
    assert "STANDALONE_ENGINE=podman" in proc.stderr


def test_a_stopped_docker_desktop_is_not_a_pass(tmp_path):
    proc = _bash(
        tmp_path, "docker_mem_mib() { echo 0; }; ENGINE=docker; preflight_docker_desktop 16"
    )
    assert proc.returncode != 0
    assert "Start Docker Desktop" in proc.stderr


_SHIM_DOCKER = 'echo "Emulate Docker CLI using podman." >&2\nexec podman "$@"\n'


def test_the_podman_docker_shim_is_podman(tmp_path):
    """`docker` that execs podman is podman: no docker overlay (C0-C-PORT-06)."""
    _stub(
        tmp_path, "podman", 'case "$*" in version) echo "Client: Podman Engine" ;; esac\nexit 0\n'
    )
    _stub(tmp_path, "docker", _SHIM_DOCKER)
    out = _out(tmp_path, 'detect_engine; load_env; echo "engine=$ENGINE files=$(overlay_names)"')
    assert "engine=podman" in out
    assert "compose.docker.yaml" not in out


def test_the_podman_docker_shim_still_refuses_python_podman_compose(tmp_path):
    _stub(
        tmp_path,
        "podman",
        'case "$*" in "compose version") echo \'compose provider "/usr/bin/podman-compose"\' ;;\n'
        '  version) echo "Client: Podman Engine" ;; esac\nexit 0\n',
    )
    _stub(tmp_path, "docker", _SHIM_DOCKER)
    proc = _bash(tmp_path, "detect_engine")
    assert proc.returncode != 0
    assert "delegating to the Python podman-compose" in proc.stderr


def _minimal_path(tmp_path: Path) -> str:
    """tmp/bin plus the few system tools the script needs, so a tool this
    host happens to have (podman-compose, ollama) cannot change the result."""
    sysbin = tmp_path / "sys"
    sysbin.mkdir(exist_ok=True)
    for tool in (
        "bash",
        "dirname",
        "basename",
        "grep",
        "head",
        "tail",
        "awk",
        "sed",
        "tr",
        "cat",
        "uname",
    ):
        target = shutil.which(tool)
        if target and not (sysbin / tool).exists():
            (sysbin / tool).symlink_to(target)
    return f"{tmp_path / 'bin'}{os.pathsep}{sysbin}"


def test_podman_without_a_compose_provider_is_named(tmp_path):
    """Not 'install podman >= 4.7' to someone who has it (C2-C-R2-08)."""
    _stub(
        tmp_path,
        "podman",
        'case "$*" in "compose version") echo "Error: looking up compose provider failed" >&2; exit 125 ;; esac\n'
        "exit 0\n",
    )
    proc = _bash(tmp_path, "os_id() { echo fedora; }; detect_engine", PATH=_minimal_path(tmp_path))
    assert proc.returncode != 0
    assert "'podman compose' does not run" in proc.stderr
    assert "sudo dnf install -y docker-compose" in proc.stderr


def test_the_dev_overlay_headers_say_how_dev_is_chosen():
    """Both said dev needs STANDALONE_PROFILE=dev and `up` refuses otherwise;
    `up` records the profile now and later verbs follow it (C1-C-R16)."""
    dev_env = (STANDALONE / "env" / "profile.dev.env").read_text()
    dev_yaml = (STANDALONE / "compose.dev.yaml").read_text()
    assert "refuses to apply this overlay otherwise" not in dev_env
    assert "Applied only when STANDALONE_PROFILE=dev" not in dev_yaml
    for text in (dev_env, dev_yaml):
        assert "env/.state" in text and "--dev" in text


def test_the_state_file_is_gitignored():
    ignore = (STANDALONE / ".gitignore").read_text().splitlines()
    assert "env/.state" in ignore
    assert 'STATE_FILE="${STANDALONE_STATE_FILE:-${ENV_DIR}/.state}"' in SCRIPT.read_text()


def test_up_records_the_profile_and_engine(tmp_path):
    (tmp_path / "secrets").write_text("")
    code = (
        f'SECRETS="{tmp_path / "secrets"}"; '
        "cmd_preflight() { :; }; require_for_profile() { :; }; register_model() { :; }; "
        "compose() { :; }; up_profile_flag --dev; cmd_up"
    )
    _stub(tmp_path, "docker", "exit 0\n")
    proc = _bash(tmp_path, code, STANDALONE_ENGINE="docker")
    assert proc.returncode == 0, proc.stderr
    assert _kv(tmp_path / "state") == {"profile": "dev", "engine": "docker"}


def _down_call(tmp_path: Path, **env: str) -> str:
    log = tmp_path / "engine.log"
    _stub(tmp_path, "docker", 'printf "%s\\n" "$*" >> "$STUB_LOG"\n')
    proc = subprocess.run(
        ["bash", str(SCRIPT), "down"],
        env=_isolated_env(tmp_path, {"STANDALONE_ENGINE": "docker", "STUB_LOG": str(log), **env}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return next(line for line in log.read_text().splitlines() if line.endswith(" down"))


def test_later_verbs_read_the_recorded_profile(tmp_path):
    (tmp_path / "state").write_text("profile=dev\nengine=docker\n")
    call = _down_call(tmp_path)
    assert "compose.dev.yaml" in call and "profile.dev.env" in call


def test_an_exported_profile_still_wins_over_the_record(tmp_path):
    (tmp_path / "state").write_text("profile=dev\nengine=docker\n")
    call = _down_call(tmp_path, STANDALONE_PROFILE="hardened")
    assert "compose.dev.yaml" not in call and "profile.hardened.env" in call


# ── ollama: loopback-only on Linux, the context window ──────────────────────


@pytest.mark.parametrize(
    "addrs, scope",
    [
        ("127.0.0.1:11434", "loopback"),  # rccs-wk's systemd ollama
        ("[::1]:11434\\n127.0.0.1:11434", "loopback"),
        ("*:11434", "wide"),
        ("0.0.0.0:11434", "wide"),
        ("", "none"),
    ],
)
def test_the_native_ollama_listen_scope(tmp_path, addrs, scope):
    assert _out(tmp_path, f"printf '{addrs}' | listen_scope").strip() == scope


@pytest.mark.parametrize(
    "os_, answers, scope, cli, pick",
    [
        ("Linux", "1", "loopback", "1", "container loopback-only"),
        ("Linux", "1", "wide", "1", "native listens-wide"),
        ("Linux", "1", "wide", "0", "container wide-not-ours"),  # `docker run -p 11434` ollama
        ("Linux", "1", "unknown", "1", "container listen-unknown"),
        ("Linux", "0", "none", "0", "container no-ollama"),
        ("Darwin", "0", "none", "1", "native macos-ollama"),
        ("Darwin", "0", "none", "0", "container no-ollama"),
        ("WSL", "1", "wide", "1", "container wsl-default"),
    ],
)
def test_where_the_model_runs(tmp_path, os_, answers, scope, cli, pick):
    assert _out(tmp_path, f"choose_model_host {os_} {answers} {scope} {cli}") == pick


_LINUX_LOOPBACK = (
    "uname() { echo Linux; }; is_wsl() { return 1; }; "
    "native_ollama_answers() { return 0; }; ollama_listen_scope() { echo loopback; }; "
)


def test_a_loopback_native_ollama_on_linux_means_the_container_ollama(tmp_path):
    out = _out(tmp_path, _LINUX_LOOPBACK + 'qs_model_host_intent; echo "why=$QS_MODEL_HOST_WHY"')
    assert "why=loopback-only" in out
    assert _kv(tmp_path / "local.env") == {"STANDALONE_MODEL_HOST": "container"}


def test_native_on_loopback_stops_with_the_ollama_host_fix(tmp_path):
    (tmp_path / "local.env").write_text("STANDALONE_MODEL_HOST=native\n")
    proc = _bash(tmp_path, _LINUX_LOOPBACK + "qs_native_ollama qwen2.5:7b")
    assert proc.returncode != 0
    assert 'Environment="OLLAMA_HOST=0.0.0.0:11434"' in proc.stderr
    assert "listens on loopback only" in proc.stderr


@pytest.mark.parametrize(
    "os_, fix",
    [
        ("Linux", 'Environment="OLLAMA_CONTEXT_LENGTH=32768"'),
        ("Darwin", "launchctl setenv OLLAMA_CONTEXT_LENGTH 32768"),
    ],
)
def test_a_short_native_window_is_fixed_or_refused_never_registered(tmp_path, os_, fix):
    code = f"uname() {{ echo {os_}; }}; native_context() {{ echo 4096; }}; qs_native_context qwen2.5:7b"
    proc = _bash(tmp_path, code)
    assert proc.returncode != 0
    assert "4096-token window" in proc.stderr and fix in proc.stderr


def test_a_native_refusal_names_the_container_way_out(tmp_path):
    """Neither a systemd fix nor a missing CLI is the user's only way on
    (C0-C-PORT-03)."""
    _stub(tmp_path, "curl", 'case "$*" in *api/tags*) echo \'{"models":[]}\' ;; esac; exit 0\n')
    code = (
        "uname() { echo Linux; }; native_ollama_answers() { return 0; }; "
        "ollama_listen_scope() { echo wide; }; qs_native_ollama qwen2.5:7b"
    )
    proc = _bash(tmp_path, code, PATH=_minimal_path(tmp_path))
    assert proc.returncode != 0
    assert "no ollama CLI on PATH to pull it" in proc.stderr
    assert "STANDALONE_MODEL_HOST=container in deploy/standalone/env/local.env" in proc.stderr
    short = _bash(
        tmp_path, "uname() { echo Linux; }; native_context() { echo 4096; }; qs_native_context m"
    )
    assert "STANDALONE_MODEL_HOST=container in deploy/standalone/env/local.env" in short.stderr


def test_the_ollama_app_is_found_without_revealing_it_in_finder(tmp_path):
    """`open -R` opened a Finder window before anyone was asked (C0-C-PORT-09)."""
    log = tmp_path / "calls.log"
    _stub(tmp_path, "open", f'echo "open $*" >> "{log}"\n')
    (tmp_path / "home" / "Applications" / "Ollama.app").mkdir(parents=True)
    code = (
        f'uname() {{ echo Darwin; }}; confirm() {{ echo "asked: $1" >> "{log}"; return 1; }}; '
        "qs_start_macos_ollama || echo not-started"
    )
    assert "not-started" in _out(tmp_path, code)
    assert log.read_text().splitlines() == ["asked: Ollama is installed but not running. Start it?"]


def test_no_ollama_app_means_no_question(tmp_path):
    log = tmp_path / "calls.log"
    _stub(tmp_path, "open", f'echo "open $*" >> "{log}"\n')
    code = (
        f'uname() {{ echo Darwin; }}; confirm() {{ echo "asked" >> "{log}"; return 0; }}; '
        "qs_start_macos_ollama || echo not-started"
    )
    assert "not-started" in _out(tmp_path, code)
    assert not log.exists()


def _register_model(tmp_path: Path, served: str = "4096", **env: str) -> tuple:
    log = tmp_path / "compose.log"
    code = (
        f"model_host() {{ echo native; }}; native_context() {{ echo '{served}'; }}; "
        f'compose() {{ printf "%s\\n" "$*" >> "{log}"; cat >/dev/null; }}; register_model qwen2.5:7b'
    )
    proc = _bash(tmp_path, code, **env)
    return proc, (log.read_text() if log.exists() else "")


def test_up_refuses_to_register_a_short_native_window(tmp_path):
    proc, calls = _register_model(tmp_path)
    assert proc.returncode != 0
    assert "OLLAMA_CONTEXT_LENGTH=32768" in proc.stderr
    assert calls == ""


def test_a_short_window_registers_only_when_asked(tmp_path):
    proc, calls = _register_model(tmp_path, STANDALONE_ALLOW_SHORT_CONTEXT="1")
    assert proc.returncode == 0, proc.stderr
    assert "CTX=4096" in calls


def test_up_refuses_a_native_window_it_cannot_read(tmp_path):
    """A warm-up that timed out, or an /api/ps without context_length, is not
    a 32k window (C1-C-R15)."""
    proc, calls = _register_model(tmp_path, served="")
    assert proc.returncode != 0
    assert "a window it does not report" in proc.stderr
    assert 'Environment="OLLAMA_CONTEXT_LENGTH=32768"' in proc.stderr
    assert calls == ""


def test_an_unreadable_window_registers_only_when_asked(tmp_path):
    proc, calls = _register_model(tmp_path, served="", STANDALONE_ALLOW_SHORT_CONTEXT="1")
    assert proc.returncode == 0, proc.stderr
    assert "CTX=32768" in calls
    assert "a window it does not report" in proc.stderr


# ── the client: installed from the checkout ─────────────────────────────────


def test_no_uv_prints_the_one_line_install(tmp_path):
    _stub(tmp_path, "uv", "exit 127\n").unlink()
    proc = _bash(
        tmp_path, "install_client", R4_BIN=str(tmp_path / "venv" / "r4"), PATH="/usr/bin:/bin"
    )
    assert proc.returncode != 0
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in proc.stderr


def test_the_client_is_installed_with_uv_like_make_install_client(tmp_path):
    r4 = tmp_path / "venv" / "r4"
    log = tmp_path / "uv.log"
    _stub(
        tmp_path,
        "uv",
        f'echo "$PWD $*" >> "{log}"; mkdir -p "{r4.parent}"; printf "#!/bin/sh\\n" > "{r4}"; chmod +x "{r4}"\n',
    )
    _stub(tmp_path, "make", f'echo "make $*" >> "{log}"\n')
    proc = _bash(tmp_path, "install_client", R4_BIN=str(r4))
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().splitlines() == [
        f"{ROOT} sync --all-packages --extra client --inexact",
        f"make -C {ROOT} --no-print-directory tui-deps",
    ]
    assert "--extra client" in (ROOT / "Makefile").read_text()


def test_an_installed_client_is_left_alone(tmp_path):
    r4 = _stub(tmp_path, "r4", "exit 0\n")
    _stub(tmp_path, "uv", 'echo "uv must not run" >&2; exit 1\n')
    assert "r4 client:" in _out(tmp_path, "install_client", R4_BIN=str(r4))


# ── register (D-5): a 30-day token on stdin, the active source untouched ────

_STUB_ENGINE_REGISTER = """printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  *" exec -T umrs true") exit "${STUB_UP_RC:-0}" ;;
  *generate-jwt*) printf '{"token": "%s", "claims": {}}\\n' "$STUB_TOKEN" ;;
esac
exit 0
"""

_STUB_R4 = """{ printf 'ARGV:'; for a in "$@"; do printf ' %s' "$a"; done; printf ' |R4_SOURCE=%s\\n' "${R4_SOURCE:-}"; } >> "$STUB_R4_LOG"
case "$1 $2" in
  "source list") cat "$STUB_SOURCES" ;;
  "source add") cat > "$STUB_STDIN"
    [ -n "$STUB_FLIP" ] && printf '[{"name": "standalone", "active": true}]' > "$STUB_SOURCES" ;;
esac
exit 0
"""

# The gateway's answer to POST /gw/projects: the server's row, created or
# the one that already existed (project_id, owner_id, data_class).
_PROJECT_REPLY = (
    '{"status": "ok", "project": {"project_id": "%s", "owner_id": "%s", '
    '"data_class": "%s"}, "created": true}'
)

# curl as the gateway: every call's argv, its stdin, and the reply. Every
# register test gets it, so no test ever reaches a real stack on :8080.
_STUB_CURL = """{ printf 'ARGV:'; for a in "$@"; do printf ' %s' "$a"; done; printf '\\n'; } >> "$STUB_CURL_LOG"
cat >> "$STUB_CURL_STDIN"
if [ "${STUB_CURL_RC:-0}" != 0 ]; then
  echo "curl: (22) The requested URL returned error: 503" >&2; exit "$STUB_CURL_RC"
fi
printf '%s' "$STUB_PROJECT_REPLY"
"""


def _register(tmp_path: Path, *args: str, **env: str) -> tuple:
    _stub(tmp_path, "docker", _STUB_ENGINE_REGISTER)
    _stub(tmp_path, "curl", _STUB_CURL)
    r4 = _stub(tmp_path, "r4-stub", _STUB_R4)
    sources = tmp_path / "sources.json"
    sources.write_text(
        '[{"name": "rccs", "active": true}, {"name": "standalone", "active": false}]'
    )
    (tmp_path / "local.env").write_text("STANDALONE_CHAT_MODEL=qwen2.5:7b\n")
    names = ("engine.log", "r4.log", "stdin", "curl.log", "curl.stdin")
    files = {k: tmp_path / k for k in names}
    for f in files.values():
        f.write_text("")
    extra = {
        "STANDALONE_ENGINE": "docker",
        "R4_BIN": str(r4),
        "STUB_LOG": str(files["engine.log"]),
        "STUB_R4_LOG": str(files["r4.log"]),
        "STUB_STDIN": str(files["stdin"]),
        "STUB_SOURCES": str(sources),
        "STUB_TOKEN": TOKEN,
        "STUB_CURL_LOG": str(files["curl.log"]),
        "STUB_CURL_STDIN": str(files["curl.stdin"]),
        "STUB_PROJECT_REPLY": _PROJECT_REPLY % ("default", "scientist", "non_sensitive"),
        **env,
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "register", *args],
        env=_isolated_env(tmp_path, extra),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc, {k: f.read_text() for k, f in files.items()}


def test_register_pipes_a_30_day_token_into_source_add(tmp_path):
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    mint = next(c for c in logs["engine.log"].splitlines() if "generate-jwt" in c)
    assert "--days 30 --profile standalone --gateway-url http://localhost:8080" in mint
    assert (
        "ARGV: source add standalone --kind platform --url http://localhost:8080 "
        "--zmq-host 127.0.0.1 --zmq-port 5555 --api-key-stdin --default-model qwen2.5:7b "
        "--project default --replace"
    ) in logs["r4.log"]
    assert logs["stdin"].strip() == TOKEN


def test_register_never_switches_or_logs_in(tmp_path):
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    calls = logs["r4.log"].splitlines()
    assert not [c for c in calls if c.startswith(("ARGV: source use", "ARGV: auth login"))]
    add = next(c for c in calls if c.startswith("ARGV: source add"))
    assert add.endswith("|R4_SOURCE=standalone")  # first-source auto-activation is pinned off
    assert "active r4 source is unchanged: rccs" in proc.stdout


def test_the_token_never_reaches_argv_or_output(tmp_path):
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    texts = (proc.stdout, proc.stderr, logs["engine.log"], logs["r4.log"], logs["curl.log"])
    for text in texts:
        assert TOKEN not in text


def test_register_stops_when_the_active_source_changed(tmp_path):
    proc, _ = _register(tmp_path, STUB_FLIP="1")
    assert proc.returncode != 0
    assert "your active r4 source changed from 'rccs' to 'standalone'" in proc.stderr


def test_register_needs_a_running_stack_before_minting(tmp_path):
    proc, logs = _register(tmp_path, STUB_UP_RC="1")
    assert proc.returncode != 0
    assert "umrs is not running" in proc.stderr
    assert "generate-jwt" not in logs["engine.log"]
    assert logs["curl.log"] == ""


# ── register creates the stack's project row (2026-10-16) ───────────────────
# The stand-alone default class applies only to a project ROW, and the server
# writes one only on POST /gw/projects (its idempotent ensure: an existing
# row comes back as it is). Nothing on the stand-alone path called it, so the
# default reached no row and the narrowing command register printed answered
# "project default not found" (wave-2 review, spec F1).


def test_register_creates_the_project_row_through_the_gateway(tmp_path):
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    calls = logs["curl.log"].splitlines()
    assert len(calls) == 1, calls
    assert calls[0].endswith(" http://localhost:8080/gw/projects"), calls[0]
    for part in ("-X POST", '{"project_id": "default"}'):
        assert part in calls[0], part


def test_the_project_create_token_rides_stdin_never_a_proxy(tmp_path):
    """The bearer header comes on curl's stdin (-H @-), never its argv, and
    no proxy ever sees it."""
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for part in ("-H @-", "--noproxy *"):
        assert part in logs["curl.log"], part
    assert logs["curl.stdin"].strip() == f"Authorization: Bearer {TOKEN}"
    # -q (--disable) works only as curl's FIRST argument: without it a
    # `verbose` or `trace-ascii` line in the user's .curlrc prints or
    # writes the bearer header (wave-2 fix review, 2026-10-16).
    assert logs["curl.log"].startswith("ARGV: -q "), logs["curl.log"]


class _ProjectRowHandler(BaseHTTPRequestHandler):
    """The gateway's POST /gw/projects, answering the created row."""

    def do_POST(self):  # noqa: N802 — the http.server hook name
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = (_PROJECT_REPLY % ("default", "scientist", "non_sensitive")).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs the host's curl")
def test_the_project_create_ignores_the_users_curlrc(tmp_path):
    """The real curl against a local gateway stand-in, with `verbose` in
    every .curlrc curl would read: the token is never printed."""
    for rc_dir in (tmp_path / "home", tmp_path / "xdg"):
        rc_dir.mkdir(exist_ok=True)
        (rc_dir / ".curlrc").write_text("verbose\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProjectRowHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code = (
            f"STACK_URL=http://127.0.0.1:{server.server_address[1]}\n"
            'printf "%s\\n" "$TOK" | ensure_stack_project default'
        )
        proc = _bash(tmp_path, code, TOK=TOKEN, CURL_HOME=str(tmp_path / "home"))
    finally:
        server.shutdown()
        server.server_close()
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "non_sensitive scientist"
    assert TOKEN not in proc.stdout + proc.stderr


def test_the_project_is_created_as_the_registering_user(tmp_path):
    """Its own token (the second mint) names the same user and project, so
    that user owns the row and may narrow it."""
    proc, logs = _register(tmp_path)
    assert proc.returncode == 0, proc.stderr
    mints = [c for c in logs["engine.log"].splitlines() if "generate-jwt" in c]
    assert len(mints) == 2
    assert all("--user scientist --project default" in c for c in mints)


def test_the_narrowing_command_is_printed_only_after_the_row_exists(tmp_path):
    proc, logs = _register(tmp_path, STUB_CURL_RC="22")
    assert proc.returncode != 0
    assert "creating project 'default' on the stack failed" in proc.stderr
    assert "data-class set" not in proc.stdout + proc.stderr
    assert logs["curl.log"]  # it was tried


def test_the_printed_narrowing_command_names_the_project_register_created(tmp_path):
    reply = _PROJECT_REPLY % ("lab1", "alice", "non_sensitive")
    proc, logs = _register(tmp_path, "alice", "lab1", STUB_PROJECT_REPLY=reply)
    assert proc.returncode == 0, proc.stderr
    assert '{"project_id": "lab1"}' in logs["curl.log"]
    out = " ".join(proc.stdout.split())
    assert "project lab1 is non_sensitive" in out
    assert re.search(r"R4_SOURCE=standalone \S+ project data-class set lab1 sensitive", out)


def test_register_says_the_class_the_server_gave_the_row(tmp_path):
    """A row that is already sensitive (an operator default, or an owner who
    narrowed it on an earlier run) is reported as such: no narrowing offer."""
    reply = _PROJECT_REPLY % ("default", "scientist", "sensitive")
    proc, _ = _register(tmp_path, STUB_PROJECT_REPLY=reply)
    assert proc.returncode == 0, proc.stderr
    out = " ".join(proc.stdout.split())
    assert "project default is sensitive" in out
    assert "data-class set default sensitive" not in out


def test_register_names_another_owner_of_the_project(tmp_path):
    """Only the owner may change the class: a row an earlier register made
    for another user is named, not hidden."""
    reply = _PROJECT_REPLY % ("default", "alice", "non_sensitive")
    proc, _ = _register(tmp_path, STUB_PROJECT_REPLY=reply)
    assert proc.returncode == 0, proc.stderr
    assert "belongs to 'alice'" in " ".join(proc.stderr.split())


@pytest.mark.parametrize("klass", ["non_sensitive", "sensitive"])
def test_register_names_the_institutional_list_the_stack_uses(tmp_path, klass):
    """The stack's list is the packaged one: nothing passes a site's list to
    its umrs, and register never logs r4 in to fetch one, so the message
    names that list instead of promising 'your institution's provider'."""
    reply = _PROJECT_REPLY % ("default", "scientist", klass)
    proc, _ = _register(tmp_path, STUB_PROJECT_REPLY=reply)
    assert proc.returncode == 0, proc.stderr
    out = " ".join(proc.stdout.split())
    assert "your institution's provider" not in out
    assert "the institutional provider list (RIKEN's Rikyu)" in out


def test_the_stack_defaults_new_projects_to_non_sensitive(monkeypatch):
    """The value reaches umrs, the service whose project create reads it, no
    env file overrides it, and umrs reads it as non_sensitive (a typo would
    silently mean sensitive)."""
    pdc = pytest.importorskip("umrs.shared.project_data_class")
    env = _compose()["services"]["umrs"]["environment"]
    value = _effective(str(env["UMRS_PROJECT_DEFAULT_DATA_CLASS"]))
    assert value == "non_sensitive"
    for profile in PROFILES:
        settings = {**_env("base.env"), **_env(f"profile.{profile}.env")}
        assert settings.get("UMRS_PROJECT_DEFAULT_DATA_CLASS", value) == value, profile
    monkeypatch.setenv("UMRS_PROJECT_DEFAULT_DATA_CLASS", value)
    assert pdc.default_project_data_class() == "non_sensitive"


VENV_R4 = ROOT / ".venv" / "bin" / "r4"


def _real_r4(tmp_path: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    proc = subprocess.run(
        [str(VENV_R4), *args], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _register_with_real_client(tmp_path: Path) -> tuple[dict, str]:
    """`register` into a config of the test's own, with a cluster source active."""
    import json

    _real_r4(tmp_path, "source", "add", "cluster", "--url", "https://cluster.invalid")
    _real_r4(tmp_path, "source", "use", "cluster")
    isolated = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "R4_BIN": str(VENV_R4),
    }
    proc, _ = _register(tmp_path, **isolated)
    assert proc.returncode == 0, proc.stderr
    rows = {r["name"]: r for r in json.loads(_real_r4(tmp_path, "source", "list", "--json"))}
    pointer = tmp_path / "xdg" / "umrs" / "current_connection_profile"
    return rows, pointer.read_text().strip()


@pytest.mark.skipif(not VENV_R4.exists(), reason="no r4 client in .venv (make install-client)")
def test_register_with_the_real_client_keeps_the_active_source(tmp_path):
    """The contract with `r4 source add` itself: the stack's model and project
    land on the source, the key is stored, the source that was active stays
    active, and so does the umrs CLI's current profile."""
    rows, umrs_profile = _register_with_real_client(tmp_path)
    assert {name: row["active"] for name, row in rows.items()} == {
        "cluster": True,
        "standalone": False,
    }
    fields = ("url", "default_model", "project", "has_api_key")
    assert {k: rows["standalone"][k] for k in fields} == {
        "url": "http://localhost:8080",
        "default_model": "qwen2.5:7b",
        "project": "default",
        "has_api_key": True,
    }
    assert umrs_profile == "cluster"


@pytest.mark.skipif(not VENV_R4.exists(), reason="no r4 client in .venv (make install-client)")
def test_register_never_writes_into_an_exported_umrs_config(tmp_path, monkeypatch):
    """UMRS_CONFIG wins over XDG_CONFIG_HOME in r4: exported in the shell
    that runs the tests, it would receive the test's token (C1-C-R3)."""
    sentinel = tmp_path / "developer-umrs-config"
    monkeypatch.setenv("UMRS_CONFIG", str(sentinel))
    _register_with_real_client(tmp_path)
    assert not sentinel.exists()
    assert (tmp_path / "xdg" / "umrs" / "connections" / "standalone.json").exists()


# ── the smoke test: a pass needs a real ingest and a real retrieval ─────────

_SOURCES = "Stilling well.\\n\\n--- Sources ---\\n\\n[1] r4-local-sample.md (score: 0.91)\\n"


def _smoke(tmp_path: Path, chat: str, artifact: str, vectors: str, query: str) -> tuple:
    """ARTIFACT: never | yes (a row from an earlier run) | after-upload.
    VECTORS: a count, or after-upload:N (vectors only once uploaded again)."""
    flag = tmp_path / "uploaded"
    code = f"""
sleep() {{ :; }}
r4_on_stack() {{
  case "$1 $2" in
    "knowledge upload") touch "{flag}"; echo "Queued for knowledge ingest: job-1" ;;
    "knowledge query") printf '%b' "{query}" ;;
    chat*) printf '%b\\n' "{chat}" ;;
  esac
}}
stack_sql() {{
  case "$1" in
    *knowledge_artifacts*) [ "{artifact}" = after-upload ] && [ -f "{flag}" ] && printf art-1; [ "{artifact}" = yes ] && printf art-1; : ;;
    *knowledge_node_embeddings*) v="{vectors}"
      case "$v" in after-upload:*) [ -f "{flag}" ] && printf '%s' "${{v#after-upload:}}"; : ;; *) printf '%s' "$v" ;; esac ;;
  esac
}}
rc=0; smoke_test || rc=$?; echo "rc=$rc"
"""
    out = _out(tmp_path, code)
    return out, out.count("PASS"), out.strip().endswith("rc=0")


def test_the_smoke_test_passes_on_chat_ingest_and_retrieval(tmp_path):
    out, passes, ok_ = _smoke(tmp_path, "ready", "after-upload", "3", _SOURCES)
    assert ok_ and passes == 3, out
    assert "artifact art-1, 3 vectors" in out and "earlier run" not in out
    assert (tmp_path / "uploaded").exists()


def test_a_sample_ingested_before_is_checked_not_uploaded_again(tmp_path):
    out, passes, ok_ = _smoke(tmp_path, "ready", "yes", "3", _SOURCES)
    assert ok_ and passes == 3, out
    assert "(ingested by an earlier run)" in out
    assert not (tmp_path / "uploaded").exists()


@pytest.mark.parametrize(
    "chat, artifact, vectors, query, passes",
    [
        ("ready", "never", "0", _SOURCES, 1),  # no knowledge row: no retrieval either
        ("ready", "yes", "0", _SOURCES, 1),  # a row but no vectors
        ("ready", "yes", "3", "I do not know.", 2),  # retrieval did not cite the sample
        ("Error: No active endpoint for 'qwen2.5:7b'", "yes", "3", _SOURCES, 2),
    ],
)
def test_the_smoke_test_never_passes_without_the_evidence(
    tmp_path, chat, artifact, vectors, query, passes
):
    out, n, ok_ = _smoke(tmp_path, chat, artifact, vectors, query)
    assert not ok_, out
    assert n == passes, out


def test_an_earlier_sample_without_vectors_is_ingested_again(tmp_path):
    """An ingest that never finished (umrs restarted mid-chain) must not fail
    every later run: the sample is uploaded again (C1-C-R4)."""
    out, passes, ok_ = _smoke(tmp_path, "ready", "yes", "after-upload:4", _SOURCES)
    assert ok_ and passes == 3, out
    assert (tmp_path / "uploaded").exists()
    assert "artifact art-1, 4 vectors (re-ingested" in out


def test_the_smoke_chat_runs_as_the_printed_command_does(tmp_path):
    """No --model, and no R4_MODEL / R4_PROJECT_ID from the shell: a PASS
    proves the source's own default model and project (C1-C-R14)."""
    log = tmp_path / "r4.log"
    r4 = _stub(
        tmp_path,
        "r4-stub",
        f'echo "argv=$* model=${{R4_MODEL:-}} project=${{R4_PROJECT_ID:-}} source=${{R4_SOURCE:-}}"'
        f' >> "{log}"; echo ready\n',
    )
    out = _out(
        tmp_path,
        "smoke_chat qwen2.5:7b && echo chat-pass",
        R4_BIN=str(r4),
        R4_MODEL="gpt-oss-120b",
        R4_PROJECT_ID="cluster-proj",
    )
    assert "chat-pass" in out
    assert log.read_text().splitlines() == [
        "argv=chat Reply with the single word: ready model= project= source=standalone"
    ]


def test_the_closing_usage_runs_the_checkouts_client(tmp_path):
    """A fresh machine has no r4 on PATH: the usage names .venv (C0-C-PORT-10).
    `r4 --source X chat` loses X to chat's own --source, so R4_SOURCE it is."""
    proc = _bash(tmp_path, "print_stack_usage", R4_MODEL="gpt-oss-120b")
    venv = ROOT / ".venv"
    assert proc.returncode == 0, proc.stderr
    assert f'R4_SOURCE=standalone {venv}/bin/r4 chat "hello"' in proc.stdout
    assert f"source {venv}/bin/activate && export R4_SOURCE=standalone" in proc.stdout
    assert "--source standalone chat" not in proc.stdout
    assert "R4_MODEL is set in this shell and overrides the 'standalone' source" in proc.stderr


def test_the_bundled_sample_answers_the_smoke_question():
    sample = (STANDALONE / "sample" / "r4-local-sample.md").read_text()
    client = (LIB / "client.sh").read_text()
    assert 'SAMPLE_FILE="${HERE}/sample/r4-local-sample.md"' in client
    assert "Kestrel Bay" in sample and "stilling well damps the waves" in sample
    assert "public domain" in sample
    assert len(sample) < 4000  # one chunk, one quick extraction


# ── portability and the wrapper ─────────────────────────────────────────────

_BASH4_ONLY = (
    r"declare -A",
    r"\$\{[A-Za-z_]+,,\}",
    r"\$\{[A-Za-z_]+\^\^\}",
    r"\bmapfile\b",
    r"\breadarray\b",
    r";&",
)


@pytest.mark.parametrize(
    "path", sorted(LIB.glob("*.sh")) + [SCRIPT, R4_LOCAL], ids=lambda p: p.name
)
def test_the_scripts_run_on_macos_bash_3_2(path):
    code = _code(path)
    found = [p for p in _BASH4_ONLY if re.search(p, code)]
    assert found == [], f"{path.name}: bash-4-only syntax {found}"


@pytest.mark.parametrize(
    "path", sorted(LIB.glob("*.sh")) + [SCRIPT, R4_LOCAL], ids=lambda p: p.name
)
def test_no_pipe_ends_in_grep_q(path):
    """`... | grep -q` under `set -o pipefail`: grep exits at the first match,
    the writer dies of SIGPIPE, and the match reads as a failure (it hid the
    podman-docker shim from its own probe)."""
    assert not re.search(r"(?<!\|)\|(?!\|)\s*grep\s+-[A-Za-z]*q", _code(path))


def test_r4_local_forwards_to_the_standalone_verbs(tmp_path):
    assert os.access(R4_LOCAL, os.X_OK)
    text = R4_LOCAL.read_text()
    assert re.search(r'^\s+up\) shift; need_umrs; exec "\$STANDALONE" quickstart "\$@"', text, re.M)
    assert "down|status|doctor|logs|register)" in text
    log = tmp_path / "engine.log"
    _stub(tmp_path, "docker", 'printf "%s\\n" "$*" >> "$STUB_LOG"\n')
    env = _isolated_env(tmp_path, {"STANDALONE_ENGINE": "docker", "STUB_LOG": str(log)})
    proc = subprocess.run(
        [str(R4_LOCAL), "status"], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().rstrip().endswith(" ps")


def test_logs_without_a_service_follows_every_service(tmp_path):
    """`compose logs -f ""` is 'no such service' (C2-C-R2-05)."""
    log = tmp_path / "engine.log"
    _stub(
        tmp_path,
        "docker",
        'for a in "$@"; do printf "[%s]" "$a"; done >> "$STUB_LOG"; echo >> "$STUB_LOG"\n',
    )
    env = _isolated_env(tmp_path, {"STANDALONE_ENGINE": "docker", "STUB_LOG": str(log)})
    proc = subprocess.run(
        [str(R4_LOCAL), "logs"], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().splitlines()[-1].endswith("[logs][-f]")


def test_r4_local_refuses_an_unknown_verb():
    proc = subprocess.run(
        [str(R4_LOCAL), "reset"], capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 2
    assert "unknown command reset" in proc.stderr


# ── the docs say what the code does ─────────────────────────────────────────

MANUAL = ROOT / "docs" / "user-manual" / "29-standalone-deployment.md"


def test_the_readme_contradictions_are_gone():
    readme = (STANDALONE / "README.md").read_text()
    header = readme.split("\n## ", 1)[0]
    assert "Not yet covered: the knowledge-graph" not in header
    assert "Then set `STANDALONE_CHAT_MODEL` in `env/base.env`" not in readme
    assert "The client needs no changes at all" not in readme
    assert "./r4-local up" in readme and "env/.state" in readme


def test_chapter_29_starts_with_the_one_command_path():
    text = MANUAL.read_text()
    first = text.split("\n## ", 2)[1]
    assert first.startswith("One command")
    assert "./r4-local up" in first
    for row in ("Linux", "macOS", "WSL2"):
        assert re.search(rf"^\| \*\*{row}", first, re.M), row
    for fact in (
        ".wslconfig",
        "systemd=true",
        "OLLAMA_CONTEXT_LENGTH",
        "podman machine",
        "not yet run live",
    ):
        assert fact in first, fact


def test_chapter_29_states_the_register_contract():
    """`r4 --source X chat` loses X to chat's own --source (root flag dropped,
    2026-10-15), so the docs give the R4_SOURCE form, which every command
    honours (C1-C-R14)."""
    text = " ".join(MANUAL.read_text().split())  # line wrapping does not matter
    assert "R4_SOURCE=standalone r4 chat" in text
    assert "never runs `r4 source use` or `r4 auth login`" in text
    assert "30-day" in text
    readme = (STANDALONE / "README.md").read_text()
    for doc in (MANUAL.read_text(), readme):
        assert "r4 --source standalone" not in doc


def test_the_docs_name_the_umrs_profile_and_where_the_token_lives():
    """register leaves the umrs CLI's current profile alone, and r4 stores
    the key in its config (C1-C-R5, C2-C-R2-07)."""
    manual = MANUAL.read_text()
    readme = " ".join((STANDALONE / "README.md").read_text().split())
    assert "umrs --profile standalone info" in manual
    assert not re.search(r"^umrs info\b", manual, re.M)
    joined = " ".join(manual.split())
    assert "never a command-line argument, a file or printed" not in joined
    assert "never an argument, a file or output" not in readme
    for text in (joined, readme):
        assert "(0600)" in text


def _assert_doc_marks_live_status(text: str) -> None:
    lowered = text.lower()
    assert "ran live on linux 2026-10-16" in lowered
    assert "not yet run live" in lowered
    assert "on Linux, WSL2 and Docker" not in text
    assert "on Linux, WSL2 or Docker" not in text


def test_the_docs_mark_what_has_and_has_not_run_live():
    """The Linux one-command path ran live 2026-10-16 (rccs-wk, Docker
    Engine); the docs record that and still name every path that has not
    (C1-C-R6's honesty rule, updated by the live run)."""
    readme = " ".join((STANDALONE / "README.md").read_text().split())
    manual = " ".join(re.sub(r"^> ?", "", MANUAL.read_text(), flags=re.M).split())
    for text in (readme, manual):
        _assert_doc_marks_live_status(text)
    assert "Verified there: registration" not in manual
    for thing in ("`./r4-local up`", "`register`", "Docker"):
        assert thing in manual.split("Not yet run live")[0], thing
    # the banner names what has not: the GPU overlays (the live host was
    # CPU-only) and the macOS and WSL2 one-command paths
    assert "the gpu overlays and the macos and wsl2 one-command paths have" in manual.lower()


def test_the_docs_say_a_stopped_docker_desktop_is_named_on_a_mac():
    """Not 'podman counts as soon as it is installed' alone (C3-C-NEW-01)."""
    readme = " ".join((STANDALONE / "README.md").read_text().split())
    manual = " ".join(MANUAL.read_text().split())
    for text in (readme, manual):
        assert (
            "Docker Desktop installed (its app bundle — a bare docker CLI such as brew's docker or colima is not Desktop) but not running, podman counts only if its machine"
            in text
        )
    assert "`Docker Desktop is installed but not running, and podman has no machine yet`" in manual


def test_the_prerequisites_name_the_compose_provider_and_disk_for_every_os():
    """macOS and WSL2 rows named neither (C2-C-R2-08)."""
    first = MANUAL.read_text().split("\n## ", 2)[1]
    for row in ("Linux", "macOS", "WSL2"):
        line = next(x for x in first.splitlines() if x.startswith(f"| **{row}"))
        assert "Docker Compose v2" in line, row
        assert "15 GB free disk to run, 30 GB to build" in line, row
    assert "brew install podman docker-compose" in first
    readme = (STANDALONE / "README.md").read_text()
    assert "| OS | Linux (Docker Engine or rootless podman)" in readme


# ── project data classes: the manual says what the code does (2026-10-16) ───
# Wave-2 review DOC-1 / R4-6: no user page said what the classes mean, chapter
# 29 still said only non_sensitive projects may use a hosted backend, and the
# two operator variables were documented nowhere an operator looks.

USER_MANUAL = ROOT / "docs" / "user-manual"
PROJECTS_CHAPTER = USER_MANUAL / "05-projects-models.md"
ENV_APPENDIX = USER_MANUAL / "appendix-b-env-vars.md"
DATA_CLASS_PAGES = (
    MANUAL,
    PROJECTS_CHAPTER,
    ENV_APPENDIX,
    USER_MANUAL / "11-sources.md",
    USER_MANUAL / "13-cli-reference.md",
)


def test_chapter_29_states_the_stand_alone_class_and_how_to_narrow():
    text = " ".join(MANUAL.read_text().split())
    assert "only `non_sensitive` projects may" not in text
    assert "start as `non_sensitive`" in text
    assert "R4_SOURCE=standalone r4 project data-class set default sensitive" in text
    assert "(05-projects-models.md#data-class-what-may-leave-your-machine)" in text


def test_the_manual_explains_project_data_classes():
    text = " ".join(PROJECTS_CHAPTER.read_text().split())
    assert "### Data class: what may leave your machine" in PROJECTS_CHAPTER.read_text()
    for fact in (
        "`sensitive`",
        "`non_sensitive`",
        "your institution",
        "on your own machine",
        "refused",
        "r4 project data-class get",
        "r4 project data-class set",
        "--confirm",
    ):
        assert fact in text, fact


def test_the_data_class_pages_carry_no_internal_codes():
    """Main swept ADR codes from the user manual on 2026-10-16; gap-row ids
    (D-nn) are internal too."""
    for page in DATA_CLASS_PAGES:
        text = page.read_text()
        assert not re.search(r"\bADR-\d+", text), page.name
        assert not re.search(r"\bD-\d{1,2}\b", text), page.name


def test_the_operator_variables_are_documented_with_the_packaged_default():
    pdc = pytest.importorskip("umrs.shared.project_data_class")
    text = " ".join(ENV_APPENDIX.read_text().split())
    for var in ("UMRS_INSTITUTIONAL_PROVIDER_HOSTS", "UMRS_PROJECT_DEFAULT_DATA_CLASS"):
        assert f"`{var}`" in text, var
    for host in pdc.DEFAULT_INSTITUTIONAL_PROVIDER_HOSTS:
        assert f"`{host}`" in text, host


def test_the_client_never_calls_an_institutional_provider_public():
    assert "Rikyu-style public" not in (LIB / "client.sh").read_text()


# ── data-class docs: the fix review of 2026-10-16 ──────────────────────────
# A project made only with `r4 project create` has no server row, so no class
# and no refusal; a loopback address cannot show what it forwards to; the
# appendix must read allowlist entries as the server does; and the stand-alone
# stack can only use the packaged list.

STANDALONE_LIST_LIMIT = (
    "On a stand-alone stack the packaged list applies (RIKEN's Rikyu) and cannot be changed yet."
)


def test_the_manual_says_a_project_only_on_this_machine_has_no_class():
    text = " ".join(PROJECTS_CHAPTER.read_text().split())
    assert "Every project on a platform deployment has a" not in text
    for fact in (
        "A project you made only with `r4 project create` exists on your machine alone",
        "nothing is refused for it",
        "`./r4-local register <user> <project>`",
    ):
        assert fact in text, fact


def test_the_manual_says_a_local_address_may_forward_elsewhere():
    text = " ".join(PROJECTS_CHAPTER.read_text().split())
    for fact in ("a local relay", "`ssh -L`", "counts as on your machine"):
        assert fact in text, fact


def test_the_appendix_reads_allowlist_entries_as_the_server_does(monkeypatch):
    pdc = pytest.importorskip("umrs.shared.project_data_class")
    entries = "https://llm.example.ac.jp:8443/v1 llm2.example.ac.jp. user:pw@x.example.ac.jp"
    monkeypatch.setenv("UMRS_INSTITUTIONAL_PROVIDER_HOSTS", entries)
    assert pdc.institutional_provider_hosts() == {"llm.example.ac.jp", "llm2.example.ac.jp"}
    text = " ".join(ENV_APPENDIX.read_text().split())
    assert "does not match the host" not in text
    for fact in (
        "A scheme, port, path or trailing dot is dropped and the bare host is used",
        "is ignored, and a warning is logged",
    ):
        assert fact in text, fact


def test_the_stand_alone_institutional_list_is_the_packaged_one_and_said_so():
    umrs = _compose()["services"]["umrs"]
    # When compose passes a list through, these pages must change with it.
    assert "UMRS_INSTITUTIONAL_PROVIDER_HOSTS" not in umrs["environment"]
    assert "env_file" not in umrs
    for page in (STANDALONE / "README.md", MANUAL, ENV_APPENDIX):
        assert STANDALONE_LIST_LIMIT in " ".join(page.read_text().split()), page.name


# ── the manual says a class change is recorded (integration, 2026-10-16) ───
# The server now writes the audit event project.data_class_changed for every
# change (who, from which class to which); the CLI's confirmation already
# says "recorded with your identity". The user manual must say it too.


def test_the_manual_says_a_class_change_is_recorded():
    projects_api = ROOT / "umrs" / "src" / "umrs" / "api" / "projects.py"
    assert '"project.data_class_changed"' in projects_api.read_text()
    text = " ".join(PROJECTS_CHAPTER.read_text().split())
    assert "The deployment records every change of class" in text


# ── no audit ids on the pages this branch wrote (integration, 2026-10-16) ───
# Wave 1 added "G-4" (an internal audit finding id) to chapter 11 twice; main's
# user manual carries no such id there. The pages this branch wrote stay clean.


def test_the_branch_user_pages_carry_no_audit_ids():
    for page in (MANUAL, PROJECTS_CHAPTER, USER_MANUAL / "11-sources.md"):
        found = re.findall(r"\b[GU]-\d+\b", page.read_text())
        assert not found, (page.name, found)


# ── the published UMRS image (repo-rename plan P4b, 2026-11-06) ───────────
# A laptop used to build umrs/docker/Dockerfile.server itself (~16 GB of
# transient layers a macOS podman VM never returns). The stack now pulls the
# image .github/workflows/standalone-image.yaml publishes; building is opt-in.

_DIGEST_REF = re.compile(r"^ghcr\.io/r-ccs-hpbd/r4-standalone-umrs@sha256:[0-9a-f]{64}$")


def _script_image_default() -> str:
    found = re.search(r'^UMRS_IMAGE_DEFAULT="([^"]+)"$', SCRIPT.read_text(), re.M)
    assert found, "UMRS_IMAGE_DEFAULT missing from standalone.sh"
    return found.group(1)


def test_the_stack_pulls_one_digest_pinned_image_by_default():
    default = _script_image_default()
    assert _DIGEST_REF.match(default), default
    services = _compose()["services"]
    for name in ("umrs", "rag"):
        assert services[name]["image"] == "${UMRS_IMAGE:-" + default + "}", name
        assert "build" not in services[name], name


def test_building_is_an_opt_in_overlay_with_the_old_local_name():
    overlay = _compose("compose.build.yaml")["services"]
    assert overlay["umrs"]["image"] == overlay["rag"]["image"] == "ai4s-standalone-umrs:latest"
    assert overlay["umrs"]["build"]["dockerfile"] == "umrs/docker/Dockerfile.server"
    assert "download.pytorch.org/whl/cpu" in overlay["umrs"]["build"]["args"]["TORCH_INDEX_URL"]
    assert overlay["rag"]["pull_policy"] == "never"


@pytest.mark.parametrize("build, applied", [("", False), ("0", False), ("1", True)])
def test_standalone_build_selects_the_build_overlay(tmp_path, build, applied):
    env = {"STANDALONE_ENGINE": "podman", "STANDALONE_BUILD": build}
    assert ("compose.build.yaml" in _file_args(tmp_path, **env)) is applied


_RECORDING_ENGINE = (
    'echo "$*" >> "$(dirname "$0")/calls"\n'
    'case "$1" in image) exit 1 ;; pull) exit "${PULL_RC:-0}" ;; esac\n'
)


def test_a_missing_image_is_pulled_with_the_engine_cli(tmp_path):
    _stub(tmp_path, "podman", _RECORDING_ENGINE)
    _out(tmp_path, "ENGINE_BIN=podman; ensure_umrs_image")
    calls = (tmp_path / "bin" / "calls").read_text().splitlines()
    assert calls == [f"image inspect {_script_image_default()}", f"pull {_script_image_default()}"]


def test_a_failed_pull_names_the_login_and_the_build_fallback(tmp_path):
    _stub(tmp_path, "podman", _RECORDING_ENGINE)
    proc = _bash(tmp_path, "ENGINE_BIN=podman; ensure_umrs_image", PULL_RC="1")
    assert proc.returncode != 0
    assert "podman login ghcr.io -u <github-user>" in proc.stderr + proc.stdout
    assert "STANDALONE_BUILD=1 ./standalone.sh up" in proc.stderr + proc.stdout


def test_a_build_or_an_override_never_pulls_the_default(tmp_path):
    _stub(tmp_path, "podman", _RECORDING_ENGINE)
    _out(tmp_path, "ENGINE_BIN=podman; ensure_umrs_image", STANDALONE_BUILD="1")
    assert not (tmp_path / "bin" / "calls").exists()
    _out(tmp_path, "ENGINE_BIN=podman; ensure_umrs_image", UMRS_IMAGE="example.org/umrs:dev")
    assert (tmp_path / "bin" / "calls").read_text().splitlines() == [
        "image inspect example.org/umrs:dev",
        "pull example.org/umrs:dev",
    ]


@pytest.mark.parametrize("build, warned", [("", False), ("1", True)])
def test_the_build_disk_floor_applies_only_when_building(tmp_path, build, warned):
    code = "free_disk_gb() { echo 20; }; MIN_DISK_GB=15; MIN_BUILD_DISK_GB=30; preflight_disk"
    proc = _bash(tmp_path, code, STANDALONE_BUILD=build)
    assert proc.returncode == 0, proc.stderr
    assert ("BUILD needs" in proc.stdout + proc.stderr) is warned
