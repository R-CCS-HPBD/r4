#!/usr/bin/env bash
# Install the tracked git hooks (scripts/git-hooks/*) as symlinks (plan H-3.4).
#
#   bash scripts/install-hooks.sh          # link every tracked hook
#   bash scripts/install-hooks.sh --force  # also replace a hook that is a
#                                          # regular file (kept as <hook>.local-backup)
#
# Idempotent: a hook already linked to its tracked script is left alone. A
# hook that is a regular file (a hand-made local hook, or `pre-commit install`)
# is never overwritten without --force. Hooks live in the shared git dir, so
# one run covers the main checkout and every linked worktree; the links point
# at the main checkout's scripts/git-hooks/. Run from a worktree whose hooks
# differ from the main checkout's, it refuses and names them.
set -euo pipefail

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "install-hooks: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

hooks_path_setting="$(git config --get core.hooksPath || true)"
if [ -n "$hooks_path_setting" ]; then
    echo "install-hooks: core.hooksPath is set to '$hooks_path_setting'; git reads hooks" >&2
    echo "  from there, not from .git/hooks. Unset it (git config --unset core.hooksPath)" >&2
    echo "  and run this again." >&2
    exit 1
fi

common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
if [ "$(basename "$common_dir")" != ".git" ]; then
    echo "install-hooks: unsupported layout (git dir $common_dir is not <checkout>/.git)" >&2
    exit 1
fi
main_root="$(dirname "$common_dir")"
hooks_dir="$common_dir/hooks"
source_dir="$main_root/scripts/git-hooks"

# Run from a linked worktree, the links still point at the MAIN checkout's
# scripts/git-hooks/. If this worktree's branch carries a hook the main
# checkout lacks (or a different version of one), installing would silently
# leave the old hook live: refuse and say so (2026-10-18).
check_worktree_hooks() {
    local here mismatch=0 script name
    here="$(git rev-parse --show-toplevel)"
    [ "$here" != "$main_root" ] && [ -d "$here/scripts/git-hooks" ] || return 0
    for script in "$here/scripts/git-hooks"/*; do
        [ -f "$script" ] && [ -x "$script" ] || continue
        name="$(basename "$script")"
        if [ ! -e "$source_dir/$name" ]; then
            echo "mismatch  $name exists in this worktree but not in the main checkout" \
                "$main_root; merge it there first" >&2
            mismatch=1
        elif ! cmp -s "$script" "$source_dir/$name"; then
            echo "mismatch  $name differs between this worktree and the main checkout" \
                "$main_root; the hook would run the main checkout's copy; merge first" >&2
            mismatch=1
        fi
    done
    return "$mismatch"
}
if ! check_worktree_hooks; then
    echo "install-hooks: nothing installed" >&2
    exit 1
fi

mkdir -p "$hooks_dir"

install_one() {
    local name="$1"
    local hook="$hooks_dir/$name"
    local target="../../scripts/git-hooks/$name"
    if [ -L "$hook" ] && [ "$(readlink "$hook")" = "$target" ]; then
        echo "ok        $name (already linked to scripts/git-hooks/$name)"
        return 0
    fi
    if [ -e "$hook" ] && [ ! -L "$hook" ]; then
        if [ "$FORCE" -ne 1 ]; then
            echo "refused   $name: $hook is a regular file; rerun with --force to replace it" \
                "(it is kept as $name.local-backup)" >&2
            return 1
        fi
        local backup="$hook.local-backup"
        [ ! -e "$backup" ] || backup="$backup.$(date +%Y%m%d%H%M%S)"
        mv "$hook" "$backup"
        echo "backup    $name -> $backup"
    elif [ -L "$hook" ]; then
        echo "relink    $name (was -> $(readlink "$hook"))"
    fi
    ln -sfn "$target" "$hook"
    echo "linked    $name -> scripts/git-hooks/$name"
}

status=0
found=0
for script in "$source_dir"/*; do
    [ -f "$script" ] && [ -x "$script" ] || continue
    found=1
    install_one "$(basename "$script")" || status=1
done
if [ "$found" -eq 0 ]; then
    echo "install-hooks: no hooks in $source_dir (the main checkout must contain" \
        "scripts/git-hooks/; merge it there first)" >&2
    exit 1
fi
exit "$status"
