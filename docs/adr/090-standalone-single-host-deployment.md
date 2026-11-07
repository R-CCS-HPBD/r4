# ADR-090: Stand-alone deployment — the whole platform on one host, as a site and not a second product line

**Status:** Proposed (2026-10-08; maintainer request 2026-10-07, decisions D-2, D-3, D-6, D-7, D-8 answered by the maintainer 2026-10-07/08 and recorded verbatim below — acceptance is the maintainer's)
**Date:** 2026-10-08
**Relates to:** new gap row D-62 (stand-alone deployment mode), D-63 (model-backend selection seam), D-64 (local execution venue parity). Advances D-07, D-09, D-12, D-16, D-25, D-37, D-47, D-50, D-51. Binds against ADR-004 (execution authority), ADR-008 (site-config env overlay), ADR-055 (SeaweedFS), ADR-059 (dual transport + UMRS edge), ADR-062 (model locality), ADR-068 (one internal inference API), ADR-070 (Hatch/Hatchling names), ADR-074 (uploaded step code), ADR-075 (venue is the user's choice), ADR-083 (embedding spaces). Plan: standalone-deployment-plan-2026-10-08.md.

## Context

Today a researcher using R4 needs a UMRS deployment on an institution's front-end: `umrs-server`, Postgres + pgvector, SeaweedFS and the NGINX + auth-sidecar edge all live on a remote host, reached over HTTPS and ZeroMQ. The maintainer asked for a **stand-alone** mode in which all of that runs on the scientist's own machine under podman or docker, with *every function working the same way as the remote deployment*.

Four facts, verified in the tree on 2026-10-08, decide the shape of the answer.

1. **The execution seam already exists.** `compose` is already one of the legal values of `SchedulerConfig.kind` (`umrs/src/umrs/orchestrator/site_config.py:65`), and `DockerComposeBackend` is already registered under both `docker-compose` and `compose` (`umrs/src/umrs/orchestrator/factory.py:45-49`). Site profiles already ship (`orchestrator/site_profiles/{compose-dev,lab-podman,k8s-cluster,rccs-cloud}.yaml`). A stand-alone deployment is therefore a **site**, not a new execution authority.

2. **The client already points at loopback by default.** `r4`'s built-in defaults are `gateway_url=http://localhost:8080`, `umrs_server_host=127.0.0.1`, `umrs_control_port=5555` (`r4/src/r4/config.py:345,363,369,471`), and `_normalize_gateway_url` (:173-187) deliberately keeps localhost on `http://` while upgrading everything else to `https://`. No client code change is needed to target a local stack.

3. **A near-complete one-host graph already exists and does not boot.** `docker-compose.e2e.yaml` already composes pgvector, SeaweedFS, `umrs` with its HTTP sidecar, the NGINX + auth-sidecar edge, MCP services and mock compute. But its `a2l` service builds `a2l/Dockerfile`, deleted 2026-09-11 by `cb861661`; `a2l/` now holds only `src/`, and `gateway-nginx` declares `depends_on: a2l: service_healthy`. Nothing in CI starts it — `.github/workflows/compose-e2e.yaml` is label-gated and runs pytest without bringing the stack up. The file has therefore been unbootable for roughly a month without detection.

4. **"Everything local" is not the same as "offline".** There is no artifact staging of any kind in the tree: no Python wheelhouse, no npm/bun cache, no model-weight acquisition tooling, no GGUF handling. `deploy/airgap_bundle.py` names images this stack does not use and its `--verify` cannot pass (`ImageRef.digest` defaults to `""` with floating tags). The UMRS server image cannot be built without network and **fails silently when it tries** (`umrs/docker/Dockerfile.server:37`, `python -m spacy download en_core_web_sm || true`).

A contributor who did not know (1) and (2) would reasonably add a fourth `DeployMode` member, a `PodmanComposeBackend` beside the existing one, and a second set of deployment scripts — three violations of CONSTITUTION §1.3/§1.4/§6 for no gain. A contributor who did not know (4) would promise air-gapped operation in the quickstart.

## Decision

**D-1 Stand-alone is a site, expressed only in configuration.** No file in `r4/`, `umrs/src/` or `gateway/` learns the word "standalone" as a behavioural branch. The deployment is one `site.yaml` (`scheduler.kind: compose`, `step_executor.kind: podman`), one ADR-008 env overlay set, one compose graph with profiles, and one `EndpointRegistry` row for the model. There is **no fourth `DeployMode`**: the existing `HYBRID` default and `R4_DEPLOY_MODE=hpc-local` already express the two useful shapes.

**D-2 One mode, two profiles, differing by configuration only.** The **hardened** profile is the default; **dev** is opt-in and announces itself. The difference is expressed only in compose profiles, the env overlay and a `site.dev.yaml` that `extends:` the base — never as `if dev:` in product code. Every relaxation the dev profile uses must be an env knob that already exists and already has a secure default (`GATEWAY_ENVIRONMENT`, `GATEWAY_ALLOW_INSECURE_DEV_AUTH`, `UMRS_AUTH_MODE`, `UMRS_ZMQ_AUTH_TYPE`); hardened simply never sets them. A validator enforces that the hardened overlay contains none of them.

**D-3 The model backend is one seam with three adoptions; provisioning is not funded.** All three backends — (a) a local **ollama** container with small models (maintainer decision 2026-10-08), (b) external hosted API, (c) remote institutional endpoint — become a `RegisteredEndpoint` row created by `POST /umrs/endpoints` followed by `PUT /umrs/endpoints/{job_id}/status` with `status=ready` (both calls are required: `register` creates the row as `PROVISIONING` and every consumer filters `[READY, DEGRADED]`). `ENDPOINT_PROVISION` / `r4 endpoint up` / `r4 pool` stay **absent** in stand-alone, and the `gpu_count >= 1` floor (`orchestrator/serving_spec.py:373`, `deployment_spec.py:141`) is **not** relaxed. Backend (a) is registered on the platform lane, never as an `r4 source add-ollama` (`kind=public`), for two reasons: the client refuses **every** public route for a `data_class=sensitive` project whatever its provider (`r4/src/r4/client.py:1033-1042`, `if route.kind == "public"`), so a model on the scientist's own machine would be refused; and a public source is client-only, so the UMRS-side consumers that resolve registry rows (RAG, perception, vision) would never see it. *(Corrected 2026-10-08: this sentence first cited `PUBLIC_PROVIDER_KINDS`, which governs only pushed sealed sources — an add-ollama source can never be pushed.)* **"One seam" is not yet true as shipped:** the agentic research loop, server-side companion turns and LLM KG extraction call `UMRS_AGENT_LITELLM_*` directly, and OCR (`knowledge/ocr.py`) posts to its own `KNOWLEDGE_OCR_BASE_URL`; each must be brought onto the registry row or declared an exception (gap D-63). Backend (b) requires the project's `data_class` to be `non_sensitive` — the existing owner-only ADR-062 act, with no second receipt.

**D-4 The chat backend and the embedder are separate knobs.** The embedder defaults to local-CPU under all three backends, so switching chat backend never touches embedding-space identity and never invalidates a catalog. Changing the embedder is a separate governed act: refused by name when vectors already exist, and otherwise an all-or-nothing reindex. *(Corrected 2026-10-08: as first written this keyed the refusal on `embedding_model_versions` (migration `0046`) and the switch on `JobType.KNOWLEDGE_REINDEX`. Neither works today — no production code writes `embedding_model_versions` (only tests call `register_version`), and `KNOWLEDGE_REINDEX` fails creating an HNSW index on the dimension-less `0075` column and never writes the versions table. The refusal must key on something that is written, and the reindex needs repair before an embedder switch can be offered. Until then the **first** embedder chosen is effectively permanent, which is why the default is a maintainer decision.)*

**D-5 MCP is Hatch/Hatchling, identical to the remote deployment.** No substitute mechanism and no second packaging path. Stand-alone repoints `HATCH_REGISTRY_URL` (`r4/src/r4/integrations/hatchling.py:55-58`) at the in-repo `mcp-servers/hatch-registry/`, which is a complete local registry — verified by execution with networking disabled. Two registration defects must be fixed **in the same change**, because fixing only the first is worse than fixing neither: the SSRF guard refuses `127.0.0.0/8` (`umrs/src/umrs/api/mcp.py:39-48`) which is exactly the URL Hatch registers, and the Hatch runtime hardcodes and advertises `http://127.0.0.1:{port}` (`hatchling.py:718`) which names the UMRS container itself once UMRS is on a bridge network. Applying the allowlist alone makes registration succeed, tools appear, and every call fail.

**D-6 The audience is one scientist on their own machine.** Not a shared lab workstation. The NGINX + auth-sidecar edge nonetheless **stays** — CONSTITUTION §1.5 reserves its removal to the maintainer, and `OPERATION_GATEWAY_ONLY` (`r4/src/r4/config.py:56-60`) makes chat and every MCP verb unreachable without it. Circles, quotas and the ADR-080 access guard stay enabled but need no multi-principal test matter.

**D-7 Host support.** Linux with rootless podman is first-class. **macOS keeps the podman step lane**: the identical-path fix was measured to work inside `podman machine` on 2026-10-08 (darwin 25.5.0, podman 5.7.1 client / 5.8.7 in-VM) — today's `tempfile.mkdtemp` path fails `rc=125 statfs … no such file or directory`, and a workdir under a VM-local identical-path mount returns `rc=0` with write-back visible. **WSL2 is the Windows path**; native Windows is refused by name, because `_BINARY_SUFFIXES` (`r4/src/r4/_cli/tui.py:43-48`) has no win32 key so `platform_key()` returns `None` and the console binary cannot be fetched.

**D-8 The compose graph is promoted, not forked.** `deploy/standalone/compose.yaml` becomes the canonical one-host graph, created **additively** first so the repair is provable before anything is retired; the mock leg (`slurm-mock`, `compute-mock`) survives as a `ci` profile of the same file so the stand-alone stack and the CI stack cannot drift. `docker-compose.e2e.yaml` is retired, and its consumers repointed, only after the new file is proven to boot.

**D-9 Offline is a later milestone than "local".** Stand-alone ships in three stages: **(i) local with internet** — every service on one host, images and weights pulled on demand; **(ii) behind a proxy**; **(iii) air-gapped**. Only (i) is in scope for the first wave set. The quickstart, the parity matrix and `tools/runtime_support_matrix.json` must each say which stage they describe, because silence reads as a promise.

### Why

The seams in (1) and (2) mean the honest cost of stand-alone is configuration plus a repair, not a port — and the CONSTITUTION's prohibitions on a second job store, scheduler or storage-credential path (§1.3, §1.4, §6) make riding them mandatory rather than merely cheap. D-3 avoids relaxing a GPU floor shared with the Slurm serving path for a benefit — self-provisioning — that a single-user box with one model slot does not need. D-9 is the decision most likely to be reversed by optimism: "runs on my laptop" and "runs with the network unplugged" are separated by a staging problem nobody has started.

## Consequences

**Positive.** One service graph for CI and for the product, so drift is structurally impossible. The same client binary, API paths, auth code paths and job lifecycle as remote. A `.kb` bundle remains the sanctioned laptop↔cluster bridge. The repair in D-8 fixes a stack that has been silently broken for a month.

**Cost.** Features that need a cluster, several GPUs, an IdP or egress are absent, and must be declared in the parity matrix rather than degraded silently: OIDC, multi-node/multi-GPU serving, `ENDPOINT_PROVISION`, fine-tuning, HF model staging, the `r4 schedule` connectors, remote-source ingestion, the three RIKEN vendor MCP adapters. A single local model slot sets a real concurrency ceiling for multi-agent teams. The isolation boundary on a rootless single-host box is weaker than the production front-end — `venues/egress_nft.py` states in its own docstring that rootless netavark filtering is not userland-addressable — and must be stated, not implied.

**NOT in scope.** A `PodmanComposeBackend` (D-3). Removing the edge (D-6). Air-gapped operation in the first wave set (D-9). Reconciling the four disagreeing venue vocabularies in the tree. The six defects in defect-wave-2026-10-08.md, which affect the remote deployment and are tracked separately.

## Alternatives Considered

- **A fourth `DeployMode` (`standalone`)** — rejected: `HYBRID` plus `hpc-local` already express it, and a mode member would become a behavioural branch in client code, which D-1 forbids.
- **A `PodmanComposeBackend` beside `DockerComposeBackend`** — rejected: a second scheduler under CONSTITUTION §1.4/§6, and unnecessary because registry adoption gives full parity on the consumption path.
- **A fresh `deploy/standalone/` tree authored from scratch** — rejected: two graphs would drift, which is already observable between `docker-compose.e2e.yaml` and `demos/tiers/environments/compose/demo-stack.yml`.
- **Dropping the edge for a single user (`hpc-minimal-zmq`)** — rejected under D-6: it removes chat and MCP, and CONSTITUTION §1.5 reserves the decision to the maintainer.
- **Declaring macOS step-less** — rejected on measurement (D-7); the identical-path mount works inside `podman machine`.
- **Promising air-gapped operation now** — rejected (D-9): no staging tooling exists, and the server image fails silently when it tries to build offline.

## Rollout-status (2026-10-08)

Nothing landed. This ADR is Proposed; the plan's WS-00 is this document, and WS-01 (build smoke + additive repaired compose) is deliberately ungated on it because it is a repair rather than an architecture change.

Open sub-decisions carried into the plan's §7 and not settled here: the matrix envelope name and support level (§7.1, §7.10), the client-side `s3cfg` suppression (§7.3), the reference chat model and its ADR-087 budget defaults (§7.5), the macOS accelerator trade (§7.6), the minimum host floor (§7.9), funding of the ADR-091 local step lane (§7.11 is settled; the step lane is §7's separate item), and disposition of the stale local-deployment prior art (§7.12).

## Related

- [ADR-004](004-execution-authority-invariant.md), [ADR-008](008-site-config-env-overlay.md), [ADR-059](059-transport-and-edge-dual-transport-umrs-edge.md), [ADR-062](062-model-locality-data-class.md), [ADR-068](068-one-internal-inference-api.md), [ADR-070](070-hatch-hatchling-names-bind-to-r4-runtime.md), [ADR-075](075-one-work-engine-venue-is-user-choice.md), [ADR-083](083-embedding-spaces.md)
- Plan: standalone-deployment-plan-2026-10-08.md
- Defects found while planning: defect-wave-2026-10-08.md
- .github/instructions/deployment-operations.instructions.md

## Rollout-status (2026-10-08, second block — model verification)

A verification of the ollama / small-model / config-surface design, adversarially judged, overturned 24 claims; those touching this ADR are corrected in place above and marked *(Corrected 2026-10-08)*: D-3's stated mechanism, D-3's "one seam" (three consumers and OCR bypass the registry), and D-4's governance mechanism (nothing writes `embedding_model_versions`).

Landed in `deploy/standalone/` the same day, each verified by running it:
- **Security:** `umrs:8082` is no longer published in the hardened profile — the sidecar's routers trust `X-Verified-*` headers without an HMAC check (`api/llm.py:683` → `verified_identity(verify_hmac=False)`), so a published port let any local process act as any project. Dev only now. Postgres no longer runs `umrs/docker/init_pgvector.sql`, which creates a `LOGIN CREATEDB` role `ejeannot` with password `ejeannot`.
- **Correctness:** `JWT_SECRET` on umrs (it fell back to an insecure default and rejected every sidecar-issued JWT); `UMRS_HANDSHAKE_HMAC_SECRET` on the sidecar (it signed no `X-Verified-HMAC`); `UMRS_ENDPOINT_REGISTRY_SHARED=1` + `UMRS_JOBS_DB` (the registry was in-memory, so an adopted model row would vanish on restart); `OLLAMA_CONTEXT_LENGTH=32768` (ollama serves 4096 by default while the companion's tool set alone is ~10.5k tokens) and `OLLAMA_NUM_PARALLEL=1` (two slots at 32k exceed the 8g limit).
- **Driver:** the `model_is_staged` awk program was a syntax error, so `up` with the model profile always refused; `standalone.sh` sourced `base.env`, clobbering operator-exported values; `volume exists` is podman-only; the Python `podman-compose` slipped through when it was the provider behind `podman compose`.
- **Removed dead config:** `site.yaml extra.model_backend` (no code reads `extra` for it) and `UMRS_RAG_EMBEDDING_MODEL` on the umrs service (only the separate `rag_service` process reads it, and this graph runs none).

Still open: the ollama `EndpointRegistry` adoption step itself (nothing performs it yet), the default embedder (irreversible once the first vector exists, see D-4), and the rccs-wk test run. Spec, runbook and the full list of overturned claims: standalone-model-spec-rccs-wk-runbook-2026-10-08.md.

## Rollout-status (2026-10-09)

Merged to `main` (merge commit `202163df`) together with the umrs change: `ai4s-platform` fast-forwarded to `6c6d9210` on `UMRS-org/umrs` and the `mnagaso/umrs` mirror, so `main` pins a SHA on the integration branch. Status otherwise unchanged from the 2026-10-08 blocks: Proposed, and the stack does not boot yet.

One consequence for cluster builds, stated plainly rather than as "byte-identical": with `TORCH_INDEX_URL` empty the new `RUN` step does nothing, so the resulting image is functionally identical — but it is a new layer, which invalidates the build cache from that point. The **first** cluster build of `Dockerfile.server` after this merge therefore re-runs the large `pip install` layer, and because that layer's packages are unpinned it re-resolves them to whatever versions are current.

## Rollout-status (2026-10-09, third block — the embedder is bundled)

Maintainer decision 2026-10-09: "include a very small text embedding model in umrs … downloaded in
default on umrs-server". D-4's local-CPU default is now **in the umrs-server image**:
`intfloat/e5-base-v2` at `/opt/umrs/models/e5-base-v2`, revision pinned, staged at build time by
`umrs/src/umrs/knowledge/text_embedder.py` (`--build-arg UMRS_BUNDLE_TEXT_EMBEDDER=0` skips it).
`bootstrap` no longer pulls weights; `embedder pull <repo> <dir>` stages another model for
`UMRS_KNOWLEDGE_EMBED_MODEL`. The RAG query encoder follows the same setting. D-4's "separate
knob" is unchanged. Image size 2.37 → 2.83 GB (the model plus pypdf/python-pptx for PDF and PPTX
text).

## Rollout-status (2026-10-09, second block — M0)

The stack boots and `r4 chat` works end to end on macOS (plan §3g): all five core services Healthy; the chat model adopted as an `EndpointRegistry` row by `up` (D-3, as specified: POST then PUT ready, `max_model_len` in metadata) and served by the host's native ollama; the row survives restarts (shared registry). The embedder is `e5-base-v2` (D-4). Every fix was in `deploy/standalone/` — no product code changed — and eight more boot-chain defects were found by running it (plan §3g.1). Still open from this ADR: D-3's three server-side bypasses, D-4's reindex path, the `hosted`/`remote` backends, and Linux/GPU.

## Rollout-status (2026-10-09, fourth block — SL-CONF T0; the profile values that never arrived)

- **D-2's validator does not exist.** `tools/standalone_profile_validator.py` (plan WS-05) was
  never written, although `profile.hardened.env` cited it as the gate.
  `tests/test_standalone_deployment_truth.py` (SL-CONF T0, plan §3c.4) now pins:
  - the forbidden hardened knobs;
  - that uploaded code is enabled nowhere outside the dev overlay;
  - the loopback-only host-access contract;
  - the bundled embedder;
  - bootstrap's secrets;
  - §3c.5's two bring-up defects.

  It is a test, not a registered validator. The validator stays WS-05's, under CONSTITUTION §5.2
  review.
- **Every value a profile sets must reach a container or the script.** `--env-file` only
  interpolates `${...}`, so a value not mapped in compose is decorative. T0 found:
  - the dev step settings (now delivered);
  - `UMRS_STEP_EXECUTOR` in both profiles (now mapped in `compose.yaml`);
  - hardened's `UMRS_EMBEDDING_SANDBOX=podman`, which **never reached a container**, so
    hardened has always embedded in the UMRS process;
  - `UMRS_STEP_TRUST_LANE=refuse`, a name no code reads (refusal comes from
    `UMRS_STEP_UPLOADED_CODE` being unset).

  The two hardened lines are now comments that state what happens. Mapping the sandbox knob
  needs the podman socket in the umrs container (WS-08); without it every embed fails, by design.
  STATE `DEPLOY-20261009-001`.

## Rollout-status (2026-10-12) — what ADR-093 changed on the stand-alone stack

- **D-4's same-model reindex is repaired**, and since wave F it converges:
  - one keyset-paged pass;
  - held artifacts skipped;
  - a per-project lock and a fresh key per pass;
  - permanently failing artifacts recorded and skipped (`metadata.embed_failed`).
- **New verbs:** `standalone.sh migrate`, `reranker pull` and `orphan-repair`. `doctor` now gives an `OLLAMA_NUM_PARALLEL` hint (ADR-093 MD-11).
- **Fixes found while measuring:**
  - Postgres runs under an init;
  - BLAS threads are pinned to each container's CPU quota;
  - KNOWLEDGE_EMBED runs off the event loop;
  - `UMRS_KNOWLEDGE_LLM_EXTRACT` is mapped;
  - KNOWLEDGE_EXPORT runs on an initialized pool and accepts text timestamps;
  - a plain export job is owned by its caller;
  - a bundle carries every artifact.
- **Bundle signing:** `bootstrap` now generates `UMRS_SECRET_KEY` and adds it to an existing secrets file. Before 2026-10-12 no stand-alone export could be signed.
- **The profile defaults** that ADR-093 Phase 9 settled: γ 0.015, lexical inclusion at the site default, `fixed-v0` chunks (`sent-v1` opt-in, eval Phase 14).
