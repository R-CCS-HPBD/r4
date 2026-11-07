#!/usr/bin/env bash
# Propose a dependency refresh for review (ADR-100); run by deps-refresh.yaml
# after scripts/deps/refresh.sh, the lock tests and the image smoke test.
#
# Env (all set by the workflow):
#   BRANCH            parent branch to push, e.g. deps/refresh-2026-11
#   TITLE             PR title
#   OLD_LOCK          the uv.lock before the refresh (for the version list)
#   RUN_URL           this run's URL
#   GH_TOKEN          the workflow's GITHUB_TOKEN (push the branch, open the PR)
#   GITHUB_REPOSITORY owner/repo of the parent (set by Actions)
#   UMRS_PUSH_TOKEN   optional: a token that may push to UMRS-org/umrs. Unset,
#                     or when its push fails, the umrs half (umrs/src/uv.lock
#                     and the server-image locks) is left as a patch for the
#                     maintainer (a warning says why).
#   OUT_DIR           where the umrs patch goes (uploaded as an artifact)
#
# Never pushes to main or to UMRS-org ai4s-platform: the parent change goes to
# BRANCH and a PR, the umrs commit (with a token) to the umrs branch BRANCH.
# Tokens travel in an env-scoped git header (GIT_CONFIG_*), never in a URL,
# argv or .git/config (the checkout does not persist credentials).
# Prints `umrs=pushed|patch|unchanged` and `pr=<url>|issue=<url>` lines.
#
# Every step runs in this shell, never inside $(...) (bash turns errexit off
# there): a failed commit stops the run; a failed umrs push becomes a patch.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
bot=(-c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com")
umrs_git() { env -u GIT_DIR -u GIT_WORK_TREE git -C umrs "$@"; }
UMRS_STATE=unchanged

# git with "AUTHORIZATION: basic x-access-token:<token>" for github.com, for
# this one command only: authed_git <token> <git args...>
authed_git() {
  local auth
  auth=$(printf 'x-access-token:%s' "$1" | base64 | tr -d '\n')
  shift
  GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \
    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${auth}" "$@"
}
PARENT_PATHS=(uv.lock pyproject.toml r4/docker gateway/sidecar/requirements.lock.txt
  deploy/ansible/roles/front_end/files/assistant-frontend.requirements.lock.txt)
UMRS_PATHS=(src/uv.lock docker/server-image)

umrs_changed() { [ -n "$(umrs_git status --porcelain -- "${UMRS_PATHS[@]}")" ]; }
parent_changed() { [ -n "$(git status --porcelain -- "${PARENT_PATHS[@]}")" ]; }

commit_umrs() {
  umrs_git add -- "${UMRS_PATHS[@]}"
  umrs_git "${bot[@]}" commit -q -m "chore(deps): ${TITLE} (umrs half; ADR-100)" -m "Run: ${RUN_URL}"
}

# Called as an `if` condition (errexit off inside): every step checks itself.
push_umrs() {
  authed_git "$UMRS_PUSH_TOKEN" umrs_git push -q https://github.com/UMRS-org/umrs.git \
    "HEAD:refs/heads/${BRANCH}" || return 1
  git add umrs || return 1
  UMRS_STATE="pushed"
}

save_umrs_patch() {
  mkdir -p "$OUT_DIR"
  umrs_git format-patch -1 -q -o "$OUT_DIR" HEAD
  UMRS_STATE="patch"
}

umrs_half() {
  umrs_changed || return 0
  commit_umrs
  if [ -n "${UMRS_PUSH_TOKEN:-}" ]; then
    if push_umrs; then return 0; fi
    echo "::warning::the umrs push to UMRS-org/umrs ${BRANCH} failed: the umrs half is left as a patch" >&2
  fi
  save_umrs_patch
}

write_body() {
  local umrs_state="$1" body="$2"
  {
    echo "Dependency refresh by ${RUN_URL} (ADR-100). Lock tests and the image smoke test passed in that run."
    echo
    grep -E '^(exclude-newer|# [0-9]+-day)' pyproject.toml | sed 's/^/    /' || true
    echo
    echo "### Packages that moved (uv.lock)"
    echo '```'
    python3 scripts/deps/lockctl.py changes "$OLD_LOCK"
    echo '```'
    maintainer_legs "$umrs_state"
  } > "$body"
}

maintainer_legs() {
  echo "### Maintainer legs"
  echo "- CI has not run on this PR (a PR opened by GITHUB_TOKEN starts no workflows): push to the branch, or close and reopen it."
  case "$1" in
    pushed) echo "- The umrs half is on UMRS-org branch \`${BRANCH}\`. Before merging, put it on ai4s-platform (lineage check): \`git -C umrs fetch upstream ${BRANCH} && git -C umrs push upstream FETCH_HEAD:ai4s-platform && git -C umrs push origin FETCH_HEAD:ai4s-platform\`." ;;
    patch) echo "- The umrs half (umrs/src/uv.lock, umrs/docker/server-image/) is NOT in this PR: no UMRS_LOCK_PUSH_TOKEN, or its push failed (run log). Until it is, the lock check fails (server-image lock vs uv.lock). Download the \`umrs-refresh-patch\` artifact of the run, then: \`git -C umrs checkout ai4s-platform && git -C umrs am <patch> && git -C umrs push upstream HEAD:ai4s-platform && git -C umrs push origin HEAD:ai4s-platform\`; \`git checkout ${BRANCH} && git add umrs && git commit -m 'chore(deps): bump umrs to the refreshed locks' && git push\`." ;;
    patch-only) echo "- Only the umrs half moved, so there is no parent branch or PR. Download the \`umrs-refresh-patch\` artifact of the run, then: \`git -C umrs checkout ai4s-platform && git -C umrs am <patch> && git -C umrs push upstream HEAD:ai4s-platform && git -C umrs push origin HEAD:ai4s-platform\`; on a new branch from main: \`git add umrs && git commit -m 'chore(deps): bump umrs to the refreshed locks'\`, push it and open a PR." ;;
    *) echo "- No umrs change." ;;
  esac
  echo "- Deploy is the usual converge check, then apply; the converge build runs the same smoke test (Dockerfile.server)."
}

open_issue() {
  local url
  url=$(gh issue create --title "$TITLE: apply the umrs patch" --body-file "$1")
  echo "issue=${url}"
}

open_pr() {
  local body="$1" url
  if url=$(gh pr create --base main --head "$BRANCH" --title "$TITLE" --body-file "$body" 2>/dev/null); then
    echo "pr=${url}"
    return
  fi
  # Repository setting "Allow GitHub Actions to create pull requests" is off.
  url=$(gh issue create --title "$TITLE: open the PR from ${BRANCH}" --body-file "$body")
  echo "issue=${url}"
}

main() {
  if ! umrs_changed && ! parent_changed; then echo "nothing moved: no PR"; return; fi
  local body="${OUT_DIR}/pr-body.md"
  mkdir -p "$OUT_DIR"
  umrs_half
  echo "umrs=${UMRS_STATE}"
  git add -- "${PARENT_PATHS[@]}"
  if git diff --cached --quiet; then
    write_body patch-only "$body"
    open_issue "$body"
    return
  fi
  git "${bot[@]}" commit -q -m "chore(deps): ${TITLE} (ADR-100)" -m "Run: ${RUN_URL}"
  authed_git "$GH_TOKEN" git push -q "https://github.com/${GITHUB_REPOSITORY}.git" "HEAD:refs/heads/${BRANCH}"
  write_body "$UMRS_STATE" "$body"
  open_pr "$body"
}

main
