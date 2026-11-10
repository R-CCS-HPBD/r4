# 20. User & Access Management

This chapter covers user enrollment, token management, Slurm account binding, and project membership.

> **New to user onboarding?** See [Chapter 0 — Account Creation & Onboarding](00-onboarding.md) for the step-by-step walkthrough that combines admin and user instructions in one place.

## Admin CLI

All administrative tasks use `umrs-server admin` on the UMRS host:

```bash
umrs-server admin --help
```

## User Lifecycle

### Add a User

```bash
# Basic user creation
umrs-server admin user add alice --project team-alpha

# Create user with immediate token
umrs-server admin user add alice --project team-alpha --issue-token

# Create with specific role
umrs-server admin user add alice --project team-alpha --role admin
```

### List Users

```bash
umrs-server admin user list
umrs-server admin user show alice
```

### Disable / Enable / Delete

```bash
umrs-server admin user disable alice
umrs-server admin user enable alice
umrs-server admin user delete alice
```

**Disabling ends what the account holds (2026-10-22, RS-0 S-2).**
`disable`, `delete` (soft, `--purge`, `--deprovision`) end the account's work on
this site, as one audited event (`audit_events.event = 'account.cascade'`):

- every lease the user holds on this site is revoked; the inference proxy and
  the venue rail refuse it at once;
- the user's remote sessions end at their next status check (about 5 s), through
  the revoked lease;
- the user's planned, queued and running jobs on this site are cancelled
  (durable cancel; a running Slurm job is not killed);
- the user's CURVE/ZMQ sessions end at the server's next keystore sweep (60 s);
- the user's tokens stop working at the gateway within 30 s
  (`GATEWAY_MEMBERSHIP_CACHE_TTL_SEC`), admin tokens included: every request
  gets 403 with the code `account_disabled`. This needs
  `GATEWAY_PROJECT_MEMBERSHIP=live` (the deploy default) or `enforce`; under
  `token` the gateway looks nothing up, and a token works until it expires.
  A disabled admin's JWT is also refused by the UMRS admin routes
  (`umrs-server admin endpoint|session`). `enable` restores both within the
  same 30 s. An account that signs in through the IdP is matched by its
  `oidc_subject`, and a disable holds while the `users` table is unreachable
  (the JSON record is read first). *(2026-10-23, ADR-097 Amendment D-8.)*

The command prints what it ended. It needs the site database (the same
`DATABASE_URL` / `DB_*` / `POSTGRES_*` environment the server uses, so run it
in the server container). Without one it says so, and the server's account
sweep (below) does the same within a minute.

The site's only enabled admin cannot be disabled or deleted: make another
account admin first (`set-role <name> admin`).

**Account sweep.** Every UMRS server re-checks, every
`UMRS_ACCOUNT_SWEEP_SEC` seconds (default 30, at most 60), the owners of the
live leases on its site: a disabled account gets the cascade above. Under
`UMRS_PROJECT_MEMBERSHIP=enforce` (set it together with the gateway's
`GATEWAY_PROJECT_MEMBERSHIP=enforce`), a lease in a project the owner is no
longer a member of gets the project-scoped cascade too; admin and service
accounts are never swept out of a project. `UMRS_ACCOUNT_SWEEP_ENABLED=false`
turns the sweep off.

### Set Roles

```bash
umrs-server admin user set-role alice admin
umrs-server admin user set-role alice user
```

## Token Management

### Generate Admin Token

For UMRS direct access (umrs):

```bash
umrs-server admin token generate
```

### Generate JWT

For Gateway/r4 authentication:

```bash
umrs-server admin token generate-jwt \
  --user alice \
  --project team-alpha \
  --days 30
```

Output shows `sub`, `project_id`, `roles`, `gateway` and `expires` — verify before distributing.

**Gateway URL in the token (2026-10-03).** Both `token generate-jwt` and
`user add --issue-token` embed the public gateway URL as the `gateway_url`
claim: `--gateway-url URL` when given, else `UMRS_PUBLIC_GATEWAY_URL`, which
your deployment sets in each site's server container (e.g.
`https://data0.ai.r-ccs.riken.jp/rccs`). Mint inside the site's container —
`podman exec <server-container> umrs-server admin token generate-jwt ...` — and
the user needs only `r4 auth login --api-key TOKEN`; the printed login line
says so. `--no-gateway-url` leaves the claim out; the login line then carries
`--gateway-url https://HOST/SITE` for you to fill in. One URL rule holds for
`--gateway-url`, `UMRS_PUBLIC_GATEWAY_URL` and the deployment's `.env` line:
`http(s)://HOST[:PORT][/PATH]` from letters, digits and `. _ ~ / -` only (no
user:password, query, fragment, space or shell character), trailing slash
dropped. An explicit `--gateway-url` outside it, or the literal example
`https://HOST/SITE`, exits 2 before anything is created; an env value outside
it is ignored with a warning. `user add` without `--issue-token` prints the
`generate-jwt` command that mints the same token (`--projects P1 P2` for
several projects, `--profile` forwarded), every value shell-quoted.

### List and Revoke Tokens

```bash
umrs-server admin token list
umrs-server admin token revoke <token-id>
```

## Slurm Account Binding

Every user who provisions endpoints needs a Slurm account:

```bash
# Set user's Slurm account
umrs-server admin user set-slurm-account alice my-slurm-project

# List available Slurm accounts
umrs-server admin slurm accounts
```

> **Important:** Users cannot provision endpoints without a configured Slurm account. The Gateway forwards `X-User-ID` to UMRS, which resolves the user's account and passes `--account=` to sbatch.

> **Note (2026-11-08):** the Slurm account is *attribution* (which allocation is charged). It does not choose the login a job runs under. That is now declared per site in `scheduler.hpc_access` ([ADR-112](../adr/112-user-hpc-accounts-gated-delegation.md) D-1, D-2), and the older per-user SSH delegation (`slurm_delegation: per_user_ssh`) is retired. There is no fallback from one login to another: work that cannot run under the site's declared identity is refused with an `hpcaccess.*` code. On a site whose login belongs to the operator, user work (staged steps, `hpc_script`, cluster loops) runs only for admins and listed projects:
>
> ```yaml
> scheduler:
>   hpc_access:
>     modes_allowed: [service_account]
>     default_mode: service_account
>     service_account:
>       login: <operator-account>     # must equal extra.ssh_user
>       personal: true
>       consent_ref: ""               # empty = interim
>       allowed_projects: [<project>] # empty list = admins only
> ```
>
> Registering your own HPC account, and what UMRS can and cannot do with it: [chapter 31](31-own-hpc-accounts.md).

## Project Membership

```bash
# Add user to a project
umrs-server admin user add-to-project alice team-beta

# Remove from project
umrs-server admin user remove-from-project alice team-beta
```

**No new token needed (2026-10-22).**
The gateway checks the project of each request against the user's *live*
memberships: the projects in this record plus the projects the user owns.
A change takes effect within 30 s (`GATEWAY_MEMBERSHIP_CACHE_TTL_SEC`), with
no new token. The user's next `r4 auth login` lists the new project, and
`r4 project switch <id>` acts in it.

The setting is `GATEWAY_PROJECT_MEMBERSHIP` on the gateway sidecar:

| Value | Behaviour |
|---|---|
| `token` | The old behaviour: only the token's project. No lookup, so a disabled account's token works until it expires. |
| `live` | The deploy default. Any live membership may be selected. |
| `enforce` | The token's own project must also be a live membership. |

Under `live`, a removed user keeps only the token's own project until the
token expires. Under `enforce`, removal takes effect at once.

**Removal ends the user's work in that project (2026-10-22, RS-0 S-2).**
`remove-from-project` revokes the user's leases in that project, ends their
remote sessions there and cancels their jobs there, as one audited event. The
user's other projects are untouched. Nothing is ended when the user still has
access to the project: an admin or service account, or the project's owner.

*Correction (2026-10-22):* before RS-0 the admin CLI wrote only the user's JSON
file, while the server reads the `users` row first, so a disable or a
membership change made with the CLI reached the server only at its next
restart (not within 30 s). The CLI now writes the row too.
`UMRS_ADMIN_CLI_DB=off` restores the JSON-only behaviour.

Refusals reach the client as JSON 403s:
- `project_not_member`: the requested project is not one of the user's memberships.
- `project_membership_revoked`: `enforce` only; the token's own project is no longer a membership.

When UMRS cannot be asked, the client gets a 503 `membership_unavailable`.

**The ZMQ rail (2026-10-22, ADR-097 Amendment D-7).** r4's DIRECT and
HPC_LOCAL modes send commands over ZMQ, not through the gateway. UMRS
applies the same rule there, at its command dispatcher. The setting is
`UMRS_ZMQ_PROJECT_MEMBERSHIP` on the UMRS server, with the same three values.
Its default is `live`. The session's token projects stand in for the
token's project. The refusals are JSON error replies with the same codes.
Admins act in any project.

## Circles (2026-10-05 — supersedes the 2026-10-03 groups)

Circles are **project-scoped** cells — the boundary that keeps the
contexts of works apart inside one project and the smallest addressable
sharing audience (`circle:<project>/<id>`) in the access lists of
shareable resources (endpoints, knowledge graphs, scientific catalogs,
workflows, buckets, RAG corpora — see §06, §09, §13). Superseded
`group:<id>` entries admit no one and are rejected on writes.

```bash
r4 circle enter proj-a                      # join a project: your personal cell
r4 circle mine                              # my circles + derived project list
r4 circle list proj-a                       # circles of one project
r4 circle create proj-a team-x              # named cell (needs affiliation)
r4 circle members proj-a team-x
r4 circle member-add proj-a team-x bob      # owner / circle-admin / project-owner / admin
r4 circle member-add proj-a team-x cleo --role admin
r4 circle member-remove proj-a team-x bob
r4 circle retire proj-a team-x              # owner-or-admin tombstone
```

The same reads and roster writes ride the console: `/circles [project]`
lists, `/circles <project> <id>` (or `/circles members <p> <id>`) shows a
circle's accounts, and `/circles enter|create|add|remove …` mints cells and
moves accounts in and out (2026-10-19; retire stays CLI-only).

Rules of the model (enforced by UMRS at `/umrs/circles`):

- **Personal cell**: `circle_id == your user id` is your private context
  in that project — creating it IS entering the project (casual, any
  verified user). It never gains other members.
- **Roster ladder** (four-rung, ADR-086 amended 2026-10-19): the circle
  owner, any `role=admin` member, the containing **project's owner**, or a
  platform admin adds/removes members; retire stays
  circle-owner-or-platform-admin — the project-owner rung covers the
  roster only, never lifecycle. Membership is presence-only (no expiry,
  no nesting, no cap).
- **Multi-membership**: a user joins any number of circles — every
  membership counts when an access entry `circle:<proj>/<id>` is checked.
- **Retire is a tombstone, not a delete**: retired circles stop resolving
  immediately — every access list that named the circle silently stops
  admitting (the row is kept for audit). Retired circles cannot grow.
- Propagation is live: membership changes take effect at the next access
  decision (at worst a minute on the LLM routing cache); no converge.
- Only an **admin** creates a circle for another user (`--for-user`).

## User Configuration Template

Generate a config template for new users:

```bash
umrs-server admin user config-template alice
```

This outputs the configuration the user needs to save.

## User Onboarding Flow

### Admin Side

```bash
# USERNAME, PROJECT_ID, ACCOUNT: your values
# 1. Create user
umrs-server admin user add USERNAME --project PROJECT_ID

# 2. Set Slurm account (HPC deployments)
umrs-server admin user set-slurm-account USERNAME ACCOUNT

# 3. Generate token (embeds the site's gateway URL when run in its container)
umrs-server admin token generate-jwt \
  --user USERNAME \
  --project PROJECT_ID \
  --days 90

# 4. Give the user the token and the login line the command printed
```

### User Side

```bash
# 1. Install client
pip install r4

# 2. Authenticate (the token carries the gateway URL and project)
r4 auth login --api-key TOKEN
# A token without a gateway URL needs it named:
#   r4 auth login --gateway-url "https://HOST/SITE" --api-key TOKEN

# 3. Verify
r4 health
r4 model list
```

## Server Configuration

### Reload Config

```bash
umrs-server admin config show
umrs-server admin reload
```

### Boot (One-Time)

```bash
umrs-server admin boot
```

## Multi-User Isolation

Each user gets:
- **Separate S3 buckets** for file storage
- **Project-scoped access** to shared resources
- **Per-user Slurm accounting** for resource tracking
- **Endpoint tier isolation** (system/project/user)

## Next Steps

- [Chapter 21 — UMRS Operations](21-umrs-operations.md)
- [Chapter 22 — Demo Suite](22-demos.md)
