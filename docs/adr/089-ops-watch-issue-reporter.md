# ADR-089: Ops Watch — report-only service watcher that files GitHub issues

**Status:** Accepted (2026-10-08; maintainer request, design and open choices decided by the agent under delegation)
**Date:** 2026-10-08
**Relates to:** ADR-088 (Security Watch: same "reports, never acts" rule; security signals stay there), ADR-076 (decision model: evidence, never permission), ADR-062 (in-site models only), ADR-019 amendment (host checkouts read-only), R-05 (`converge-umrs-front.yaml`: runner, SSH route, one-issue-per-failure pattern); gap row D-52 (observability); dgx-spark-fleet-plan-2026-10-08.md

## Context

Maintainer, 2026-10-08: "I also want a watcher to keep our services working,
debugged and improving. The models runnable on DGX Spark are small, so they
are not enough for modifying the code. Just watching and reporting bugs,
errors and problems to GitHub issues would be good."

Verified on 2026-10-08 (read-only):
- Prometheus on umrs-front (`127.0.0.1:50190`) loads 7 groups / 23 alert rules (`umrs/deploy/umrs-front/observability/prometheus/alerts.yaml`), all `health=ok`, over 10 healthy targets (four UMRS servers, Loki, SeaweedFS, …). There is **no Alertmanager**: a firing alert reaches nobody.
- **The log pipeline has been dead since about 2026-06-05.** Promtail tails `umrs/deploy/umrs-front/logs/*.log`. Those files were last written on 2026-06-05, when the servers moved into podman containers that log to **journald** (`podman info` → `journald`). Loki holds no current lines (24 h query: 0 streams).
- Container logs are readable with `podman logs --since` over the SSH alias. This is the read-only diagnostics path that `deployment-operations.instructions.md` allows.
- A 24 h read on 2026-10-08 found three real signals:
  - `psycopg2.OperationalError: server closed the connection unexpectedly` on rccs (9 events: all 11 `Exception in ASGI application` traces end in it, i.e. HTTP 500s) and on bdrt (12 events, model-store lookups);
  - `janitor: Orphan cleanup STILL failing (backend=slurm-remote, streak=704)` on ai4s (the known AI4S reachability gap).
- The nightly converge check already shows the pattern this needs. It runs on the self-hosted runner that reaches umrs-front, and opens or updates one issue with the workflow's own `GITHUB_TOKEN` (`issues: write`). No GitHub credential lives on a host.

## Decision

**D-1 Report only.** Ops Watch reads, and writes nothing but GitHub issues, comments and labels. It does not:
- change code, configuration, services or data;
- restart anything;
- open pull requests.

It touches only issues that carry the `ops-watch` label **and** its fingerprint marker. Fixes come from people, or from a coding agent a person assigns to an issue; small in-site models do not write code.

**D-2 Placement.** A scheduled GitHub workflow (`.github/workflows/ops-watch.yaml`, hourly, a 70 min window) runs on the converge runner. Its sources:
- Prometheus `/api/v1/alerts` through an SSH tunnel to the loopback port;
- `podman logs --since` of the `umrs-*` containers over the SSH alias, plus `umrs-*` containers that exited non-zero.

Its only credential is `GITHUB_TOKEN`. Logic lives in `tools/ops_watch.py` (repo tooling, not a package), with tests in `tests/test_ops_watch.py`. Loki stays an optional source (`--loki`) until the journald pipeline is fixed (D-7).

**D-3 Deterministic floor.**
- **What counts:** a log line counts by its level. CRITICAL is high; ERROR, Go `level=error` and bare exception lines are medium; WARNING containing "failed" is low; INFO and DEBUG never count.
- **Tracebacks:** a traceback is one event. Its frames are the excerpt, and its final exception line is the signature, so different exceptions stay different issues and one bug stays one issue.
- **Fingerprint:** sha256 of source, service and the normalized signature (ids, numbers, timestamps and quoted values removed).
- **Always reported:** firing alerts, CRITICAL lines, crashed containers and unreadable sources. The watch never goes silent.

**D-4 Small models are advisory and fail open.** When the owner configures a key, two optional stages run:
- **Triage.** The decision model (Nimble) scores each log signature: is it actionable, and what kind? It can suppress a medium log finding below P = 0.2, and a low one below 0.5. It never suppresses alerts, CRITICAL lines, crashed containers or source failures. A model error means "no triage", not silence.
- **Notes.** A small in-site chat model may add a ≤ 5-line note to *new* issues, inside a collapsed "unverified" section. The excerpt is quoted as untrusted data (ADR-039), and the watch takes no action from model text.

Both stages call in-site models through the edge only (ADR-062), with a non-admin service key on a sensitive-class project.

**D-5 Issue hygiene.**
- Labels: `ops-watch`, `sev:<high|medium|low>`, `venue:<rccs|ai4s|bdrt|octopus|front>`.
- One open issue per fingerprint. Each sighting refreshes the body footer (last seen, total count); a recurrence comment is posted at most once per 24 h.
- At most 5 new issues per run; the rest wait for the next run.
- An issue is closed with a comment after 72 h without a sighting; a recurrence opens a new one.
- Everything bound for GitHub is redacted (JWTs, keys, tokens, `key=value` secrets, URL credentials, presigned query strings, e-mail and IP addresses) and truncated (12 lines × 300 chars). The repository is private.

**D-6 Split with ADR-088.** Audit events, authentication failures and ZeroMQ session events are skipped here (`DEFAULT_IGNORE`); they belong to the Security Watch, whose incidents carry ids only.

**D-7 The dead log pipeline is a fix, not a workaround.** Promtail must read journald, with the container name as the `service` label, so Grafana and Loki see current logs again. That is an Ansible change to the observability role and needs a converge to verify (owner leg). Ops Watch then gains `--loki` as a second source, and the podman path stays as the fallback.

### Why

- Report-only keeps the watcher harmless when it is wrong: the worst case is a noisy issue, never a changed system. The maintainer asked for exactly this.
- A GitHub-side watcher needs no new credential on any host. It reuses a proven runner and SSH route, and the hourly cadence costs nothing on the front.
- Deterministic detection first: small models are good at "is this noise?" and poor at debugging, so they are used only where a wrong answer costs one missed or one extra issue.

## Consequences

- **Positive:**
  - The 23 Prometheus alerts get their first receiver.
  - Server errors become tracked issues with counts and excerpts.
  - Crashed containers and unreadable sources surface within an hour.
  - The dead Loki pipeline is now on record.
- **Cost:**
  - One SSH call and one tunnel per hour from the runner.
  - Issue triage work for the maintainer.
  - Rules (`DEFAULT_IGNORE`, the level mapping) need tuning as noise shows up.
- **NOT in scope:**
  - Fixing code.
  - Restarting services.
  - Alertmanager.
  - Security detection (ADR-088).
  - Watching the HPC login nodes or compute jobs directly (their failures reach the UMRS server logs and the job store).

## Alternatives Considered

- **Alertmanager → GitHub webhook.** It covers alerts only, not log errors, and needs a receiver credential on the host.
- **A watcher process inside UMRS.** It would need a GitHub credential on the front host. It also could not report the UMRS server's own death.
- **Let a small model read raw logs and decide everything.** It is noisy and injection-prone, and the maintainer noted these models cannot fix code. Using them only for triage and notes keeps their failure cheap.

## Rollout-status (2026-10-08)

| Item | Status |
|---|---|
| `tools/ops_watch.py` + `tests/test_ops_watch.py` | landed in this wave (37 tests; ruff, lizard ≤ 9 and actionlint clean) |
| `.github/workflows/ops-watch.yaml` (hourly; dispatch defaults to dry-run) | landed; first scheduled run after the push |
| Live dry-run against umrs-front | Prometheus via tunnel OK (0 firing, 23 rules healthy); `podman logs` read 25 containers; 3 real findings (above) |
| Owner: model stages | create a non-admin R4 service key → `secrets.OPS_WATCH_R4_API_KEY`; set `vars.OPS_WATCH_GATEWAY_URL`; optionally `vars.OPS_WATCH_SUMMARY_MODEL` once Qwen3.8-27B-NVFP4 serves |
| Owner: first run | `gh workflow run ops-watch.yaml -f dry_run=true`, read the plan, then let the schedule run |
| D-7 Promtail → journald | **landed repo-side 2026-10-08, flag off** (`umrs_front_promtail_journal`). Promtail gets a journald job with `service` = container name; the compose adds `group_add: [keep-groups]` (the deploy user's wheel group, which the journal ACL admits), `security_opt: [label=disable]` (SELinux enforcing; `:z` on host-owned journal files is not allowed) and read-only mounts of `/run/log/journal` and `/etc/machine-id`. Probed on data0 with transient `--rm` containers: denied without keep-groups or without label=disable; with both, `promtail --dry-run` read 12+ container streams (`umrs-server-*`, gateways, postgres, loki). With the flag off, Ansible's rendering is byte-identical to before. **2026-10-08 later:** the first apply with the flag (37736129159) failed at `compose up`: the Docker-compat API looks `keep-groups` up as a literal group name. The services were unaffected; only Promtail was left `Created`. Replaced by the crun annotation `run.oci.keep_original_groups: "1"` (compose `annotations:`); transient compose probes on data0: READ-OK with it, DENIED without. **LIVE 2026-10-08:** converge 37738404975 (`f8feae3f`, ok=339 failed=0). Loki serves current streams for every `umrs-*` container (`{service="umrs-server-rccs"}`); the default is flipped to `true`, and the journal-priority `level` label is dropped (podman tags stderr `err`, and Python logs INFO to stderr). `keep-groups` through the docker-compose compat path is the one unproven step; if `compose up` refuses it, leave the flag off and report. Then check Grafana Explore `{service="umrs-server-rccs"}`. Retention note: the journal is volatile (about 4.5 h on host); Loki keeps what Promtail ships. |

## Related

- [ADR-088](088-umrs-security-watch.md), [ADR-076](076-decision-model-role.md), [ADR-062](062-model-locality-data-class.md), [ADR-039](039-untrusted-content-provenance-taint.md)
- `.github/workflows/converge-umrs-front.yaml` (runner and issue pattern), `.github/instructions/deployment-operations.instructions.md` (read-only diagnostics)
