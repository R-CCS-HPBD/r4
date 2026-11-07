# R4 Platform Makefile
# ===================================
# Install targets for the monorepo.
#
# Prerequisites (installed once per machine):
#   1. Python 3.11+ for this checkout (uv.lock, ADR-096; the r4 client alone
#      still supports 3.10+) — https://www.python.org/downloads/
#   2. uv (fast Python package manager) — https://docs.astral.sh/uv/
#        macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh
#        Windows:      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
#        Verify: uv --version
#   3. bun >= 1.3.14 (optional; runs Console v3 from this checkout)
#        curl -fsSL https://bun.sh/install | bash
#
# After the prerequisites are in place, the standard flow is:
#   make venv                       # create ./.venv if missing
#   source .venv/bin/activate       # activate the venv (Windows: .venv\Scripts\activate)
#   make install-client             # r4 + r4-contracts + umrs[client]
#   make install-server             # umrs[server] (heavy ML stack)
#
# For a clean-room client install (no source checkout required):
#   curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash

.PHONY: help install install-client install-server venv tui-deps

help:
	@echo "R4 Platform Makefile"
	@echo "===================="
	@echo ""
	@echo "Setup:"
	@echo "  make venv                  Create ./.venv (uses uv if available, else python3 -m venv)"
	@echo "  make install               Install ALL packages (client + server) into ./.venv (uv) or the active env (pip)"
	@echo "  make install-client        Install client packages: r4, r4-contracts, umrs[client]"
	@echo "  make install-server        Install server packages: umrs[server] (heavy ML stack)"
	@echo "  make tui-deps              Console v3 deps (bun install); r4 console then runs this checkout's TUI"
	@echo ""
	@echo "Typical client-only flow:"
	@echo "  make venv && source .venv/bin/activate && make install-client"
	@echo ""
	@echo "Typical full-stack (operator) flow:"
	@echo "  make venv && source .venv/bin/activate && make install"
	@echo ""
	@echo "Shell completion (optional, after install-client):"
	@echo "  r4 completion install      Install r4 tab completion into your shell RC"
	@echo "  umrs completion install      Install umrs tab completion"
	@echo ""
	@echo "Checks:"
	@echo "  make ci                    Local CI gate on files changed vs origin/main"
	@echo ""
	@echo "Documentation:"
	@echo "  make docs                  List documentation entry points"

# =============================================================================
# Environment / Prerequisites
# =============================================================================

# Detect uv: prefer uv when available (faster, lockfile-aware), fall back to plain pip.
UV := $(shell command -v uv 2>/dev/null)
PIP := $(UV) pip
PIP_FALLBACK := python3 -m pip

# Resolve the pip command to use.
ifeq ($(UV),)
PIP := $(PIP_FALLBACK)
endif

venv:
	@echo "==> Creating virtual environment at ./.venv"
	@if [ -d ".venv" ]; then \
		echo "    .venv already exists — skipping creation"; \
	else \
		python3 -m venv .venv; \
		echo "    created .venv"; \
	fi
	@echo ""
	@echo "==> Activate the venv before installing:"
	@echo "    source .venv/bin/activate"
	@echo ""

# =============================================================================
# Install
# =============================================================================

# install: client + server. Use `make install-client` for client-only setups.
install:
	@echo "==> Installing all packages (client + server)"
	@if [ "$(UV)" ]; then \
		uv sync --all-packages --all-extras; \
	else \
		$(PIP_FALLBACK) install --upgrade pip; \
		$(PIP_FALLBACK) install -e "./r4-contracts" -e "./packages/r4-teamengine-spi" -e "./science" -e "./r4" -e "./umrs/src[client,server]"; \
	fi
	@$(MAKE) --no-print-directory tui-deps || echo "==> WARNING: Console v3 deps step failed (see above); r4 console keeps its previous choice. Retry: make tui-deps"
	@echo "==> Done. Activate the venv: source .venv/bin/activate"

# install-client: r4 + r4-contracts + umrs[client]. Pulls in httpx, rich, boto3,
# psycopg2-binary (for the `list-recovery-status` helper), pyyaml.
# For direct ZMQ + S3 transport, install umrs[client-full] instead.
install-client:
	@echo "==> Installing client packages (r4, r4-contracts, umrs[client])"
	@if [ "$(UV)" ]; then \
		uv sync --all-packages --extra client; \
	else \
		$(PIP_FALLBACK) install --upgrade pip; \
		$(PIP_FALLBACK) install -e "./r4-contracts" -e "./packages/r4-teamengine-spi" -e "./science" -e "./r4" -e "./umrs/src[client]"; \
	fi
	@$(MAKE) --no-print-directory tui-deps || echo "==> WARNING: Console v3 deps step failed (see above); r4 console keeps its previous choice. Retry: make tui-deps"
	@echo "==> Done. Verify with: r4 --help && umrs --help"
	@echo "==> Optional: install shell completion with 'r4 completion install'"

# install-server: full client transport + server runtime + ML stack.
# Includes psycopg2-binary, pandas, fastapi, torch, transformers, etc.
install-server:
	@echo "==> Installing server packages (umrs[server])"
	@if [ "$(UV)" ]; then \
		uv pip install -e "./r4-contracts" -e "./r4" -e "./umrs/src[server]"; \
	else \
		$(PIP_FALLBACK) install --upgrade pip; \
		$(PIP_FALLBACK) install -e "./r4-contracts" -e "./r4" -e "./umrs/src[server]"; \
	fi
	@echo "==> Done. Verify with: umrs-server --help"
	@echo "==> Optional: install shell completion with 'umrs-server completion install'"

# tui-deps: Console v3 (packages/r4-tui-v3) runs from THIS checkout's source.
# Once its node_modules is installed, `r4 console` prefers it over the per-user
# release binary from `r4 tui install`, so every clone runs its own TUI. Nothing
# is compiled or copied into .venv/bin: a copied binary would go stale on the
# next TUI edit or pull. The stamp records the package.json + bun.lock that
# node_modules was installed from; `r4 console` warns when they change
# (r4/src/r4/_cli/console.py DEPS_STAMP). No bun -> skipped.
TUI_DIR := packages/r4-tui-v3
BUN_MIN := 1.3.14
BUN := $(shell command -v bun 2>/dev/null || { test -x "$$HOME/.bun/bin/bun" && echo "$$HOME/.bun/bin/bun"; })

tui-deps:
	@if [ -z "$(BUN)" ]; then \
		echo "==> Console v3: bun not found - skipped ($(TUI_DIR))"; \
		echo "    r4 console falls back to the release binary (r4 tui install)."; \
		echo "    To run this checkout's console: install bun >= $(BUN_MIN) (https://bun.sh), then: make tui-deps"; \
		exit 0; \
	fi; \
	ver="$$("$(BUN)" --version)"; \
	if [ "$$(printf '%s\n' "$(BUN_MIN)" "$$ver" | sort -V | head -n 1)" != "$(BUN_MIN)" ]; then \
		echo "==> Console v3: bun $$ver is older than $(BUN_MIN) (packages/r4-tui-v3 engines) - run: bun upgrade"; \
		exit 1; \
	fi; \
	echo "==> Console v3: bun $$ver install --frozen-lockfile ($(TUI_DIR))"; \
	cd "$(TUI_DIR)" && "$(BUN)" install --frozen-lockfile && \
		cat package.json bun.lock > node_modules/.r4-deps-stamp

# =============================================================================
# Documentation
# =============================================================================

docs:
	@echo "Documentation available at:"
	@echo "  - README.md"
	@echo "  - docs/architecture/boundaries/INTEGRATION_POLICY.md"
	@echo "  - r4/README.md"
	@echo "  - mcp-servers/README.md"
	@echo "  - gateway/README.md"
	@echo "  - docs/R4-UMRS-DESIGN.md"
	@echo "  - docs/adr/README.md"

# =============================================================================
# CI/CD
# =============================================================================

# The local CI gate: lint, complexity, boundary validators and the tests of the
# changed components (files changed against origin/main). For the whole tree:
# bash scripts/ci-lint-local.sh --full
.PHONY: ci
ci:
	bash scripts/ci-lint-local.sh

pre-commit:
	uv run pre-commit run --all-files

# =============================================================================
# Architecture
# =============================================================================

setup-hooks:
	@command -v pre-commit >/dev/null 2>&1 || pip install pre-commit
	pre-commit install --hook-type pre-push
	@echo "Pre-push hooks installed. Boundary validators will run before each push."

arch-health:
	@python tools/arch_health.py

arch-health-json:
	@python tools/arch_health.py --json

arch-health-verbose:
	@python tools/arch_health.py --verbose
