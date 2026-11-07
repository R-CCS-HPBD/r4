# Sandbox profile map — tree surfaces → ADR-061's five profiles (D-13)

**Status:** ACTIVE (2026-09-23) · **Contract:** [ADR-061](adr/061-mini-rivault-sandbox-profiles.md)
(five named profiles — binding; nothing grows a sixth without a new ADR)

This is the implementation wave ADR-061 named: the one vocabulary. Every
sandbox surface in the tree maps onto exactly one of the five profiles;
tenant tiers, compose profiles and venue classes are **scoping** of
these, never new sandbox classes.

## The map

| ADR-061 profile | Tree surface(s) | Scoping knobs (not new classes) |
|---|---|---|
| **`gpu-job`** | Cluster compute lane: workflow-run jobs and user sbatch scripts via podman-compose under Slurm — the `slurm-remote` factory (RemoteSlurmBackend + serve-model.slurm wrapper), `slurm_podman_backend`, and the `HPC_SCRIPT` durable kind (D-37). | Site `slurm_*` config (partition/GPU sizing); per-job allowlist = ADR-015 ingest class only. |
| **`cpu-agent`** | The frontend venue worker sandbox (ADR-046 A2): rootless-Podman worker pod + egress net; `web_policy ∈ {public, allowlist, none}` is the ADR-046 D-4 egress class set. Session root is the only mount; provider sources ride the sealed store (never keys on the sandbox). The `cpu:<host>` venue grammar (declared-host gate landed) hosts this same profile on a named CPU host — the hosting lane is the open 3.2 leg. | Owner lease + `extra.venues.cpu_hosts` declaration; the D-51 data-class gate (venue start) already binds project scoping here. |
| **`model-server`** | The mini-RiVault endpoint containers, in two serving flavors via **compose profiles**: `vllm` (LLM/VLM serving — offline at boot, `assert-offline.sh`, inference listeners only) and `vision-bentoml` (the BentoML vision service lane); `harness` is the shared control/monitor sidecar set carried by both. Tenant **tiers** (`system`/`project`/`user` — provision route + `--tier`) are ownership scoping **of this one profile**, not distinct sandboxes. | Egress: fully offline at boot; only model downloads + guarded `KNOWLEDGE_INGEST` have egress. Model weights read-only. |
| **`embedding-job`** | **First occupant landed repo-side 2026-09-24 (D-12 move leg):** `KNOWLEDGE_EMBED`'s model compute (tokenize → chunk → embed) runs in a throwaway rootless-Podman container (`umrs/knowledge/embed_sandbox.py` → `python -m umrs.knowledge.embed_worker`): `--network=none`, read-only rootfs, `no-new-privileges`, all caps dropped, the pre-staged model mounted read-only with HF offline, JSON over stdin/stdout — texts in, chunks + vectors out. The server keeps every DB/S3 read and write (destination stores only); no credential crosses. Site opt-in `UMRS_EMBEDDING_SANDBOX=podman` (+ `UMRS_EMBEDDING_SANDBOX_IMAGE`, `…_MODEL_HOST_DIR`); unset = the in-process lane; a configured-but-failing sandbox fails its batch by name, never a silent in-process fallback. Job refs name the `lane`. `KNOWLEDGE_REINDEX` re-embeds through `KNOWLEDGE_EMBED` (so it rides the same sandbox); `INDEX_BUILD` is pgvector index DDL with no model compute — it stays a server-side DB maintenance job (a sandbox would only need DB credentials, the opposite of the profile). | Site activation = owner leg (podman reachable from the server container; image + model host path declared). |
| **`mcp-trusted`** | The MCP runtime lane: default runtimes installed under `/opt/umrs/mcp-runtimes` (privileged Ansible cadence) + hatch-installed server instances; the registry's `trust_class` vocabulary (D-19/D-24: `local-safe`/`front-end`/`trusted-host-only`) maps onto the profile's per-server declared allowlist rule (manifest `hatch.json` capabilities). The actual sandboxing of these instances (the D-22 job kind's start executor) is the open leg — **gate corrected 2026-09-23**: the per-server manifest format EXISTS (S8 hatch.json capabilities; `umrs.mcp.manifest` validates it server-side, `3d894f94`); the gate is the mcp-trusted sandbox pod itself (owner infra). | Registry placement + trust_class (0105); call-time enforcement seam recorded (D-19 row). |

## Scoping rules (the one-vocabulary corollary)

- **Tenant tiers** (system/project/user): ownership scoping of `model-server` (admin gate at provision: `api/endpoints.py:488`).
- **Compose profiles** (`harness`/`vllm`/`vision-bentoml`): serving flavors inside `model-server` — `rivault_launcher._resolve_profiles_for_model` picks the minimal set per model type.
- **Venue classes** (`frontend`, `cluster:<name>`, `cpu:<host>`): hosting locations — `frontend` hosts `cpu-agent`; `cluster:<name>` hosts `gpu-job` driver lanes; `cpu:<host>` (grammar landed) will host `cpu-agent` on the named host.
- **Egress classes** (ADR-046 D-4 `public`/`allowlist`/`none`): the `cpu-agent` profile's posture selector — never a separate profile.
- **Front-host workflow steps** (ADR-074 D-3, 2026-09-25): uploaded step code runs in the `gpu-job` profile hosted on the `frontend` venue (CPU-only there) — a hosting location, not a sixth profile. Not built yet (ADR-074 rollout).

*Note (2026-10-30, [ADR-101](adr/101-local-tool-sandbox.md), gap row D-65):* the **local tool sandbox** is not on this map, on purpose. It is not a mini-RiVault and not a sixth profile: it is client-side (R4, not UMRS, is its parent), it runs on the scientist's workstation (venue `local`), and its backends are bubblewrap and Seatbelt, not Podman. It wraps the processes a local session starts (shell, Python, code tools, language servers, local stdio MCP servers, hatch runtimes), opt-in and off by default. It reuses the same three web words as the `cpu-agent` posture selector (`none`/`allowlist`/`public`, the session's ADR-099 web access, squid `dstdomain` matching through `web_access.host_allowed`), but locally they are called web access, never an egress class. Locally pulled `local-safe` MCP servers run under it when it is on; the trust classes are unchanged (D-24).

## Open legs (named, not hidden)

1. `embedding-job` move (D-12): **all embedding compute rides the sandbox (opt-in, 2026-09-24)** — KNOWLEDGE_EMBED directly, KNOWLEDGE_REINDEX via KNOWLEDGE_EMBED; INDEX_BUILD is DB DDL, not an occupant. Remaining: the site activation (owner).
2. `mcp-trusted` sandboxing as the D-22 job kind (lifecycle + accounting).
3. `cpu:<host>` hosting lane for `cpu-agent` (3.2).
4. ~~MCP trust classes → profiles at call time~~ **D-24 + D-19 call-time landed** (policy + declaration + invoke gate); remaining: the mcp-trusted pod (owner infra) → start executor.
