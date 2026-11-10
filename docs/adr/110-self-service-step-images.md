# ADR-110: Self-service step images: a user registers their own step image, the server checks it, the lane runs it, with no admin step

**Status:** Accepted (2026-11-04).
- **Maintainer direction.** The maintainer said a normal user should be able to enable a workflow's image. On the gap analysis they answered "yes and go for your recommendations", and added: "any users custom models e.g. hmae … should be running in a self-service image".
- **Recommendation adopted.** Self-service images open on a site only after its step lane runs under a cluster identity that is not the operator's (D-8).
- **Agent's part.** The agent drafted this ADR and chose the details under the standing delegation, marked *(agent decision)*.

**Date:** 2026-11-04
**Relates to:**
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md): this ADR replaces D-7's "admin-curated" with D-1 here. It closes the residual risk in D-7 for any site that opens user images (D-8).
- [ADR-079](079-researcher-self-service-data-path.md): the upload path, personal buckets, and the same "no role gate" reasoning.
- [ADR-080](080-one-access-guard-shareable-resources.md): images become a shareable family.
- [ADR-069](069-identity-mapping-per-target.md): per-user cluster identity, one way to satisfy D-8.
- [ADR-109](109-vision-service-and-routed-endpoints-for-steps.md): service models (SAM3.1) are not user images.
- Wave ledger (186).

## Context

Verified 2026-11-04 at parent `f954dede2`, umrs pin `f14aaf34`. Code lines are from umrs `f63dba59`/`ed8c41d7` (the newest refs available locally); the pin's newer commits do not touch these files.

**Live receipts (rccs).**
- The video demo workflow (`wf-<id>`, project `example-project-a`) was refused: "routing_hints.image 'docker-archive:<workdir>/images/<step-worker>-0.1.0.tar' is not an allowed step image on this site".
- That path does not exist on rccs.
- The `example-project-b` workflow runs because an admin allow-listed its image (ADR-074 rollout 2026-10-02, later) after a project tool built it on the login node.

**Policy today.** ADR-074 D-7 (L106-109): "A step image is either the platform step image or one on an admin-curated allow-list". It gives no reason for "admin-curated". The list is `step_executor.allowed_images` in site config, exported as `UMRS_STEP_ALLOWED_IMAGES` (`step_trust.py:115-122`). Changing it takes a umrs commit and a converge.

**What already holds for any image** (ADR-074 Amendment 2, R5 receipt):
- `--network=none --read-only --cap-drop=all --security-opt no-new-privileges --userns=keep-id`, non-root;
- only the step's staged files mounted;
- four env names, no credentials, JSON replies with a size cap.

Uploaded Python already runs arbitrary code inside that pod. A user image therefore adds no new code execution. It adds new paths **around** the pod.

**The paths around the pod.**
- **(a) The seccomp profile comes from the step image.** It is produced by running the step image's own Python (`slurm_step_executor.py:707-735`). A user image could write a permissive profile.
- **(b) `podman load` of the tar runs outside any container,** as the service account, into the account's shared per-node graphroot (`slurm_step_executor.py:637-690`, `mini-rivault-llm/venue.sh:50-72`).
- **(c) Integrity rests on a sidecar file.**
  - Allow-list entries are `name:tag`, and digests are refused (`slurm_step_executor.py:336-351`).
  - The `.id` sidecar that pins the image sits next to the tar, in a directory the cluster account writes.
  - The server records no digest.
- **(d) The image's ENTRYPOINT, Env and VOLUME apply.** The pod runs `python -m umrs.server.step_worker` without `--entrypoint` (`:536-544`).
- **(e) One service account.** Every step runs as `<operator-account>` through one SSH identity (`remote_slurm_step_executor.py:173-178`), under one workdir root (`<workdir>/umrs_steps`). This is ADR-074 D-7's recorded residual risk: any escape reaches every user's step workdirs, the account's SSH keys and its serving secrets.
- **(f) No user path exists at all.**
  - There is no image upload or registry API, no `r4` image command, and no helper verb for images.
  - The archive dir is admin-only.
  - login2 has no podman (ADR-074 rollout 2026-10-28).
  - Non-platform archives are never pruned.
  - `docker-archive:` references are not understood.
- **(g) The user guide describes removed behaviour.** `docs/guides/custom-workflows.md` still describes Dockerfile auto-build on upload, which D-7 removed.

**The adopters waiting.**
- **`example-project-b`.** Allow-listed today. Its image is a COPY-only layer on the platform image (a detection model and torch cu130, several GB).
- **A vision model (`example-project-c`).** A few hundred MB of weights plus a config file.
- **The video demo pipeline (`example-project-a`).** A chain of detection, pose and video-embedding models.
  - Its models are several GB.
  - Its input videos are large and live on external storage outside the cluster; some files are several GB each.
  - The maintainer names rccs as its HPC.

## Decision

**D-1. Policy.**
- A step image is one of:
  - the platform step image;
  - an admin allow-listed site image;
  - an image a verified user registered under this ADR. It is usable by that user's workflows and by whoever its access list admits.
- A registered image needs no admin step.
- ADR-074 D-7 "admin-curated" is replaced by this list. D-7's other rules stand: no server-side builds, nodes never pull.

**D-2. What a user image is.**
- An OCI image for `linux/arm64`, built by the user on their own machine.
- Its base is a platform step image: `FROM localhost/umrs-step:<sha12>`, which `r4 image base` pulls from the server as a tar. Its layers are added on top; it is the recipe of today's allow-listed `example-project-b` image.
- Contract:
  - label `org.umrs.step-contract=1` (inherited);
  - the platform's `umrs.server.step_worker` is unchanged.
- **Model weights go in the image or in the user's bucket.**
  - Small weights go in the image (HMAE, a few hundred MB).
  - Large or changing weights go in a personal bucket and are staged in as step inputs (the video demo, several GB).

**D-3. Registration.**
- **Upload.** `r4 image push <tar> --name <n> --tag <t>` streams the archive through UMRS (the ADR-079 path) into the owner's image store. The server records a `step_images` row: owner, name, tag, image config digest, layer diff ids, base image id, size, status, access.
- **Names.** Tags never move: a re-push of the same name and tag with a different digest is refused, and the user picks a new tag.
- **Caps** *(agent decision; site-overridable)*:
  - 20 GiB per image;
  - 5 images or 60 GiB per user;
  - images unused for 60 days are collected unless pinned (`r4 image pin`).
- **Commands.** `r4 image ls|show|rm|share|pin`.

**D-4. Server-side checks before the cluster sees an image.** The server parses the archive without loading it:
- a single image whose architecture is `arm64`;
- its first layers equal, by diff id, a platform step image the site still holds (the current one and the previous N=3). This is what "FROM the platform image" means, checked rather than trusted.
- the added layers carry:
  - no device nodes and no setuid/setgid files;
  - no path escapes (`..`, absolute links leaving the root);
  - no secrets by the platform scan patterns (`env.*`, `*.env`, `s3cfg`, `id_*`, `*.pem`; rccs step-lane spec §9).
- **Outcome.** Status `verified` or `rejected` with the reasons named. Only `verified` images can be staged.

**D-5. Staging and integrity.**
- **Where it goes.** A server-driven helper verb `stage-image` copies a verified archive to `<image_archive_dir>/users/<owner>/<name>-<tag>-aarch64.tar`.
- **Pinned by the server.** The step payload carries the image config digest from the server's row. The job script compares the loaded image id with the payload value, not with a sidecar file. A mismatch fails the step by name.
- **Where it loads.** User images load into a per-owner graphroot on the node, `/var/tmp/podman_<account>/users/<owner>`. A user image can never shadow or replace the platform image in the account's main store.

**D-6. The pod's posture does not depend on the image.** These apply to every non-platform image, including today's allow-listed ones:
- **Seccomp from trusted code.** The seccomp profile is generated by the platform step image, or shipped with the helper, never by the step image.
- **Fixed command.** The pod sets `--entrypoint` to the step worker explicitly.
- **Image config ignored.** It runs with `--unsetenv-all` plus the step env names, and ignores the image's VOLUME and WORKDIR.
- **Validated archives only.** `podman load` reads only a D-4-verified archive whose digest the server recorded.

**D-7. References and sharing.**
- **Reference form.** A workflow names a user image as `routing_hints.image: "user:<name>:<tag>"`.
- **Resolution is owner-scoped.** The workflow owner's image comes first, then an image whose ADR-080 access list admits the triggering user (`user:`, `project:`, `circle:`). There is no cross-owner fallback by name.
- **Docker-style references** (`docker-archive:`, registry names) are refused with a pointer to `r4 image push`.
- **Existing allow-listed names keep working.**

**D-8. The identity gate.**
- **When user images open.** A site accepts user images (site flag `step_executor.user_images: on`) only when its step lane does not run as the operator's account. Either way is enough:
  - a dedicated unprivileged cluster account for steps, which owns only `umrs_steps` and the image store and has no serving secrets, SSH keys or other users' data;
  - per-user cluster identity on the step lane (ADR-069 `per_user_ssh`), where each user's steps run as that user.
- **Until then.** `user:` images are refused by name: "user step images need a dedicated step account on this site (ADR-110 D-8)".
- **What the gate is for.** It turns an escape from "every user's data and the operator's keys" into "the step account's own workdirs".
- **Who acts.** Creating the account at RCCS is a maintainer action.

**D-9. First adopters.**
- **`example-project-b`.** Moves from the allow-list to a `user:` image owned by the project's maintainer and shared `project:example-project-b`. The allow-list entry is removed after one receipt.
- **The vision model.** A user image (platform base + model weights + adapter) for the demo vision workflows, shared `project:example-project-c` (ADR-109 D-6).
- **The video demo.** A user image with its pipeline models (aarch64 builds), shared `project:example-project-a`.
  - The weights go into an `example-project-a` bucket.
  - The videos are staged to rccs as a subset first. The transfer route from the external storage to rccs is a rollout check, D-10.
  - The steps read videos as staged inputs: no `work_root` on a shared filesystem, because the pod mounts only its staged files.

**D-10. The staged-input cap.**
- **Today.** The per-object stage-in cap is 8 GiB (rccs step-lane spec §5). Some of the video demo's files are about that size, so some may exceed it.
- **The rule.** The site may raise the cap for a project's buckets: `step_executor.stage_in_object_cap` per project, owner or admin set. The cap stays on by default.
- **The video demo.** The workflow should process one video per step, not a whole batch of videos per step.

### Why

- ADR-079 removed role gates on uploads because "they protected nothing ADR-074 does not already contain". The same is true of the image once (a)–(d) are closed: the image adds files to a pod that already runs arbitrary user code. D-4–D-6 close (a)–(d) on the server and in trusted code, so the admin step no longer adds safety.
- (e) is different. Every new artifact path that touches the cluster as the operator widens what an escape reaches. D-8 makes closing (e) the precondition, instead of accepting it again.
- Basing every user image on a platform image keeps the step contract (staged I/O, JSON replies) the same as for uploaded code, and lets D-4 check "built on our base" by diff ids instead of trusting a label.

## Consequences

**Positive.**
- Researchers ship their own models (HMAE and the demo projects' models) without an admin.
- The allow-list shrinks to site images.
- Integrity is pinned by the server.
- The seccomp and entrypoint fixes (D-6) also harden today's allow-listed images.

**Cost / negative.**
- New server surface: image upload, a parser and validator, a store with quotas, a helper verb.
- Users must build arm64 images on their own machine (or under emulation).
- Platform base upgrades leave old user images valid only while their base is among the last N=3 platform images. After that they must be rebuilt (`r4 image show` names the base and whether it is still held).
- D-8 blocks the whole feature on a maintainer action at RCCS.

**Not in scope.**
- A server-side or sandboxed builder (D-7's "may return later").
- x86_64 images: every lane node today is aarch64.
- Service models (ADR-109).
- Pulling from public registries.

## Alternatives Considered

- **Keep the allow-list and let project owners add to it.** Rejected: it is still a site-wide name list with no digest, no owner and no checks. It moves the admin step without making it safe.
- **Accept the one-account residual risk and open images now.** Rejected as the default (the maintainer took the gated recommendation): an image path is exactly where (a)/(b)-style escapes would come from.
- **Any base image (not only platform bases).** Rejected for now: the step worker and staged I/O must match the server's version. A free base would push that compatibility check onto the user. It can be revisited with a versioned worker contract.
- **A registry (OCI distribution) instead of tar upload.** Deferred: the ADR-079 streamed upload already exists, and nodes never pull either way.

## Rollout-status (2026-11-04)

Nothing landed yet. The slices, each its own wave:

| Slice | Decision | Content | Live leg |
|---|---|---|---|
| I0 | D-8 | **Maintainer:** request a dedicated step account at RCCS (or decide per-user identity); the agent then moves `ssh_user`, `workdir_root` and the image store to it | lane receipt under the new account |
| I1 | D-6 | trusted seccomp, `--entrypoint`, `--unsetenv-all`, per-owner graphroot; applies to the allow-listed images too | `example-project-b` receipt unchanged |
| I2 | D-3, D-4 | `step_images` table + upload API + validator + `r4 image push/ls/show/rm/share/pin/base` | a push is verified or rejected with reasons |
| I3 | D-5, D-7 | helper `stage-image`, server-pinned digest in the step payload, `user:` references, ADR-080 family `step_image` | a user image used by an `example-project-c` run |
| I4 | D-9, D-10 | `example-project-b` moves to a `user:` image; video demo image and bucket weights; a video subset staged to rccs; per-project stage-in cap | video demo receipt in `example-project-a` |
| I5 | — | rewrite `docs/guides/custom-workflows.md` for D-1–D-7 | — |

**Owner legs:**
- I0 (the account);
- the external storage → rccs transfer route for the video demo's inputs (who copies, with what);
- converge dispatches.

## Rollout-status (2026-11-05)

- **I1 (D-6): landed repo-side** (umrs `28ab35bb`, ledger (197)); not deployed.
  - Seccomp is written by the platform step image (an allow-listed step image also loads the platform image for this), and the lane refuses by name without one.
  - `--entrypoint` is explicit.
  - `--unsetenv-all`, with fixed platform env values plus the four step names.
  - `--image-volume=ignore`, `--workdir=/tmp`.
  - The podman 4.9.3 node support for these flags is checked by the first `example-project-b` receipt after deploy.
- **I0 (maintainer: the step account), I2–I5:** open.

## Rollout-status (2026-11-06)

- **I1 (D-6): live on rccs and PASS.** Deployed at main `7843d392a` (umrs `684ce031`, step image `umrs-step:684ce0311403`).
  - Receipt: run `wf-<id>`, project `example-project-b`, its allow-listed project image, ng-dgx-m3 podman 4.9.3, Slurm 458964 → 459071.
  - The output matches the pre-I1 run of the same workflow: cuda, the same results, no failures.
  - So the platform-image seccomp writer, `--entrypoint`, `--unsetenv-all` with the fixed env, `--image-volume=ignore` and `--workdir=/tmp` all work for a non-platform image on the nodes.
- **I0** (maintainer: a dedicated step account) is still open; it gates I2–I5.

## Rollout-status (2026-11-08): D-8's per-user identity is ADR-112 `user_account`

- **D-8, second bullet.** "Per-user cluster identity on the step lane" now means [ADR-112](112-user-hpc-accounts-gated-delegation.md) `user_account`: the step runs in the user's own HPC account, through the gate, on intents the user signed. It no longer means [ADR-069](069-identity-mapping-per-target.md) `per_user_ssh`, which ADR-112 retires. The first bullet (a dedicated step account) is unchanged.
- **Wave A does not meet D-8.** Wave A of the gated-delegation plan declares each site's access mode and limits user work on a site whose platform login belongs to a person to admins and listed projects. Steps still run as `<operator-account>`, so `user:` images stay refused.
- **I0 stays open** until a gated lane receipt (ADR-112 wave B2) or a dedicated step account. It still gates I2–I5.

## Rollout-status (2026-11-08, later): the gated lane (ADR-112 wave B2) in progress

- **What B2 builds.** On a site that offers ADR-112 `user_account`, a staged step of a person with an active account and an active project link runs under that person's own HPC login and allocation, through the gate in their HPC home, on an intent they signed. The gated lane has no payload pickle: the gate writes the step input as JSON from signed fields (ADR-112 D-6, [ADR-074](074-uploaded-step-code-runs-sandboxed.md) rollout-status of the same date). The gate composes the batch script itself, pins each image by id, and checks the archive's digest and labels; D-6 of this ADR (trusted seccomp, a fixed entrypoint, image env ignored) is kept in the gate's template.
- **D-8, second bullet: met per site, after a lane receipt.** A site meets it for the users whose steps run on the gated lane once a gated lane receipt has passed there (one step run end to end under a user's own login through the gate). Until that receipt, nothing changes on that site. Steps of users without an account and link at that site keep running under the site's platform login, so `user:` images stay refused for them.
- **I0 stays open** until the first gated lane receipt, or a dedicated step account. It still gates I2–I5. B2 is in progress on `wave/gated-b2-intents`, not merged and not deployed; it needs a step-image rebuild and a staged converge before the receipt (maintainer legs).

## Related

- [ADR-069](069-identity-mapping-per-target.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-079](079-researcher-self-service-data-path.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-109](109-vision-service-and-routed-endpoints-for-steps.md)
- `docs/development/rccs-step-lane-spec-2026-09-26.md` (§5 caps, §7 pod, §9 image contract)

*(2026-11-06: internal addresses, paths and group ids replaced with placeholders for the public export (repo-rename plan P5b-2); the values live in r4-ops.)*

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
