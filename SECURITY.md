# Security policy

R4 is developed at the RIKEN Center for Computational Science (R-CCS) as a
research project. We take security reports seriously and are grateful to
anyone who helps us find and fix problems responsibly.

## Reporting a vulnerability

Please report vulnerabilities privately. **Do not open a public issue, pull
request or discussion** for a suspected vulnerability.

1. Go to the [R-CCS-HPBD/r4](https://github.com/R-CCS-HPBD/r4) repository on
   GitHub.
2. Open the **Security** tab.
3. Click **Report a vulnerability** and fill in the form.

This uses GitHub private vulnerability reporting: only you and the project
maintainers can see the report.

If the **Report a vulnerability** button is not shown, open a public issue
that only asks for a private contact, with no details of the problem, and we
will reply with a private channel.

Problems in UMRS (Unified Model Registry & Storage, the backbone service
included here as the `umrs/` submodule) should also be reported on
R-CCS-HPBD/r4 in the same way; we coordinate the fix with the UMRS
maintainers. If you are not sure which project is affected, report it on
R-CCS-HPBD/r4 and we will route it.

## What to include

A good report lets us reproduce the problem quickly. Where you can, include:

- the affected component (r4 client, terminal UI, VS Code extension,
  stand-alone stack, published image, UMRS) and its version, release tag or
  commit;
- your platform (Linux, macOS with podman machine or Docker Desktop, or
  Windows via WSL2) and container runtime, if relevant;
- a description of the issue and its impact (for example: what an attacker
  can read, change or run, and from where);
- step-by-step reproduction instructions, a minimal proof of concept, or
  both;
- any known workaround or suggested fix.

Please do not include real credentials, tokens or personal data in a report.
Use placeholders or test accounts. Do not access, modify or delete data that
is not yours while investigating.

## What to expect

R4 is maintained by a small research team, so we work on a best-effort basis
and cannot promise fixed response times. In general:

- we acknowledge a report once we have read it;
- we investigate, keep you informed of our assessment and progress, and may
  ask follow-up questions in the private report;
- when a fix is ready we release it, then publish an advisory on GitHub; we
  are happy to credit you in the advisory unless you prefer to stay
  anonymous.

We follow coordinated disclosure: please give us a reasonable chance to fix
the problem before you share details publicly, and agree a disclosure date
with us in the report.

## Supported versions

Security fixes are made for:

| Version                  | Supported |
| ------------------------ | --------- |
| `main` branch            | Yes       |
| Latest release           | Yes       |
| Older releases           | No        |

If you run an older release, please upgrade to the latest release (or
`main`) before reporting, and tell us if the issue only affects an older
version.

## Scope

In scope:

- the r4 client and agent (`r4/`);
- the terminal UI (`packages/r4-tui-v3/`);
- the VS Code extension (`vscode-extension/`);
- the stand-alone local stack (`./r4-local`, `deploy/standalone/`; see
  [deploy/standalone/README.md](deploy/standalone/README.md)) and its
  published image `ghcr.io/r-ccs-hpbd/r4-standalone-umrs`;
- UMRS ([UMRS-org/umrs](https://github.com/UMRS-org/umrs), the `umrs/`
  submodule), reported through R-CCS-HPBD/r4 as described above.

Out of scope:

- third-party models (including their outputs) and model providers;
- third-party dependencies, container base images and tools, except where
  the way R4 integrates, configures or ships them is at fault (please report
  other issues to the upstream project);
- issues that need an already-compromised machine or account, or physical
  access;
- deployments run by other parties, unless the issue is in R4 itself.

If you are unsure whether something is in scope, report it anyway; we would
rather hear about it.

## Contributing fixes

Please do not send a public pull request for an unpublished vulnerability.
Once an issue is disclosed, general contribution guidance is in
[CONTRIBUTING.md](CONTRIBUTING.md).
