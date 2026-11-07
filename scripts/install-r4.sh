#!/bin/bash
# R4 Platform CLI — single-shot installer
# Usage: curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
#
# Installs r4 into a virtual environment at ~/.r4/venv and creates a
# symlink (or wrapper) in a directory on $PATH.
#
# Environment overrides:
#   R4_INSTALL_DIR  — base directory (default: ~/.r4)
#   R4_BRANCH       — git branch/tag to install from (default: main)
#   R4_REPO_URL     — git URL to clone from
#                     (default: https://github.com/R-CCS-HPBD/r4.git; maintainers
#                     of the private development repository set their own)

set -euo pipefail

R4_INSTALL_DIR="${R4_INSTALL_DIR:-$HOME/.r4}"
R4_BRANCH="${R4_BRANCH:-main}"
VENV_DIR="$R4_INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
REPO_URL="${R4_REPO_URL:-https://github.com/R-CCS-HPBD/r4.git}"

info()  { printf '\033[0;34m→\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m✓\033[0m %s\n' "$*"; }
err()   { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; }

# ── Pre-flight checks ──────────────────────────────────────────────────────

command -v python3 >/dev/null 2>&1 || { err "python3 is required"; exit 1; }
command -v git     >/dev/null 2>&1 || { err "git is required"; exit 1; }

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    err "Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi

info "Installing R4 CLI (Python $PY_VERSION, branch $R4_BRANCH)"

# ── Clone / update source ──────────────────────────────────────────────────

SRC_DIR="$R4_INSTALL_DIR/src"
if [ -d "$SRC_DIR/.git" ]; then
    info "Updating existing clone..."
    git -C "$SRC_DIR" fetch origin "$R4_BRANCH" --depth 1
    git -C "$SRC_DIR" checkout FETCH_HEAD --force
else
    info "Cloning R4 ($REPO_URL)..."
    mkdir -p "$R4_INSTALL_DIR"
    git clone --depth 1 --branch "$R4_BRANCH" "$REPO_URL" "$SRC_DIR"
fi

# The umrs client lives in the umrs submodule (UMRS-org/umrs, ADR-010). A
# plain clone leaves umrs/ empty and the umrs[client] install below fails.
# The public repositories (R-CCS-HPBD/r4, UMRS-org/umrs) clone without
# credentials; an R4_REPO_URL naming the private development repository needs
# a credential helper or `gh auth login` for its HTTPS clone.
info "Fetching the umrs submodule..."
git -C "$SRC_DIR" submodule sync --quiet
git -C "$SRC_DIR" -c 'url.https://github.com/.insteadOf=git@github.com:' \
    submodule update --init --depth 1 umrs

# ── Create / update venv ───────────────────────────────────────────────────

if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

info "Installing client packages..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# Install client packages in dependency order
# (mirrors `make install-client`: r4 depends on r4-teamengine-spi and science,
# which are workspace packages, not on PyPI)
"$VENV_DIR/bin/pip" install -e "$SRC_DIR/r4-contracts" --quiet
"$VENV_DIR/bin/pip" install -e "$SRC_DIR/packages/r4-teamengine-spi" --quiet
"$VENV_DIR/bin/pip" install -e "$SRC_DIR/science" --quiet
"$VENV_DIR/bin/pip" install -e "$SRC_DIR/r4" --quiet
# Lightweight client from the umrs monorepo (no pyzmq/boto3/server deps).
# Use umrs[client-full] for direct ZMQ + S3 transport.
"$VENV_DIR/bin/pip" install -e "$SRC_DIR/umrs/src[client]" --quiet

# ── Symlinks ────────────────────────────────────────────────────────────────

mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/r4" "$BIN_DIR/r4"
ln -sf "$VENV_DIR/bin/umrs" "$BIN_DIR/umrs"
ln -sf "$VENV_DIR/bin/umrs" "$BIN_DIR/umrs-client"  # backwards-compatible alias

# ── Verify ──────────────────────────────────────────────────────────────────

failed=0
if "$VENV_DIR/bin/r4" --help >/dev/null 2>&1; then
    ok "r4 installed successfully"
else
    err "r4 --help failed"
    failed=1
fi
if "$VENV_DIR/bin/umrs" --help >/dev/null 2>&1; then
    ok "umrs installed successfully"
else
    err "umrs --help failed"
    failed=1
fi
if [ "$failed" -ne 0 ]; then
    exit 1
fi

# ── PATH hint ───────────────────────────────────────────────────────────────

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        info "Add $BIN_DIR to your PATH:"
        echo "  export PATH=\"$BIN_DIR:\$PATH\""
        echo ""
        info "Or add to your shell profile (~/.bashrc, ~/.zshrc)."
        ;;
esac

ok "Run 'r4 onboard' to get started."
