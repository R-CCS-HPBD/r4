"""Deployment wiring of the knowledge ingest v2 knobs and models (ADR-116 D-9).

Every umrs server of the umrs-front compose reads UMRS_KNOWLEDGE_{DOC_PARSER,
DOCLING_ARTIFACTS,GLINER_MODEL,DOCMODELS_URL,EXTRACT_MODEL,EXTRACT_AUTO_MODE}
from its own UMRS_<FRONT>_KNOWLEDGE_* .env key; the converge writes only the
overrides the inventory names; the stand-alone driver stages optional model
copies with the same module the fronts use.

Since the 2026-11-13 amendment (maintainer request: Docling and GLiNER on by
default) the defaults are on: the server image bundles the weights at build
time, one hardened umrs-docmodels service per host keeps them loaded for
every server, and the servers start without it.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from umrs.knowledge import ingest_models

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "umrs/deploy/umrs-front/docker-compose.yaml"
START = REPO_ROOT / "umrs/deploy/umrs-front/start.sh"
TASKS = REPO_ROOT / "deploy/ansible/roles/front_end/tasks/umrs-front.yaml"
GROUP_VARS = REPO_ROOT / "deploy/ansible/inventory/umrs-front/group_vars/umrs_front.yaml"
STANDALONE = REPO_ROOT / "deploy/standalone/standalone.sh"
STANDALONE_COMPOSE = REPO_ROOT / "deploy/standalone/compose.yaml"
DOCKERFILE = REPO_ROOT / "umrs/docker/Dockerfile.server"
SERVER_IMAGE = REPO_ROOT / "umrs/docker/server-image"
SMOKE_SCRIPT = REPO_ROOT / "scripts/deps/image-smoke.sh"
SMOKE_NEEDED = REPO_ROOT / "scripts/deps/image-smoke-needed.sh"
STANDALONE_IMAGE_FLOW = REPO_ROOT / ".github/workflows/standalone-image.yaml"
FRONTS = ("rccs", "ai4s", "bdrt", "octopus")
DOCMODELS = "umrs-docmodels"
# The servers run with network_mode: host, so they reach the service on the
# loopback port it publishes; stand-alone servers share its internal network.
FRONT_DOCMODELS_URL = "http://127.0.0.1:8091"
STANDALONE_DOCMODELS_URL = "http://umrs-docmodels:8091"
# Server env name suffix -> its default (Docling and GLiNER on, bundled).
DEFAULTS = {
    "DOC_PARSER": "docling",
    "DOCLING_ARTIFACTS": "",
    "GLINER_MODEL": "",
    "DOCMODELS_URL": FRONT_DOCMODELS_URL,
    "DOCMODELS_LOCAL_FALLBACK": "sandbox",
    "EXTRACT_MODEL": "",
    "EXTRACT_AUTO_MODE": "lite",
}
BUNDLED_DOCLING = "/opt/umrs/models/docling"
BUNDLED_GLINER = "/opt/umrs/models/gliner_medium-v2.1"
SHA = re.compile(r"[0-9a-f]{40}")


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _environment(front: str) -> dict:
    return _services()[f"umrs-server-{front}"]["environment"]


def test_every_front_server_reads_its_own_knob_with_the_default_on():
    for front in FRONTS:
        env = _environment(front)
        for name, default in DEFAULTS.items():
            expected = f"${{UMRS_{front.upper()}_KNOWLEDGE_{name}:-{default}}}"
            assert env[f"UMRS_KNOWLEDGE_{name}"] == expected, (front, name)


def test_the_standalone_server_maps_every_knob_with_the_default_on():
    """An env file only interpolates ``${...}``: a knob the compose does not
    map never reaches the stand-alone server, so an override could not be set."""
    env = yaml.safe_load(STANDALONE_COMPOSE.read_text())["services"]["umrs"]["environment"]
    for name, default in {**DEFAULTS, "DOCMODELS_URL": STANDALONE_DOCMODELS_URL}.items():
        expected = f"${{UMRS_KNOWLEDGE_{name}:-{default}}}"
        assert env[f"UMRS_KNOWLEDGE_{name}"] == expected, name


def test_the_model_mount_the_knobs_point_at_is_present():
    services = _services()
    for front in FRONTS:
        assert "./models:/models:ro,z" in services[f"umrs-server-{front}"]["volumes"]


# ── the umrs-docmodels service on the fronts ────────────────────────────────


def test_every_front_server_reaches_the_port_docmodels_publishes():
    services = _services()
    (port,) = services[DOCMODELS]["ports"]
    host_ip, host_port, container_port = re.fullmatch(
        r"(127\.0\.0\.1):\$\{UMRS_DOCMODELS_HOST_PORT:-(\d+)\}:(\d+)", port
    ).groups()
    assert f"http://{host_ip}:{host_port}" == FRONT_DOCMODELS_URL
    assert services[DOCMODELS]["environment"]["UMRS_DOCMODELS_PORT"] == container_port
    for front in FRONTS:
        assert services[f"umrs-server-{front}"]["network_mode"] == "host", front


def test_no_front_server_waits_for_docmodels():
    """The servers must start while the service loads, crashes or is off."""
    for name, service in _services().items():
        assert DOCMODELS not in (service.get("depends_on") or {}), name


def _assert_hardened(service: dict) -> None:
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert any(opt.startswith("no-new-privileges") for opt in service["security_opt"])
    assert any(str(t).startswith("/tmp:") for t in service["tmpfs"])
    assert "env_file" not in service and "network_mode" not in service
    _assert_unprivileged(service)
    _assert_offline_model_service(service)


def _assert_unprivileged(service: dict) -> None:
    """Not root: the parsers read untrusted bytes, and under rootful Docker
    (stand-alone) container root is host root. /tmp stays writable for it."""
    assert service["user"] == "65534:65534"
    assert any("mode=1777" in str(t) for t in service["tmpfs"])


def _assert_offline_model_service(service: dict) -> None:
    assert service["command"] == ["python", "-m", "umrs.knowledge.docmodels_server"]
    env = service["environment"]
    assert (env["HF_HUB_OFFLINE"], env["TRANSFORMERS_OFFLINE"]) == ("1", "1")
    # Liveness, not readiness: a service whose models never load must not
    # fail `up --wait` (stand-alone) nor be restarted in a loop.
    assert "/livez" in " ".join(service["healthcheck"]["test"])
    assert service["mem_limit"] and service["cpus"] and service["pids_limit"]


def test_front_docmodels_is_hardened_and_internal():
    compose = yaml.safe_load(COMPOSE.read_text())
    service = compose["services"][DOCMODELS]
    _assert_hardened(service)
    assert service["image"] == compose["services"]["umrs-server-rccs"]["image"]
    assert all(str(p).startswith("127.0.0.1:") for p in service["ports"])
    (network,) = service["networks"]
    assert compose["networks"][network] == {"internal": True}
    assert service["volumes"] == ["./models:/models:ro,z"]


def test_front_docmodels_limits_are_converge_written_with_a_hardened_default():
    service = _services()[DOCMODELS]
    assert service["mem_limit"] == "${UMRS_DOCMODELS_MEM_LIMIT:-6g}"
    assert service["cpus"] == "${UMRS_DOCMODELS_CPUS:-4}"


# ── the umrs-docmodels service in the stand-alone stack ─────────────────────


def _members(services: dict, network: str) -> set[str]:
    return {n for n, s in services.items() if network in (s.get("networks") or [])}


def test_standalone_docmodels_shares_an_internal_network_with_umrs_only():
    compose = yaml.safe_load(STANDALONE_COMPOSE.read_text())
    service = compose["services"][DOCMODELS]
    _assert_hardened(service)
    assert "ports" not in service
    (network,) = service["networks"]
    assert compose["networks"][network]["internal"] is True
    assert _members(compose["services"], network) == {"umrs", DOCMODELS}
    assert service["image"] == compose["services"]["umrs"]["image"]
    assert DOCMODELS not in compose["services"]["umrs"]["depends_on"]


def test_standalone_docmodels_reads_the_same_model_overrides_as_umrs():
    services = yaml.safe_load(STANDALONE_COMPOSE.read_text())["services"]
    for name in ("DOCLING_ARTIFACTS", "GLINER_MODEL"):
        key = f"UMRS_KNOWLEDGE_{name}"
        assert services[DOCMODELS]["environment"][key] == services["umrs"]["environment"][key]
    assert services[DOCMODELS]["volumes"] == ["umrs_models:/models:ro"]


# ── start.sh and the converge ───────────────────────────────────────────────


def test_start_recreates_docmodels_with_the_servers_and_never_fails_on_it():
    script = START.read_text()
    up = script.index("up -d umrs-docmodels")
    assert script.index("podman rm -f umrs-docmodels") < up
    assert up < script.index("up -d \\\n    umrs-server-rccs")
    assert '"${UMRS_FRONT_ENABLE_DOCMODELS:-true}" == "true"' in script
    wait = script[script.index("# ─── umrs-docmodels readiness") :]
    wait = wait[: wait.index("# ─── Verify servers started")]
    assert "/readyz" in wait and not re.search(r"\bexit\b(?!ed)", wait)


def _task(prefix: str) -> dict:
    tasks = yaml.safe_load(TASKS.read_text())
    return next(t for t in tasks if t.get("name", "").startswith(prefix))


def test_the_converge_writes_only_known_keys_and_nothing_by_default():
    assert yaml.safe_load(GROUP_VARS.read_text())["umrs_front_knowledge_ingest_env"] == {}
    check = _task("Knowledge ingest v2 — only known fronts and keys")
    allowed = re.search(
        r"item\.name in \[([^\]]*)\]", " ".join(check["ansible.builtin.assert"]["that"])
    )
    assert allowed, "the assert names no key list"
    assert sorted(re.findall(r"'([A-Z_]+)'", allowed.group(1))) == sorted(DEFAULTS)
    write = _task("Knowledge ingest v2 — write")
    assert write["ansible.builtin.lineinfile"]["line"] == "{{ item.key }}={{ item.value }}"
    lines = write["vars"]["umrs_front_knowledge_ingest_env_lines"]
    assert "'UMRS_' ~ (front | upper) ~ '_KNOWLEDGE_' ~ (name | upper)" in lines


def test_the_converge_caps_docmodels_only_where_the_host_has_the_controller():
    """A memory or cpu limit without its delegated cgroup controller fails the
    rootless container create (2026-09-25: "controller cpu is not available")."""
    group = yaml.safe_load(GROUP_VARS.read_text())
    assert group["umrs_front_docmodels_enabled"] is True
    assert (group["umrs_front_docmodels_mem_limit"], group["umrs_front_docmodels_cpus"]) == (
        "6g",
        "4",
    )
    write = _task("Knowledge ingest models — write the umrs-docmodels lines")
    lines = {item["regexp"]: item["line"] for item in write["loop"]}
    assert "umrs_front_docmodels_enabled" in lines["^UMRS_FRONT_ENABLE_DOCMODELS="]
    assert "'memory' in _controllers else '0'" in lines["^UMRS_DOCMODELS_MEM_LIMIT="]
    assert "'cpu' in _controllers else '0'" in lines["^UMRS_DOCMODELS_CPUS="]
    assert write["register"] == "umrs_front_docmodels_env"
    restart = _task("Run start.sh --server")
    assert "umrs_front_docmodels_env.changed" in " ".join(restart["when"])


def test_the_converge_reports_docmodels_readiness_without_failing():
    probe = _task("Knowledge ingest models — umrs-docmodels readiness")
    assert "/readyz" in probe["ansible.builtin.command"]["cmd"]
    assert probe["failed_when"] is False


def test_the_front_check_does_not_follow_the_runs_cluster_scope():
    """A clusters=rccs converge (umrs_front_clusters=['rccs']) aborted on an
    ai4s override in the inventory."""
    check = _task("Knowledge ingest v2 — only known fronts and keys")
    that = " ".join(check["ansible.builtin.assert"]["that"])
    assert "umrs_front_clusters" not in that
    fronts = re.search(r"item\.front in \[([^\]]*)\]", that)
    assert fronts and sorted(re.findall(r"'([a-z0-9]+)'", fronts.group(1))) == sorted(FRONTS)


def test_the_converge_names_the_host_directory_behind_models():
    """The ingest sandbox is created by the host podman: it must mount the
    host path of /models/docling, not the server's path."""
    write = _task("Knowledge ingest v2 — the host directory")
    line = write["ansible.builtin.lineinfile"]["line"]
    assert line.startswith("UMRS_KNOWLEDGE_MODELS_HOST_DIR=")
    assert line.endswith("/umrs/deploy/umrs-front/models")


# ── the image: bundled weights, pinned revisions ────────────────────────────


def _stages(text: str) -> dict[str, str]:
    """Dockerfile stage name (or index) -> its instructions, continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    stages: dict[str, str] = {}
    name = ""
    for line in joined.splitlines():
        match = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?", line)
        if match:
            name = match.group(1) or f"stage{len(stages)}"
            stages[name] = ""
        elif name:
            stages[name] += line + "\n"
    return stages


def test_the_pinned_revisions_are_full_commit_shas():
    assert SHA.fullmatch(ingest_models.GLINER_REVISION)
    assert SHA.fullmatch(ingest_models.GLINER_BACKBONE_REVISION)
    assert ingest_models.DOCLING_REVISIONS
    assert all(SHA.fullmatch(sha) for sha in ingest_models.DOCLING_REVISIONS.values())
    assert ingest_models.DEFAULT_GLINER_REPO == "urchade/gliner_medium-v2.1"


def test_the_image_stages_both_models_in_a_stage_of_their_own():
    stages = _stages(DOCKERFILE.read_text())
    stage = stages["ingest-models"]
    assert DOCKERFILE.read_text().count("FROM builder AS ingest-models") == 1
    assert "ARG UMRS_BUNDLE_INGEST_MODELS=1" in stage
    copies = re.findall(r"^COPY (\S+) ", stage, re.M)
    # Only the staging module: a runtime default edited in ingest_models.py
    # re-downloaded ~1.4 GB on every front rebuild.
    assert copies == [
        "./umrs/src/umrs/knowledge/ingest_models_stage.py",
        "./umrs/docker/server-image/NOTICE-ingest-models.txt",
    ]
    assert f"ingest_models_stage.py stage docling --dest {BUNDLED_DOCLING}" in stage
    assert re.search(rf"ingest_models_stage.py stage gliner .*--dest {BUNDLED_GLINER}", stage)
    assert re.search(
        rf"ingest_models_stage.py verify\s+--docling {BUNDLED_DOCLING} --gliner {BUNDLED_GLINER}",
        stage,
    )
    assert "rm -rf /tmp/hf" in stage
    # Readable by the service's uid 65534, made so in this stage (a chmod in
    # the runtime stage would copy the 1.4 GB layer again).
    assert "chmod -R a+rX /opt/umrs/models" in stage


def test_the_runtime_copies_the_bundle_before_the_source_and_keeps_e5():
    text = DOCKERFILE.read_text()
    runtime = list(_stages(text).values())[-1]
    copy = runtime.index("COPY --from=ingest-models /opt/umrs/models/ /opt/umrs/models/")
    assert runtime.index("ARG UMRS_BUNDLE_TEXT_EMBEDDER=1") < copy
    assert copy < runtime.index("COPY ./umrs/src /app/umrs-src")
    assert "ingest_models" not in runtime.split("COPY --from=ingest-models")[0]


def test_the_bundled_paths_match_the_runtime_contract():
    for name, path in (
        ("BUNDLED_DOCLING_DIR", BUNDLED_DOCLING),
        ("BUNDLED_GLINER_DIR", BUNDLED_GLINER),
    ):
        assert getattr(ingest_models, name, path) == path, name


def test_the_notice_names_the_pinned_revisions_and_every_licence():
    notice = (SERVER_IMAGE / "NOTICE-ingest-models.txt").read_text()
    assert ingest_models.GLINER_REVISION in notice
    assert ingest_models.GLINER_BACKBONE_REVISION in notice
    for repo, sha in ingest_models.DOCLING_REVISIONS.items():
        assert repo in notice and sha in notice, repo
    assert "(pinned" in notice[notice.index("docling-layout-heron") :]
    for licence in ("Apache-2.0", "MIT", "CDLA-Permissive-2.0"):
        assert licence in notice, licence


def test_the_ci_smoke_image_bundles_no_model_and_the_published_one_both():
    smoke = SMOKE_SCRIPT.read_text()
    assert "--build-arg UMRS_BUNDLE_INGEST_MODELS=0" in smoke
    assert "--build-arg UMRS_BUNDLE_TEXT_EMBEDDER=0" in smoke
    flow = yaml.safe_load(STANDALONE_IMAGE_FLOW.read_text())
    steps = [s for job in flow["jobs"].values() for s in job["steps"]]
    (build,) = [s for s in steps if s.get("name") == "Build and push"]
    assert "UMRS_BUNDLE_INGEST_MODELS=1" in build["with"]["build-args"]


def test_the_ci_image_build_follows_the_staging_module_only():
    """A settings edit in ingest_models.py started the CI image build."""
    needed = SMOKE_NEEDED.read_text()
    assert "src/umrs/knowledge/ingest_models_stage.py" in needed
    assert "src/umrs/knowledge/ingest_models.py" not in needed


# ── the image smoke test checks the bundle it was built with ────────────────


def _stage_fakes(root: Path) -> None:
    for sub in ingest_models.DOCLING_SUBDIRS:
        (root / "docling" / sub).mkdir(parents=True)
        (root / "docling" / sub / "model.safetensors").write_bytes(b"0")
    gliner = root / "gliner_medium-v2.1"
    (gliner / "backbone").mkdir(parents=True)
    for name in ("gliner_config.json", "tokenizer_config.json", "backbone/config.json"):
        (gliner / name).write_text(json.dumps({}))
    (gliner / "model.safetensors").write_bytes(b"0")


def test_the_smoke_checks_the_bundle_with_the_runtime_check():
    """smoke.py is not importable (ADR-053: no spec-loads in tests), so the
    check lives in ingest_models and the smoke only calls it."""
    smoke = (SERVER_IMAGE / "smoke.py").read_text()
    assert "return ingest_models.bundle_status(root)" in smoke
    assert 'BUNDLED_MODELS = "/opt/umrs/models"' in smoke
    assert ingest_models.BUNDLE_NOTICE == "NOTICE-ingest-models.txt"


def test_the_smoke_passes_an_image_built_without_the_bundle(tmp_path):
    assert ingest_models.bundle_status(str(tmp_path)).startswith("not bundled")


def test_the_smoke_fails_a_bundle_the_runtime_would_not_load(tmp_path):
    (tmp_path / "NOTICE-ingest-models.txt").write_text("x")
    with pytest.raises(ingest_models.StagingError, match="docling"):
        ingest_models.bundle_status(str(tmp_path))


def test_the_smoke_passes_a_complete_bundle(tmp_path):
    (tmp_path / "NOTICE-ingest-models.txt").write_text("x")
    _stage_fakes(tmp_path)
    assert ingest_models.bundle_status(str(tmp_path)) == "bundled"


# ── stand-alone staging (now optional overrides) ────────────────────────────


def test_standalone_staging_names_an_image_too_old_for_it():
    """The pinned stand-alone image predates ingest_models: `docling pull`
    died with a bare "pull failed" after a ModuleNotFoundError."""
    stage = _function(STANDALONE.read_text(), "_stage_ingest_model")
    check = stage.index('-c "import umrs.knowledge.ingest_models"')
    assert check < stage.index("-m umrs.knowledge.ingest_models stage")
    assert "predates ADR-116" in stage and "STANDALONE_BUILD=1" in stage


def _function(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.M | re.S)
    assert match, f"standalone.sh has no {name}()"
    return re.sub(r"\s*\\\n\s*", " ", match.group(1))


def test_standalone_stages_the_ingest_models_with_the_umrs_module():
    script = STANDALONE.read_text()
    stage = _function(script, "_stage_ingest_model")
    assert "-m umrs.knowledge.ingest_models stage" in stage
    assert '--dest "/models/${name}"' in stage
    assert "_stage_ingest_model docling" in _function(script, "cmd_docling")
    gliner = _function(script, "cmd_gliner")
    assert '--mount-path "/models/${name}"' in gliner
    assert 'GLINER_REPO="urchade/gliner_medium-v2.1"' in script
    assert re.search(r"^\s+docling\)\s+shift; cmd_docling", script, re.M)
    assert re.search(r"^\s+gliner\)\s+shift; cmd_gliner", script, re.M)


def test_standalone_pulls_are_described_as_optional_overrides():
    script = STANDALONE.read_text()
    for name in ("cmd_docling", "cmd_gliner"):
        body = _function(script, name)
        assert "Optional override" in body and "optional, the image bundles" in body, name


# ── review fixes (2026-11-13) ───────────────────────────────────────────────


def test_one_setting_caps_pages_and_bytes_on_both_sides():
    """The service read UMRS_DOCMODELS_DOCLING_MAX_PAGES, the servers
    UMRS_KNOWLEDGE_DOCLING_MAX_PAGES: raising one left the other at 80."""
    for compose_file, servers in (
        (COMPOSE, [f"umrs-server-{f}" for f in FRONTS]),
        (STANDALONE_COMPOSE, ["umrs"]),
    ):
        services = yaml.safe_load(compose_file.read_text())["services"]
        for name, default in (("MAX_PAGES", "80"), ("MAX_BYTES", "67108864")):
            key = f"UMRS_KNOWLEDGE_DOCLING_{name}"
            expected = f"${{{key}:-{default}}}"
            assert services[DOCMODELS]["environment"][key] == expected, (compose_file, key)
            for server in servers:
                assert services[server]["environment"][key] == expected, (server, key)
        assert "UMRS_DOCMODELS_MAX_BYTES" not in services[DOCMODELS]["environment"]


def test_the_front_servers_and_docmodels_share_one_token():
    services = _services()
    assert services[DOCMODELS]["environment"]["UMRS_DOCMODELS_TOKEN"] == "${UMRS_DOCMODELS_TOKEN:-}"
    for front in FRONTS:
        env = _environment(front)
        assert env["UMRS_KNOWLEDGE_DOCMODELS_TOKEN"] == "${UMRS_DOCMODELS_TOKEN:-}", front


def test_start_generates_the_token_before_the_service_starts():
    script = START.read_text()
    generate = script.index('if [[ -z "${UMRS_DOCMODELS_TOKEN:-}" ]]; then')
    assert (
        generate
        < script.index("export UMRS_DOCMODELS_TOKEN")
        < script.index("up -d umrs-docmodels")
    )
    block = script[generate : script.index("export UMRS_DOCMODELS_TOKEN")]
    assert "/dev/urandom" in block and '>> "${SCRIPT_DIR}/.env"' in block


def test_a_disabled_service_points_the_servers_at_off():
    """umrs_front_docmodels_enabled=false left every server calling a port
    nothing owned (and any local process could take it)."""
    script = START.read_text()
    enabled = script.index('if [[ "${UMRS_FRONT_ENABLE_DOCMODELS:-true}" == "true" ]]; then')
    disabled = script[script.index("\nelse\n", enabled) : script.index("\nfi\n", enabled)]
    assert "for _front in RCCS AI4S BDRT OCTOPUS" in disabled
    assert 'export "${_url_var}=off"' in disabled
    assert '[[ -n "${!_url_var:-}" ]] ||' in disabled  # an explicit URL wins
    for front in FRONTS:
        assert (
            f"UMRS_{front.upper()}_KNOWLEDGE_DOCMODELS_URL"
            in _environment(front)["UMRS_KNOWLEDGE_DOCMODELS_URL"]
        )


def test_both_docmodels_services_read_the_parser_switch():
    """UMRS_KNOWLEDGE_DOC_PARSER=pypdf never reached the service: Docling
    stayed loaded there."""
    front = _services()[DOCMODELS]["environment"]
    assert front["UMRS_KNOWLEDGE_DOC_PARSER"] == "${UMRS_DOCMODELS_DOC_PARSER:-docling}"
    standalone = yaml.safe_load(STANDALONE_COMPOSE.read_text())["services"]
    key = "UMRS_KNOWLEDGE_DOC_PARSER"
    assert standalone[DOCMODELS]["environment"][key] == standalone["umrs"]["environment"][key]


def test_a_knowledge_ingest_change_restarts_the_servers():
    """The settings were written into .env but no server restarted."""
    write = _task("Knowledge ingest v2 — write")
    assert write["register"] == "umrs_front_knowledge_ingest_env_written"
    host_dir = _task("Knowledge ingest v2 — the host directory")
    assert host_dir["register"] == "umrs_front_models_host_dir_env"
    restart = " ".join(_task("Run start.sh --server")["when"])
    assert "umrs_front_knowledge_ingest_env_written.changed" in restart
    assert "umrs_front_models_host_dir_env.changed" in restart


def test_a_rollback_removes_docmodels_before_the_older_start():
    """An older start.sh / stop.sh does not know umrs-docmodels: it kept
    running on the newer image after a rollback."""
    tasks = yaml.safe_load(TASKS.read_text())
    names = [t.get("name", "") for t in tasks]
    remove = names.index("Rollback — remove umrs-docmodels (an older start.sh does not manage it)")
    task = tasks[remove]
    assert "podman rm -f --ignore umrs-docmodels" in task["ansible.builtin.command"]["cmd"]
    assert task["when"] == "umrs_front_rollback_mode | bool"
    assert task["failed_when"] is False
    first_start = min(i for i, n in enumerate(names) if n.startswith("Run start.sh"))
    assert remove < first_start


def test_standalone_docmodels_is_a_profile_the_script_adds_by_default():
    """With no profile, `up --wait` failed the whole stack whenever the
    service could not become healthy, and nothing could leave it out."""
    service = yaml.safe_load(STANDALONE_COMPOSE.read_text())["services"][DOCMODELS]
    assert service["profiles"] == ["docmodels"]
    script = STANDALONE.read_text()
    body = _function(script, "docmodels_profile")
    assert '"$(cfg_get STANDALONE_DOCMODELS)" != 0' in body
    assert "PROFILE_ARGS+=(--profile docmodels)" in body
    assert "export UMRS_KNOWLEDGE_DOCMODELS_URL=off" in body
    assert "docmodels_profile" in _function(script, "profile_args")


def _run_docmodels_image_check(engine: str) -> str:
    """Run the real docmodels_image_check() under bash with stubbed helpers;
    ``engine`` stands in for the container engine (``true`` = the image has
    the module, ``false`` = it does not)."""
    import subprocess

    match = re.search(r"^docmodels_image_check\(\) \{\n.*?^\}", STANDALONE.read_text(), re.M | re.S)
    assert match, "standalone.sh has no docmodels_image_check()"
    script = "\n".join(
        [
            "set -u",
            "build_wanted() { return 1; }",
            "umrs_image_ref() { echo old-image; }",
            "cfg_get() { echo ''; }",
            "warn() { :; }",
            f"ENGINE_BIN={engine}",
            "PROFILE_ARGS=(--profile web --profile docmodels --profile models-local)",
            match.group(0),
            "docmodels_image_check",
            'echo "${PROFILE_ARGS[*]}|${UMRS_KNOWLEDGE_DOCMODELS_URL:-}"',
        ]
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_an_image_without_the_service_starts_the_stack_without_it():
    """The pinned published image predates umrs-docmodels: its container would
    exit at once and `up --wait` would fail the whole stack (2026-11-13)."""
    assert _run_docmodels_image_check("false") == "--profile web --profile models-local|off"
    assert _run_docmodels_image_check("true") == (
        "--profile web --profile docmodels --profile models-local|"
    )
    assert "docmodels_image_check" in _function(STANDALONE.read_text(), "cmd_up")
