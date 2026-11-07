#!/usr/bin/env bash

# ============================================================================
# Build, install, and publish the R4 Platform VS Code extension.
#
# The default action is build + package only. Installing and publishing are
# opt-in, so the script is safe in a plain development loop and only demands
# credentials when it actually needs them.
#
#   ./scripts/publish-vscode-extension.sh                  # build + package
#   ./scripts/publish-vscode-extension.sh --install        # + install locally
#   ./scripts/publish-vscode-extension.sh --publish        # + publish to both
#   ./scripts/publish-vscode-extension.sh --all --bump patch
# ============================================================================

set -euo pipefail

# ── Path resolution (symlink-safe) ──────────────────────────────────────────

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    LINK_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$LINK_DIR/$SOURCE"
done
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
EXT_DIR="$(cd -P "$SCRIPT_DIR/../vscode-extension" 2>/dev/null && pwd || echo "$SCRIPT_DIR/../vscode-extension")"
WEBVIEW_DIR="$EXT_DIR/webview-ui"
readonly SCRIPT_NAME SCRIPT_DIR EXT_DIR WEBVIEW_DIR

# ── Configuration (override via environment) ────────────────────────────────

readonly VSCE_SPEC="${VSCE_SPEC:-@vscode/vsce@^3}"
readonly OVSX_SPEC="${OVSX_SPEC:-ovsx@^0.10}"
readonly VSCODE_CLI="${VSCODE_CLI:-code}"
readonly KEEP_VSIX="${KEEP_VSIX:-5}"

# ── Defaults ────────────────────────────────────────────────────────────────

DO_INSTALL=false
DO_PUBLISH=false
REGISTRY="both"
BUMP=""
RUN_TESTS=true
RUN_LINT=true
DRY_RUN=false
FRESH_DEPS=false
PRE_RELEASE=false
ASSUME_YES=false
PKG_NAME=""
PUBLISHER=""
VERSION=""
VSIX=""

# ── Output helpers ──────────────────────────────────────────────────────────

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

on_error() {
    printf '\033[1;31m[error]\033[0m %s failed at line %s\n' "$SCRIPT_NAME" "$1" >&2
}
trap 'on_error "$LINENO"' ERR

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Builds a VSIX from vscode-extension/. Installing and publishing are opt-in.

Actions:
  --install              Install the built VSIX into the local VS Code
  --publish              Publish to VS Code Marketplace and Open VSX
  --marketplace-only     Publish to the VS Code Marketplace only
  --ovsx-only            Publish to Open VSX only
  --all                  Build, install locally, then publish to both

Build options:
  --bump <patch|minor|major>  Bump package.json version before building
  --fresh                     Reinstall node_modules (extension + webview)
  --skip-tests                Skip 'npm run test:unit'
  --skip-lint                 Skip 'npm run lint'
  --pre-release               Mark the release as a pre-release

Other:
  --dry-run              Build and verify, but never install or publish
  -y, --yes              Do not prompt for confirmation
  -h, --help             Show this help

Credentials (only required for the matching publish target):
  VSCE_PAT               VS Code Marketplace personal access token
  OVSX_PAT               Open VSX personal access token

Environment overrides:
  VSCODE_CLI (default: code), VSCE_SPEC, OVSX_SPEC, KEEP_VSIX (default: 5)
EOF
}

# ── Argument parsing ────────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install)          DO_INSTALL=true; shift ;;
            --publish)          DO_PUBLISH=true; REGISTRY="both"; shift ;;
            --marketplace-only) DO_PUBLISH=true; REGISTRY="marketplace"; shift ;;
            --ovsx-only)        DO_PUBLISH=true; REGISTRY="ovsx"; shift ;;
            --all)              DO_INSTALL=true; DO_PUBLISH=true; REGISTRY="both"; shift ;;
            --bump)
                [[ $# -ge 2 ]] || die "--bump requires an argument"
                case "$2" in
                    patch|minor|major) BUMP="$2" ;;
                    *) die "--bump must be one of: patch, minor, major" ;;
                esac
                shift 2
                ;;
            --fresh)        FRESH_DEPS=true; shift ;;
            --skip-tests)   RUN_TESTS=false; shift ;;
            --skip-lint)    RUN_LINT=false; shift ;;
            --pre-release)  PRE_RELEASE=true; shift ;;
            --dry-run)      DRY_RUN=true; shift ;;
            -y|--yes)       ASSUME_YES=true; shift ;;
            -h|--help)      usage; exit 0 ;;
            *)              usage >&2; die "Unknown option: $1" ;;
        esac
    done
}

# ── Preflight ───────────────────────────────────────────────────────────────

read_pkg() {
    node -p "require('./package.json').$1"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

preflight() {
    log "Preflight"

    [[ -d "$EXT_DIR" ]] || die "Extension directory not found: $EXT_DIR"
    cd "$EXT_DIR"
    [[ -f package.json ]] || die "No package.json in $EXT_DIR"
    # The sidebar agent view is a nested React/Vite project bundled into
    # dist/webview/. Without it, 'npm run package' cannot complete.
    [[ -f "$WEBVIEW_DIR/package.json" ]] || die "No package.json in $WEBVIEW_DIR"

    require_cmd node
    require_cmd npm
    require_cmd npx

    local node_major
    node_major="$(node -p 'process.versions.node.split(".")[0]')"
    if (( node_major < 20 )); then
        die "Node.js >= 20 required by @vscode/vsce (found $(node -v))"
    fi

    PKG_NAME="$(read_pkg name)"
    PUBLISHER="$(read_pkg publisher)"
    [[ -n "$PKG_NAME" && "$PKG_NAME" != "undefined" ]] || die "package.json is missing 'name'"
    [[ -n "$PUBLISHER" && "$PUBLISHER" != "undefined" ]] || die "package.json is missing 'publisher'"

    if [[ "$DO_INSTALL" == true && "$DRY_RUN" == false ]]; then
        command -v "$VSCODE_CLI" >/dev/null 2>&1 || die \
            "VS Code CLI '$VSCODE_CLI' not found. Run 'Shell Command: Install code command in PATH' or set VSCODE_CLI."
    fi

    if [[ "$DO_PUBLISH" == true && "$DRY_RUN" == false ]]; then
        if [[ "$REGISTRY" == "both" || "$REGISTRY" == "marketplace" ]]; then
            [[ -n "${VSCE_PAT:-}" ]] || die "VSCE_PAT is not set (needed to publish to the VS Code Marketplace)"
        fi
        if [[ "$REGISTRY" == "both" || "$REGISTRY" == "ovsx" ]]; then
            [[ -n "${OVSX_PAT:-}" ]] || die "OVSX_PAT is not set (needed to publish to Open VSX)"
        fi
    fi

    info "Extension: $PUBLISHER.$PKG_NAME"
    info "Node:      $(node -v)"
    info "Directory: $EXT_DIR"
    info "Webview:   $WEBVIEW_DIR"
}

confirm_publish() {
    [[ "$DO_PUBLISH" == true && "$DRY_RUN" == false ]] || return 0
    [[ "$ASSUME_YES" == true ]] && return 0

    if command -v git >/dev/null 2>&1 && git -C "$EXT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        if [[ -n "$(git -C "$EXT_DIR" status --porcelain 2>/dev/null)" ]]; then
            warn "vscode-extension/ has uncommitted changes — publishing an uncommitted build."
        fi
    fi

    printf '\nPublish %s v%s to: %s\n' "$PUBLISHER.$PKG_NAME" "$VERSION" "$REGISTRY"
    local reply
    read -r -p "Continue? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "Aborted by user"
}

# ── Build ───────────────────────────────────────────────────────────────────

# Install one npm project, preferring the reproducible 'npm ci' when a lockfile
# is present. Reinstalls only when the caller forces it or the tree looks stale.
install_project_deps() {
    local label="$1" dir="$2"
    local lock="$dir/package-lock.json"
    local installer="ci"
    [[ -f "$lock" ]] || installer="install"

    if [[ "$FRESH_DEPS" == true ]]; then
        info "$label: running 'npm $installer' (--fresh)"
    elif [[ ! -d "$dir/node_modules" ]]; then
        info "$label: node_modules missing — running 'npm $installer'"
    elif [[ -f "$lock" && "$lock" -nt "$dir/node_modules" ]]; then
        info "$label: package-lock.json is newer than node_modules — running 'npm $installer'"
    else
        info "$label: node_modules is up to date"
        return 0
    fi

    ( cd "$dir" && npm "$installer" )
}

ensure_deps() {
    log "Dependencies"
    install_project_deps "extension" "$EXT_DIR"
    install_project_deps "webview" "$WEBVIEW_DIR"
}

bump_version() {
    [[ -n "$BUMP" ]] || return 0
    log "Version bump ($BUMP)"
    npm version "$BUMP" --no-git-tag-version >/dev/null
    info "New version: $(read_pkg version)"
}

verify() {
    log "Verify"

    info "Type check (extension)"
    npm run check-types

    # Fail here rather than deep inside 'vsce package', where the webview is
    # rebuilt as part of vscode:prepublish.
    info "Type check (webview)"
    npm run webview:check-types

    if [[ "$RUN_LINT" == true ]]; then
        info "Lint"
        npm run lint
    else
        warn "Lint skipped"
    fi

    if [[ "$RUN_TESTS" == true ]]; then
        info "Unit tests"
        npm run test:unit
    else
        warn "Unit tests skipped"
    fi
}

build_vsix() {
    log "Package VSIX"

    VERSION="$(read_pkg version)"
    VSIX="$EXT_DIR/$PKG_NAME-$VERSION.vsix"

    # Rebuild from scratch so a stale artifact can never be installed/published.
    rm -f "$VSIX"

    local args=(package --out "$VSIX")
    [[ "$PRE_RELEASE" == true ]] && args+=(--pre-release)
    # vsce refuses to package without a LICENSE file unless told otherwise.
    if ! compgen -G 'LICENSE*' >/dev/null; then
        warn "No LICENSE file found — packaging with --skip-license"
        args+=(--skip-license)
    fi

    # 'vsce package' runs the 'vscode:prepublish' script, which builds the
    # React webview into dist/webview/ and then produces the production
    # esbuild bundle. Do not build separately — that doubles the work and
    # risks shipping a different bundle than the one just verified.
    npx --yes "$VSCE_SPEC" "${args[@]}"

    verify_vsix
}

verify_vsix() {
    [[ -f "$VSIX" ]] || die "Packaging reported success but $VSIX does not exist"
    [[ -s "$VSIX" ]] || die "Packaged VSIX is empty: $VSIX"

    if command -v unzip >/dev/null 2>&1; then
        unzip -tqq "$VSIX" >/dev/null 2>&1 || die "VSIX archive is corrupt: $VSIX"

        local listing main entry
        listing="$(unzip -l "$VSIX")"

        main="$(read_pkg main)"          # e.g. ./dist/extension.js
        entry="extension/${main#./}"
        printf '%s' "$listing" | grep -qF "$entry" \
            || die "VSIX is missing its entry point '$entry' — check .vscodeignore"

        # The agent sidebar renders from these; a stale or skipped webview
        # build would otherwise ship an extension with a blank view.
        local asset
        for asset in extension/dist/webview/assets/index.js \
                     extension/dist/webview/assets/index.css; do
            printf '%s' "$listing" | grep -qF "$asset" \
                || die "VSIX is missing webview asset '$asset' — run 'npm run webview:build'"
        done
    else
        warn "unzip not available — skipping VSIX integrity check"
    fi

    info "Built: $(basename "$VSIX") ($(du -h "$VSIX" | cut -f1))"
}

prune_old_vsix() {
    (( KEEP_VSIX > 0 )) || return 0
    [[ "$DRY_RUN" == false ]] || return 0
    local stale
    # Newest first; delete everything past the retention window.
    # shellcheck disable=SC2012  # VSIX names are version strings, no odd chars
    stale="$(ls -1t "$PKG_NAME"-*.vsix 2>/dev/null | tail -n "+$((KEEP_VSIX + 1))" || true)"
    [[ -n "$stale" ]] || return 0

    log "Prune old VSIX artifacts (keeping $KEEP_VSIX)"
    local f
    while IFS= read -r f; do
        [[ -n "$f" ]] || continue
        info "rm $f"
        rm -f -- "$f"
    done <<<"$stale"
}

# ── Install ─────────────────────────────────────────────────────────────────

# Remove extensions that share this extension's name but come from a different
# publisher. VS Code lets both register the same view IDs and commands, and the
# stale copy can silently win — producing UI errors that look like backend bugs.
remove_conflicting_extensions() {
    local installed conflict
    installed="$("$VSCODE_CLI" --list-extensions 2>/dev/null || true)"
    [[ -n "$installed" ]] || return 0

    while IFS= read -r conflict; do
        [[ -n "$conflict" ]] || continue
        warn "Removing conflicting extension: $conflict (duplicates $PUBLISHER.$PKG_NAME)"
        "$VSCODE_CLI" --uninstall-extension "$conflict" >/dev/null 2>&1 \
            || warn "Could not uninstall $conflict — remove it manually"
    done < <(printf '%s\n' "$installed" \
        | grep -iE "^[^.]+\.${PKG_NAME}\$" \
        | grep -ivE "^${PUBLISHER}\.${PKG_NAME}\$" || true)
}

install_vsix() {
    [[ "$DO_INSTALL" == true ]] || return 0

    log "Install locally"
    if [[ "$DRY_RUN" == true ]]; then
        info "Dry run — would install $(basename "$VSIX")"
        return 0
    fi

    remove_conflicting_extensions

    "$VSCODE_CLI" --install-extension "$VSIX" --force \
        || die "Installation failed for $VSIX"

    local active count
    active="$("$VSCODE_CLI" --list-extensions --show-versions 2>/dev/null \
        | grep -iE "^[^.]+\.${PKG_NAME}@" || true)"
    info "Installed: ${active:-unknown}"

    count="$(printf '%s\n' "$active" | grep -c . || true)"
    if (( count > 1 )); then
        warn "More than one copy of '$PKG_NAME' is still registered — reload VS Code and re-check."
    fi
    info "Reload VS Code (Developer: Reload Window) to activate the new build."
}

# ── Publish ─────────────────────────────────────────────────────────────────

publish_marketplace() {
    log "Publish to VS Code Marketplace"
    if [[ "$DRY_RUN" == true ]]; then
        info "Dry run — would publish $(basename "$VSIX")"
        return 0
    fi
    local args=(publish --packagePath "$VSIX" --skip-duplicate --pat "$VSCE_PAT")
    [[ "$PRE_RELEASE" == true ]] && args+=(--pre-release)
    npx --yes "$VSCE_SPEC" "${args[@]}"
}

publish_ovsx() {
    log "Publish to Open VSX"
    if [[ "$DRY_RUN" == true ]]; then
        info "Dry run — would publish $(basename "$VSIX")"
        return 0
    fi
    local args=(publish --packagePath "$VSIX" --skip-duplicate --pat "$OVSX_PAT")
    [[ "$PRE_RELEASE" == true ]] && args+=(--pre-release)
    npx --yes "$OVSX_SPEC" "${args[@]}"
}

publish_all() {
    [[ "$DO_PUBLISH" == true ]] || return 0
    case "$REGISTRY" in
        both)        publish_marketplace; publish_ovsx ;;
        marketplace) publish_marketplace ;;
        ovsx)        publish_ovsx ;;
    esac
}

summary() {
    log "Done"
    info "Artifact: $VSIX"
    info "Version:  $PUBLISHER.$PKG_NAME v$VERSION"
    if [[ "$DO_PUBLISH" == true && "$DRY_RUN" == false ]]; then
        if [[ "$REGISTRY" == "both" || "$REGISTRY" == "marketplace" ]]; then
            info "https://marketplace.visualstudio.com/items?itemName=$PUBLISHER.$PKG_NAME"
        fi
        if [[ "$REGISTRY" == "both" || "$REGISTRY" == "ovsx" ]]; then
            info "https://open-vsx.org/extension/$PUBLISHER/$PKG_NAME"
        fi
    elif [[ "$DO_PUBLISH" == false ]]; then
        info "Not published. Re-run with --publish (or --all) when ready."
    fi
}

main() {
    parse_args "$@"
    preflight
    ensure_deps
    bump_version
    verify
    build_vsix
    confirm_publish
    install_vsix
    publish_all
    prune_old_vsix
    summary
}

main "$@"
