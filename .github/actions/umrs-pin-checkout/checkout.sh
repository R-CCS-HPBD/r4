#!/usr/bin/env bash
# Check out umrs/protocol at the recorded umrs pin, anonymously (U2.0, ADR-117 D-2).
#
# For jobs that install umrs-protocol from the checkout but never fetched the
# umrs submodule. UMRS-org/umrs is public: plain HTTPS, no token, no SSH key,
# and no credential helper. The fetch is by sha, the same mechanism as the
# submodule-lineage job in ci.yaml.
#
# Inputs (environment, all optional):
#   UMRS_PIN     the commit to check out (default: `git rev-parse HEAD:umrs`)
#   UMRS_DIR     where the submodule lives (default: umrs)
#   UMRS_URL     the remote (default: https://github.com/UMRS-org/umrs.git)
#   UMRS_SPARSE  1 = only protocol/ in the working tree. The composite sets it
#                on GitHub-hosted runners only, unless its `tree` input says
#                otherwise (test-r4's hosted fallback passes `tree: full`
#                because it also installs ./umrs/src). A self-hosted workspace is
#                reused by later jobs, and boundary-check accepts any tree at
#                the pin, so a sparse tree left there would hide umrs/src.
#
# A tree already at the pin with protocol/ is reused as it is. An existing
# clone at another commit is moved to the pin, keeping its sparse setting.
set -euo pipefail

dir="${UMRS_DIR:-umrs}"
url="${UMRS_URL:-https://github.com/UMRS-org/umrs.git}"
pin="${UMRS_PIN:-$(git rev-parse HEAD:umrs)}"
export GIT_TERMINAL_PROMPT=0

# The runner's global config may rewrite https://github.com/ to SSH (the test
# jobs set url.git@github.com:.insteadOf). Git applies the longest matching
# insteadOf, so mapping the full URL to itself keeps the fetch anonymous HTTPS.
anon_git() {
    git -c credential.helper= -c "url.${url}.insteadOf=${url}" "$@"
}

at_pin() {
    [ -e "$dir/.git" ] || return 1
    [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo none)" = "$pin" ] || return 1
    [ -f "$dir/protocol/pyproject.toml" ]
}

fresh_repo() {
    rm -rf "$dir"
    mkdir -p "$dir"
    git -C "$dir" init -q
    if [ "${UMRS_SPARSE:-0}" = "1" ]; then
        git -C "$dir" sparse-checkout set protocol
    fi
}

if at_pin; then
    echo "umrs already at the pin ${pin}; reusing it"
    exit 0
fi
# `git -C umrs` without umrs/.git would run in the parent repository.
[ -e "$dir/.git" ] || fresh_repo
anon_git -C "$dir" fetch -q --depth 1 "$url" "$pin"
git -C "$dir" checkout -q --force --detach FETCH_HEAD
test "$(git -C "$dir" rev-parse HEAD)" = "$pin"
if [ ! -f "$dir/protocol/pyproject.toml" ]; then
    echo "::error title=umrs pin has no protocol/::umrs ${pin} holds no protocol/pyproject.toml (U2.0 needs U-A0 or later)"
    exit 1
fi
echo "umrs/protocol at the pin ${pin}"
