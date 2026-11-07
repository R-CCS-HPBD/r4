# Contributing to R4

Thank you for your interest in R4. Bug reports, documentation fixes and code
changes are all welcome. This guide explains how a contribution travels from
a pull request to a release, what licence it is under, and what has to pass
before a maintainer can accept it.

## How contributions flow

R4 is developed in a private repository at the RIKEN Center for Computational
Science (R-CCS). This public repository, `R-CCS-HPBD/r4`, is a reviewed export
of that repository: internal records, operations material and private demos
are left out. Because of that, pull requests here are not merged directly.

`CONSTITUTION.md` and `docs/onboarding/agent.md` describe the maintainers'
internal workflow (ledger entries, gap rows, internal gate scripts, state
files that are not in this repository). Public contributors, and coding
agents working for them, follow this guide instead; `AGENTS.md` points here
too. Where the pull request template or another document asks for one of
those internal items, a public pull request can leave it out.

1. **Open an issue** on `R-CCS-HPBD/r4` for bugs and feature ideas. For a
   larger change, please open an issue first so we can agree on the approach
   before you write the code.
2. **Open a pull request** against `main` on `R-CCS-HPBD/r4`. Keep it focused
   on one change and describe what it does and how you tested it.
3. **Review.** A maintainer reviews the pull request here, in public. The
   public CI (lint and tests, see below) must be green.
4. **Port.** Once accepted, a maintainer applies your change to the
   development repository. You stay the author of record: the commit carries
   a `Co-authored-by:` trailer with the name and e-mail address of your
   commits (use your GitHub no-reply address if you prefer not to publish
   one).
5. **Sync and close.** The change appears here with the next public sync. The
   maintainer then closes your pull request with a link to the commit that
   contains it.

The port can adjust your change slightly, for example to fit code that has
moved on in the development repository since the last sync. If it needs more
than small adjustments, the maintainer will ask you in the pull request
first.

### Changes to UMRS

`umrs/` is a git submodule pointing at UMRS (Unified Model Registry &
Storage), a separate project: `UMRS-org/umrs` on GitHub, branch
`ai4s-platform`. Changes to code under `umrs/` belong in a pull request to
that repository. Once it is merged there, the pointer in this repository is
updated with the next sync.

## Licensing of contributions

R4 is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
Contributions are accepted under the same licence ("inbound = outbound"): as
section 5 of the licence states, any contribution you intentionally submit
for inclusion is under the terms of the Apache License 2.0, without any
additional terms or conditions. No separate contributor licence agreement is
required.

By opening a pull request you confirm that you have the right to submit the
change under that licence, for example that it is your own work or that your
employer allows you to contribute it. Do not include code copied from
sources whose licence is incompatible with Apache-2.0.

## Reporting security issues

Do not open a public issue or pull request for a security vulnerability.
Follow [SECURITY.md](SECURITY.md) to report it privately.

## Development setup

Prerequisites:

- Python 3.11 or newer (the checkout pins 3.11 in `.python-version`; CI uses
  3.11).
- git.
- [uv](https://docs.astral.sh/uv/) is recommended but optional: the
  `Makefile` uses it when it is on your `PATH` and falls back to `pip`
  otherwise.
- [bun](https://bun.sh) 1.3.14 or newer, only if you work on the terminal UI
  in `packages/r4-tui-v3`.
- Podman or Docker, only if you work on the stand-alone stack
  (`./r4-local up`, see [deploy/standalone/README.md](deploy/standalone/README.md)).

Supported operating systems are Linux and macOS. On Windows, use WSL2.

### Clone

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
git submodule update --init --recursive    # only if you cloned without submodules
```

### Install the Python packages

Either use the `Makefile`:

```bash
make venv
source .venv/bin/activate
make install-client      # the client packages and umrs[client]; also runs make tui-deps
```

`make install` installs the client and the UMRS server stack (a heavy machine
learning dependency set); you only need it if you work on server code.

Or install exactly what the public CI (`.github/workflows/ci.yaml`)
installs, with plain `pip` inside a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ./r4-contracts -e ./packages/r4-teamengine-spi -e ./science \
  -e "./r4[dev,test,umrs]" -e "./umrs/src[client]" -e ./mcp-servers \
  fastapi python-multipart pytest-xdist ipython
pip install "ruff==0.16.6"
```

The `Makefile` route does not install r4's test extras, the MCP servers or
the test helpers. Before running the tests, run the two `pip install` lines
above inside the same virtual environment.

### Install the terminal UI dependencies

The terminal UI lives in `packages/r4-tui-v3` (TypeScript, run with bun).

```bash
make tui-deps
# or, by hand:
cd packages/r4-tui-v3 && bun install --frozen-lockfile
```

Once its dependencies are installed, `r4 console` started from this
checkout runs the checkout's own terminal UI.

## Checks that must pass

The public CI (`.github/workflows/ci.yaml`) runs on every pull request. Run
the same commands locally before you push:

```bash
# Lint
ruff check r4/src r4-contracts/src

# Contract tests
python -m pytest r4-contracts/tests -q

# r4 tests, as CI runs them: from r4/, with a read-only scratch HOME so
# that no test can touch your real home directory
TEST_HOME="$(mktemp -d)" && chmod a-w "$TEST_HOME"
(cd r4 && HOME="$TEST_HOME" python -m pytest tests/ -q -n 4 -p no:cacheprovider \
  --deselect tests/test_jupyter.py \
  --ignore=tests/test_companion/test_jupyter_magic.py \
  --deselect "tests/test_package_imports.py::test_import[r4.jupyter]" \
  --deselect tests/test_cli.py::TestCLIConfigContext \
  --deselect tests/test_cli_help.py::test_cli_help_exits_zero \
  --deselect tests/test_gateway_down_ux.py \
  --deselect tests/test_sweep_templates.py::TestCLIParserSetup::test_sweep_group_registered \
  --deselect tests/test_user_journeys.py \
  --deselect tests/test_tui_backend_audit_coverage.py::test_umrs_api_modules_import_without_errors)
```

The deselected tests are the ones CI skips too (suites that need the full
UMRS server stack or are still landing); `.github/workflows/ci.yaml` is the
authority if the two lists ever differ.

If you change the terminal UI, also run its type check and test suite:

```bash
cd packages/r4-tui-v3
bun run typecheck
bun run test
```

`bun run test` runs `bun test` over the suite in batches (see
`packages/r4-tui-v3/scripts/run-tests.ts`); to run single files, pass them:
`bun scripts/run-tests.ts tests/<name>.test.ts`.

Please also format the Python files you change with `ruff format <files>`.

## Code style

- **Complexity of at most 9 per function.** Every function you add or change
  must have a cyclomatic complexity of 9 or less. Check the files you touch
  with:

  ```bash
  ruff check --select C901 --config "lint.mccabe.max-complexity = 9" <files>
  ```

  The command also lists functions elsewhere in the file that were already
  over the limit; only the functions you add or change must pass. The public
  CI does not run this check; reviewers apply it.

  If a function you edit is already over the limit, split it rather than
  make it longer.
- **Tests ship with the change.** A bug fix comes with a test that fails on
  the old code and passes with the fix. New behaviour comes with tests for it.
- **Tests stay hermetic.** Tests must not read or write your real home
  directory, configuration or credentials, and must not need network access
  or a running service unless they are explicitly marked and skipped by
  default.
- **No secrets anywhere.** Never put credentials, tokens, API keys, private
  hostnames or IP addresses in code, tests, fixtures, examples or
  documentation. Use obvious placeholders such as `example.org` and
  `<your-token>`.
- Type hints on public functions; follow the existing style of the file you
  edit.
- Keep documentation in step with the code: if your change makes a document
  wrong, update it in the same pull request.

## Getting help

Open an issue with your operating system, Python version, how you installed
R4, and the exact command and output. For the stand-alone stack, include the
output of `./r4-local` commands you ran (with any secrets removed).
