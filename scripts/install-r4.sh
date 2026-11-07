#!/bin/bash
# R4 client — one-command installer (macOS, Linux, Windows through WSL2)
#
#   curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
#
# Default path (2026-11-07): install the newest R4 release (prereleases
# included) as a uv tool. The installer
#   1. uses the uv on PATH, or installs uv with the official astral.sh installer;
#   2. finds the release (R4_VERSION, else the newest release that publishes a
#      client bundle, through the anonymous GitHub API);
#   3. downloads r4-client-<tag>.tar.gz, checks it against the release's
#      SHA256SUMS asset when one is published, and every wheel in it against
#      the bundle's own SHA256SUMS (any mismatch aborts);
#   4. runs `uv tool install` on those LOCAL wheels only (r4, r4-contracts,
#      r4-teamengine-spi, r4-science, umrs[client]), so no package index can
#      substitute a same-named package; third-party dependencies come from PyPI;
#   5. fetches the console binary (`r4 tui install`, same tag).
# Upgrade: run the same command again. Uninstall: uv tool uninstall r4, and
# rm -f ~/.local/share/r4/bin/r4-tui for the console ($R4_TUI_HOME/bin/r4-tui
# when R4_TUI_HOME is set; the rest of ~/.local/share/r4 is the delete-guard trash)
#
# When no release publishes a client bundle yet, or with R4_FROM_SOURCE=1, it
# falls back to the source install: a git clone in ~/.r4/src and a venv in
# ~/.r4/venv, linked into ~/.local/bin (needs python3 >= 3.10 and git).
#
# Environment overrides:
#   R4_VERSION           release tag to install, e.g. v0.2.0-rc2 (default: newest)
#   R4_RELEASE_REPO      GitHub owner/name of the releases (default: R-CCS-HPBD/r4)
#   R4_RELEASE_API_URL   release listing JSON (default: the GitHub API for R4_RELEASE_REPO)
#   R4_RELEASE_BASE_URL  asset base; the bundle is <base>/<tag>/r4-client-<tag>.tar.gz
#                        (default: https://github.com/<repo>/releases/download; a mirror
#                        or a file:// directory works). When set, the console binary
#                        comes from <base>/<tag> too, unless R4_TUI_DIST_BASE_URL is set
#   R4_PYTHON            Python version for the tool environment (default: 3.12;
#                        uv downloads it when the machine has none)
#   R4_SKIP_TUI=1        do not run `r4 tui install`
#   R4_NO_UV_INSTALL=1   never install uv; stop with instructions instead
#   R4_UV_INSTALLER_URL  uv installer script (default: https://astral.sh/uv/install.sh)
#   UV_INSTALL_DIR       where the uv installer puts uv (default ~/.local/bin)
#   UV_TOOL_DIR, UV_TOOL_BIN_DIR, UV_PYTHON_INSTALL_DIR, HTTPS_PROXY, ...
#                        read by uv and curl as usual
#   R4_FROM_SOURCE=1     use the source install below instead of a release
# Source install only:
#   R4_INSTALL_DIR       base directory (default: ~/.r4)
#   R4_BRANCH            git branch or tag to install from (default: main)
#   R4_REPO_URL          git URL to clone from
#                        (default: https://github.com/R-CCS-HPBD/r4.git; maintainers
#                        of the private development repository set their own)
#
# The whole script is one function called on the last line, so `curl | bash`
# has read all of it before anything runs.

set -euo pipefail

R4_RELEASE_REPO="${R4_RELEASE_REPO:-R-CCS-HPBD/r4}"
R4_RELEASE_API_URL="${R4_RELEASE_API_URL:-https://api.github.com/repos/$R4_RELEASE_REPO/releases?per_page=20}"
R4_RELEASE_BASE_URL="${R4_RELEASE_BASE_URL:-https://github.com/$R4_RELEASE_REPO/releases/download}"
R4_PYTHON="${R4_PYTHON:-3.12}"
R4_UV_INSTALLER_URL="${R4_UV_INSTALLER_URL:-https://astral.sh/uv/install.sh}"
WORK_DIR=""
# 1 when uv_install had to install the umrs CLI as a tool of its own.
UMRS_OWN_TOOL=0

if [ -t 1 ]; then
    info()  { printf '\033[0;34m→\033[0m %s\n' "$*"; }
    ok()    { printf '\033[0;32m✓\033[0m %s\n' "$*"; }
    warn()  { printf '\033[0;33m!\033[0m %s\n' "$*" >&2; }
    err()   { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; }
else
    info()  { printf -- '-> %s\n' "$*"; }
    ok()    { printf 'ok %s\n' "$*"; }
    warn()  { printf 'warning: %s\n' "$*" >&2; }
    err()   { printf 'error: %s\n' "$*" >&2; }
fi
die() { err "$*"; exit 1; }

cleanup() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

# ── Downloads and checksums ────────────────────────────────────────────────

# fetch URL DEST: download to a file (curl, else wget); proxies via HTTPS_PROXY.
fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$1"
    else
        die "curl or wget is required"
    fi
}

# sha256_check DIR: verify DIR/SHA256SUMS (sha256sum, else shasum -a 256).
sha256_check() {
    local out
    if command -v sha256sum >/dev/null 2>&1; then
        out=$(cd "$1" && sha256sum -c SHA256SUMS 2>&1) && return 0
    elif command -v shasum >/dev/null 2>&1; then
        out=$(cd "$1" && shasum -a 256 -c SHA256SUMS 2>&1) && return 0
    else
        die "sha256sum or shasum is required to verify the download"
    fi
    printf '%s\n' "$out" >&2
    return 1
}

# ── uv ─────────────────────────────────────────────────────────────────────

# find_uv: print the path of a usable uv, or nothing.
find_uv() {
    local candidate
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for candidate in "${UV_INSTALL_DIR:-}/uv" "${XDG_BIN_HOME:-$HOME/.local/bin}/uv" \
        "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 0
}

ensure_uv() {
    local uv installer
    uv=$(find_uv)
    if [ -n "$uv" ]; then
        printf '%s\n' "$uv"
        return 0
    fi
    if [ "${R4_NO_UV_INSTALL:-0}" = "1" ]; then
        die "uv is not installed and R4_NO_UV_INSTALL=1. Install uv (https://docs.astral.sh/uv/) and rerun."
    fi
    info "uv (the Python package manager R4 installs with) was not found." >&2
    info "Installing it with the official installer: $R4_UV_INSTALLER_URL" >&2
    installer="$WORK_DIR/uv-install.sh"
    fetch "$R4_UV_INSTALLER_URL" "$installer" || die "could not download the uv installer"
    sh "$installer" </dev/null >&2 || die "the uv installer failed"
    uv=$(find_uv)
    [ -n "$uv" ] || die "uv was installed but not found; open a new shell and rerun"
    printf '%s\n' "$uv"
}

# ── Release discovery ──────────────────────────────────────────────────────

# bundle_tag_from_json: read the GitHub releases JSON on stdin and print the
# newest non-draft tag that publishes r4-client-<tag>.tar.gz (listing order is
# newest first; prereleases count). Prints nothing when none does.
bundle_tag_from_json() {
    grep -oE '"(tag_name|draft|browser_download_url)": *("[^"]*"|true|false)' |
        awk -F'": *' '
            /^"tag_name"/ { tag = $2; gsub(/"/, "", tag); draft = ""; next }
            /^"draft"/ { draft = $2; next }
            /^"browser_download_url"/ {
                url = $2; gsub(/"/, "", url)
                want = "/r4-client-" tag ".tar.gz"
                n = length(url) - length(want) + 1
                if (draft == "false" && tag != "" && n > 0 && substr(url, n) == want) {
                    print tag; exit
                }
            }'
}

valid_tag() {
    case "$1" in
        "" | *[!A-Za-z0-9._+-]*) return 1 ;;
        *) return 0 ;;
    esac
}

# resolve_tag: print the tag to install, or nothing when no release has a bundle.
resolve_tag() {
    local listing tag
    if [ -n "${R4_VERSION:-}" ]; then
        tag="$R4_VERSION"
        case "$tag" in [0-9]*) tag="v$tag" ;; esac
        valid_tag "$tag" || die "R4_VERSION is not a tag name: $R4_VERSION"
        printf '%s\n' "$tag"
        return 0
    fi
    listing="$WORK_DIR/releases.json"
    if ! fetch "$R4_RELEASE_API_URL" "$listing"; then
        err "Could not list the releases of $R4_RELEASE_REPO ($R4_RELEASE_API_URL)."
        err "Anonymous GitHub API calls are rate-limited per address. Pin a release"
        err "with R4_VERSION=<tag>, or install from source with R4_FROM_SOURCE=1."
        exit 1
    fi
    tag=$(bundle_tag_from_json <"$listing" || true)
    if [ -n "$tag" ] && ! valid_tag "$tag"; then
        die "the release listing names an invalid tag: $tag"
    fi
    printf '%s\n' "$tag"
}

# ── Release install ────────────────────────────────────────────────────────

# one_wheel DIR GLOB: the single wheel in DIR matching GLOB, listed in SHA256SUMS.
one_wheel() {
    local found="" path
    # shellcheck disable=SC2086  # $2 is the glob
    for path in "$1"/$2; do
        [ -f "$path" ] || continue
        [ -z "$found" ] || die "the bundle holds more than one $2"
        found="$path"
    done
    [ -n "$found" ] || die "the bundle holds no $2"
    awk -v f="$(basename "$found")" '{ n = $2; sub(/^\*/, "", n); if (n == f) hit = 1 }
        END { exit !hit }' "$1/SHA256SUMS" ||
        die "$(basename "$found") is not listed in SHA256SUMS"
    printf '%s\n' "$found"
}

# file_url PATH: a file:// URL for an absolute path (spaces escaped).
file_url() {
    printf 'file://%s\n' "$(printf '%s' "$1" | sed 's/%/%25/g; s/ /%20/g')"
}

# check_release_sums TAG NAME: when the release publishes a SHA256SUMS asset
# naming NAME, the downloaded NAME must match it (no asset: nothing to check).
check_release_sums() {
    local line
    fetch "$R4_RELEASE_BASE_URL/$1/SHA256SUMS" "$WORK_DIR/release-sums" 2>/dev/null || return 0
    line=$(awk -v f="$2" '{ n = $2; sub(/^\*/, "", n); if (n == f) print }' "$WORK_DIR/release-sums")
    [ -n "$line" ] || return 0
    printf '%s\n' "$line" >"$WORK_DIR/SHA256SUMS"
    sha256_check "$WORK_DIR" || die "checksum mismatch: $2 differs from the release's SHA256SUMS; aborting"
}

# fetch_bundle TAG: download and verify the client bundle; print the wheel dir.
fetch_bundle() {
    local tag="$1" name sums
    name="r4-client-$tag.tar.gz"
    info "Downloading $R4_RELEASE_BASE_URL/$tag/$name" >&2
    if ! fetch "$R4_RELEASE_BASE_URL/$tag/$name" "$WORK_DIR/$name"; then
        err "Release $tag has no client bundle ($name) at $R4_RELEASE_BASE_URL."
        die "Pick another R4_VERSION, or install that tag from source: R4_FROM_SOURCE=1 R4_BRANCH=$tag"
    fi
    check_release_sums "$tag" "$name" || exit 1
    mkdir -p "$WORK_DIR/bundle" || exit 1
    tar -xzf "$WORK_DIR/$name" -C "$WORK_DIR/bundle" || die "$name is not a readable tar.gz"
    sums=$(find "$WORK_DIR/bundle" -maxdepth 2 -name SHA256SUMS -type f | head -n 1)
    [ -n "$sums" ] || die "$name holds no SHA256SUMS; refusing to install unverified wheels"
    sha256_check "$(dirname "$sums")" || die "checksum mismatch in $name; aborting"
    ok "Verified the wheels in $name against SHA256SUMS" >&2
    dirname "$sums"
}

supports_executables_from() {
    "$1" tool install --help 2>/dev/null | grep -q -- '--with-executables-from'
}

# wheel_url DIR GLOB: file:// URL of the verified wheel matching GLOB. It runs
# inside $(...), where bash drops `set -e`, so each step is checked.
wheel_url() {
    local path
    path=$(one_wheel "$1" "$2") || exit 1
    file_url "$path"
}

# uv_install UV WHEEL_DIR: install r4 (and the umrs CLI) from the local wheels.
uv_install() {
    local uv="$1" dir="$2" r4 contracts spi science umrs
    r4=$(wheel_url "$dir" 'r4-[0-9]*.whl')
    contracts=$(wheel_url "$dir" 'r4_contracts-*.whl')
    spi=$(wheel_url "$dir" 'r4_teamengine_spi-*.whl')
    science=$(wheel_url "$dir" 'r4_science-*.whl')
    umrs=$(wheel_url "$dir" 'umrs-*.whl')
    info "Installing r4 with uv (Python $R4_PYTHON)..."
    if supports_executables_from "$uv"; then
        "$uv" tool install --python "$R4_PYTHON" --force "r4 @ $r4" \
            --with "r4-contracts @ $contracts" --with "r4-teamengine-spi @ $spi" \
            --with "r4-science @ $science" --with-executables-from "umrs[client] @ $umrs" \
            </dev/null || die "uv tool install failed"
        return 0
    fi
    # uv without --with-executables-from: the umrs CLI gets its own tool env.
    "$uv" tool install --python "$R4_PYTHON" --force "r4 @ $r4" \
        --with "r4-contracts @ $contracts" --with "r4-teamengine-spi @ $spi" \
        --with "r4-science @ $science" --with "umrs[client] @ $umrs" \
        </dev/null || die "uv tool install failed"
    "$uv" tool install --python "$R4_PYTHON" --force "umrs[client] @ $umrs" \
        --with "r4-contracts @ $contracts" </dev/null || die "uv tool install (umrs) failed"
    UMRS_OWN_TOOL=1
}

# console_dist_base TAG: the asset base `r4 tui install` should use, or nothing
# for its own default. A mirror (R4_RELEASE_BASE_URL) serves the console of the
# same release; an explicit R4_TUI_DIST_BASE_URL wins.
console_dist_base() {
    if [ -n "${R4_TUI_DIST_BASE_URL:-}" ]; then
        printf '%s\n' "$R4_TUI_DIST_BASE_URL"
    elif [ "$R4_RELEASE_BASE_URL" != "https://github.com/$R4_RELEASE_REPO/releases/download" ]; then
        printf '%s\n' "$R4_RELEASE_BASE_URL/$1"
    fi
}

install_console() {
    local bin="$1" tag="$2" dist
    if [ "${R4_SKIP_TUI:-0}" = "1" ]; then
        info "R4_SKIP_TUI=1: skipping the console binary (later: r4 tui install)"
        return 0
    fi
    info "Fetching the console binary (r4 tui install)..."
    dist=$(console_dist_base "$tag")
    if [ -n "$dist" ]; then
        export R4_TUI_DIST_BASE_URL="$dist"
    fi
    if R4_TUI_VERSION="${R4_TUI_VERSION:-$tag}" "$bin/r4" tui install </dev/null; then
        return 0
    fi
    warn "The console binary was not installed; the r4 CLI works without it."
    warn "Retry later with: r4 tui install   (Windows: run R4 inside WSL2)"
}

on_path() {
    case ":$PATH:" in *":$1:"*) return 0 ;; *) return 1 ;; esac
}

# path_hints BIN: how to reach BIN/r4 (both install paths).
path_hints() {
    local bin="$1" found
    if ! on_path "$bin"; then
        info "Add $bin to your PATH:"
        echo "  export PATH=\"$bin:\$PATH\""
        return 0
    fi
    found=$(command -v r4 || true)
    if [ -n "$found" ] && [ "$found" != "$bin/r4" ]; then
        warn "another r4 comes first on PATH: $found (this install is $bin/r4)"
    fi
}

# release_hints BIN: PATH, leftovers of a source install, upgrade, uninstall.
# Release path only: the source fallback has no uv and uses ~/.r4/venv.
release_hints() {
    local bin="$1" uninstall="uv tool uninstall r4"
    path_hints "$bin"
    on_path "$bin" || info "or run 'uv tool update-shell' once, then open a new shell."
    if [ -d "$HOME/.r4/venv" ]; then
        info "An earlier source install in ~/.r4 is no longer used (rm -rf ~/.r4/venv ~/.r4/src)."
    fi
    if [ "$UMRS_OWN_TOOL" = "1" ]; then
        uninstall="$uninstall && uv tool uninstall umrs"
    fi
    info "Upgrade: run the install command again. Uninstall: $uninstall"
    info "  and rm -f ${R4_TUI_HOME:-$HOME/.local/share/r4}/bin/r4-tui (the console binary)"
}

install_release() {
    local tag="$1" uv bin dir
    uv=$(ensure_uv)
    info "Installing R4 $tag from $R4_RELEASE_REPO (uv: $uv)"
    dir=$(fetch_bundle "$tag")
    uv_install "$uv" "$dir"
    bin=$("$uv" tool dir --bin)
    "$bin/r4" --version >/dev/null 2>&1 || die "$bin/r4 --version failed"
    "$bin/umrs" --help >/dev/null 2>&1 || die "$bin/umrs --help failed"
    ok "$("$bin/r4" --version) installed ($bin/r4, $bin/umrs)"
    install_console "$bin" "$tag"
    release_hints "$bin"
    ok "Run 'r4 onboard' to get started."
}

# ── Source install (fallback; unchanged behaviour) ─────────────────────────

install_from_source() {
    local install_dir="${R4_INSTALL_DIR:-$HOME/.r4}" branch="${R4_BRANCH:-main}"
    local repo_url="${R4_REPO_URL:-https://github.com/R-CCS-HPBD/r4.git}"
    local venv_dir="$install_dir/venv" bin_dir="$HOME/.local/bin" src_dir="$install_dir/src"
    local py_version

    command -v python3 >/dev/null 2>&1 || die "python3 is required"
    command -v git     >/dev/null 2>&1 || die "git is required"
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
        die "Python 3.10+ required (found $py_version)"

    info "Installing R4 CLI from source (Python $py_version, branch $branch)"
    if [ -d "$src_dir/.git" ]; then
        info "Updating existing clone..."
        git -C "$src_dir" fetch origin "$branch" --depth 1
        git -C "$src_dir" checkout FETCH_HEAD --force
    else
        info "Cloning R4 ($repo_url)..."
        mkdir -p "$install_dir"
        git clone --depth 1 --branch "$branch" "$repo_url" "$src_dir"
    fi

    # The umrs client lives in the umrs submodule (UMRS-org/umrs, ADR-010). A
    # plain clone leaves umrs/ empty and the umrs[client] install below fails.
    # The public repositories (R-CCS-HPBD/r4, UMRS-org/umrs) clone without
    # credentials; an R4_REPO_URL naming the private development repository
    # needs a credential helper or `gh auth login` for its HTTPS clone.
    info "Fetching the umrs submodule..."
    git -C "$src_dir" submodule sync --quiet
    git -C "$src_dir" -c 'url.https://github.com/.insteadOf=git@github.com:' \
        submodule update --init --depth 1 umrs

    if [ ! -d "$venv_dir" ]; then
        info "Creating virtual environment..."
        python3 -m venv "$venv_dir"
    fi
    info "Installing client packages..."
    "$venv_dir/bin/pip" install --upgrade pip --quiet
    # Dependency order (mirrors `make install-client`: r4 depends on
    # r4-teamengine-spi and science, workspace packages that are not on PyPI).
    "$venv_dir/bin/pip" install -e "$src_dir/r4-contracts" --quiet
    "$venv_dir/bin/pip" install -e "$src_dir/packages/r4-teamengine-spi" --quiet
    "$venv_dir/bin/pip" install -e "$src_dir/science" --quiet
    "$venv_dir/bin/pip" install -e "$src_dir/r4" --quiet
    # Lightweight client from the umrs monorepo (no pyzmq/boto3/server deps).
    # Use umrs[client-full] for direct ZMQ + S3 transport.
    "$venv_dir/bin/pip" install -e "$src_dir/umrs/src[client]" --quiet

    mkdir -p "$bin_dir"
    ln -sf "$venv_dir/bin/r4" "$bin_dir/r4"
    ln -sf "$venv_dir/bin/umrs" "$bin_dir/umrs"
    ln -sf "$venv_dir/bin/umrs" "$bin_dir/umrs-client"  # backwards-compatible alias

    "$venv_dir/bin/r4" --help >/dev/null 2>&1 || die "r4 --help failed"
    ok "r4 installed successfully"
    "$venv_dir/bin/umrs" --help >/dev/null 2>&1 || die "umrs --help failed"
    ok "umrs installed successfully"
    path_hints "$bin_dir"
    ok "Run 'r4 onboard' to get started."
}

# ── Main ───────────────────────────────────────────────────────────────────

main() {
    local tag
    if [ "${R4_FROM_SOURCE:-0}" = "1" ]; then
        install_from_source
        return 0
    fi
    WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/r4-install.XXXXXX")
    tag=$(resolve_tag)
    if [ -z "$tag" ]; then
        warn "No release of $R4_RELEASE_REPO publishes a client bundle yet;"
        warn "falling back to the source install (git clone + venv in ~/.r4)."
        install_from_source
        return 0
    fi
    install_release "$tag"
}

main "$@"
