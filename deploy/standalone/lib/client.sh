# shellcheck shell=bash
# The r4 client side of the stand-alone stack (2026-10-15): install it from
# this checkout, register the stack as an r4 source, and prove the stack
# works with one chat, one ingest and one retrieval.
#
# Registration is decision D-5 of docs/development/onboarding-plan-2026-10-15.md:
# opt-in (`register`, which `quickstart` runs), a 30-day token re-minted on
# every quickstart, and the user's ACTIVE source and login are never touched
# — no `r4 source use`, no `r4 auth login`. The token goes from the umrs
# container to `r4 source add --api-key-stdin` through a pipe: never an
# argument, never a temporary file, never printed. r4 then keeps it in the
# user's own r4 and umrs config (sources.json and the umrs connection
# profile's token file, both 0600), like any source's key.
#
# Registration also makes the stack's project a server row (2026-10-16),
# through the gateway as any client would: the deployment's default data
# class applies only to a row, and `r4 project data-class` needs one.
#
# Needs: lib/common.sh, and from standalone.sh: HERE, compose, cfg_get,
# detect_engine, load_env, profile_args.

REPO_ROOT="$(cd "${HERE:?}/../.." && pwd)"
SOURCE_NAME="${STANDALONE_SOURCE_NAME:-standalone}"
STACK_URL="http://localhost:8080"
# The stack's own project: the one `up` registers the chat model in.
STACK_PROJECT="default"
SAMPLE_FILE="${HERE}/sample/r4-local-sample.md"
SAMPLE_QUESTION="What damps the waves inside the Kestrel Bay tide gauge?"
SMOKE_TIMEOUT="${STANDALONE_SMOKE_TIMEOUT:-900}"
# How long an artifact an earlier run uploaded gets to show its vectors
# before the sample is uploaded again (an ingest that never finished).
SMOKE_EARLIER_WAIT="${STANDALONE_SMOKE_EARLIER_WAIT:-60}"
UV_INSTALL_LINE="curl -LsSf https://astral.sh/uv/install.sh | sh"

r4_bin() { printf '%s' "${R4_BIN:-${REPO_ROOT}/.venv/bin/r4}"; }

# ── install ───────────────────────────────────────────────────────────────
# The client from this checkout, the way `make install-client` does it with
# uv. --inexact: an existing .venv keeps every package a developer installed
# into it (`make install` adds the server extras); uv only adds.
install_client() {
  local r4; r4="$(r4_bin)"
  if [[ -x "$r4" ]]; then ok "r4 client: ${r4}"; return 0; fi
  command -v uv >/dev/null 2>&1 || die "the r4 client is installed with uv, which is not on PATH. Install it:
   ${UV_INSTALL_LINE}
   then open a new terminal and run ./r4-local up again."
  info "installing the r4 client into ${REPO_ROOT}/.venv (uv sync --all-packages --extra client --inexact)"
  (cd "$REPO_ROOT" && uv sync --all-packages --extra client --inexact) || die "uv sync failed (see above)"
  if command -v make >/dev/null 2>&1; then
    make -C "$REPO_ROOT" --no-print-directory tui-deps \
      || warn "the console's dependencies did not install; r4 console falls back to the release binary (make tui-deps)"
  fi
  [[ -x "$r4" ]] || die "uv sync finished but ${r4} does not exist"
  ok "r4 client installed: ${r4}"
}

# ── register ──────────────────────────────────────────────────────────────
# r4 against this stack, for this one process only, exactly as the printed
# usage runs it: R4_SOURCE selects the source, whose own default model and
# project then apply. A gateway URL, key, model or project exported for
# another deployment is dropped so it cannot win over the source (r4 lets
# R4_MODEL and R4_PROJECT_ID override a source's).
r4_on_stack() {
  env -u R4_GATEWAY_URL -u R4_API_KEY -u R4_MODEL -u R4_PROJECT_ID \
    R4_SOURCE="$SOURCE_NAME" R4_TIMEOUT="${R4_TIMEOUT:-600}" "$(r4_bin)" "$@"
}

# The source the user's r4 config has active, ignoring R4_SOURCE; empty
# when none is.
stored_active_source() {
  env -u R4_SOURCE "$(r4_bin)" source list --json 2>/dev/null \
    | python3 -c 'import json, sys
rows = json.load(sys.stdin)
print(next((r["name"] for r in rows if r.get("active")), ""))' 2>/dev/null || true
}

# A token for the stack's single user, minted inside umrs, on stdout for
# the next process in the pipe only. Args: user project [days, default 30].
mint_client_token() {
  compose exec -T umrs umrs-server admin token generate-jwt --user "$1" --project "$2" \
    --days "${3:-30}" --profile "$SOURCE_NAME" --gateway-url "$STACK_URL" --json \
    </dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])'
}

# The stack's project as a server row: POST /gw/projects, the server's own
# idempotent create (an existing row comes back as it is, never rewritten).
# Before it, the stand-alone default class (UMRS_PROJECT_DEFAULT_DATA_CLASS
# in compose.yaml) applied to no row and `r4 project data-class … default`
# answered "project default not found". The token comes on stdin and goes
# to curl as a header read from stdin (-H @-): never an argument, a file or
# output, and never to a proxy. -q must stay curl's FIRST argument: it stops
# curl reading the user's .curlrc, where a `verbose` or `trace-ascii` line
# would print or write the header (2026-10-16). Prints "<data_class>
# <owner_id>" of the row. Args: project.
ensure_stack_project() {
  local body
  body="$(python3 -c 'import json, sys; print(json.dumps({"project_id": sys.argv[1]}))' "$1")"
  sed 's/^/Authorization: Bearer /' \
    | curl -q -sS -f --noproxy '*' -m 30 -H @- -H 'Content-Type: application/json' \
        -X POST --data "$body" "${STACK_URL}/gw/projects" \
    | python3 -c 'import json, sys
try:
    row = json.load(sys.stdin)["project"]
    print(row["data_class"], row["owner_id"])
except (ValueError, KeyError, TypeError):
    sys.exit("the gateway answered no project row")'
}

# What the stack's project may use, as the server's row says, and how to
# narrow it. The institutional list here is always the packaged one (RIKEN's
# Rikyu): compose passes no UMRS_INSTITUTIONAL_PROVIDER_HOSTS to umrs, and
# register never runs `r4 login`, which is how r4 learns a site's list.
# Args: r4 project data-class owner user.
project_class_note() {
  local r4="$1" project="$2" klass="$3" owner="$4" user="$5"
  if [[ "$klass" == non_sensitive ]]; then
    info "data class: project ${project} is non_sensitive (this stack's default for new projects), so any model source may serve it, commercial providers included. To keep its data on this machine and the institutional provider list (RIKEN's Rikyu), narrow it:  R4_SOURCE=${SOURCE_NAME} ${r4} project data-class set ${project} sensitive"
  else
    info "data class: project ${project} is ${klass}: only this stack's models, models on this machine and the institutional provider list (RIKEN's Rikyu) may serve it."
  fi
  [[ "$owner" == "$user" ]] \
    || warn "project ${project} belongs to '${owner}' (an earlier register made it); only '${owner}' can change its data class."
  return 0
}

# Add the stack as a platform source, or replace a row of the same name,
# reading the token from stdin. R4_SOURCE pins the active-source NAME for
# this process: `source add` makes the first source it stores the active one
# when none is (r4/_cli/source.py _commit_add), and get_active() reads
# R4_SOURCE first, so the stored active source stays as it was.
# Args: r4 model project.
add_stack_source() {
  env -u R4_GATEWAY_URL -u R4_API_KEY R4_SOURCE="$SOURCE_NAME" "$1" source add "$SOURCE_NAME" \
    --kind platform --url "$STACK_URL" --zmq-host 127.0.0.1 --zmq-port 5555 \
    --api-key-stdin --default-model "$2" --project "$3" --replace \
    --description "local stand-alone stack (deploy/standalone)"
}

cmd_register() {
  local user="${1:-scientist}" project="${2:-$STACK_PROJECT}" r4 model before after row klass owner
  r4="$(r4_bin)"
  [[ -x "$r4" ]] || die "no r4 client at ${r4}. ./r4-local up installs it from this checkout (or: make install-client)"
  detect_engine; load_env; profile_args
  compose exec -T umrs true >/dev/null 2>&1 || die "umrs is not running. Start the stack first: ./r4-local up"
  model="$(cfg_get STANDALONE_CHAT_MODEL)"
  before="$(stored_active_source)"
  mint_client_token "$user" "$project" | add_stack_source "$r4" "$model" "$project" >/dev/null \
    || die "registering the r4 source '${SOURCE_NAME}' failed (see above; the token is minted inside umrs: ./r4-local logs umrs)"
  after="$(stored_active_source)"
  [[ "$after" == "$before" ]] || die "your active r4 source changed from '${before:-none}' to '${after:-none}'; this is a bug. Restore it with: ${r4} source use ${before:-<name>}"
  ok "registered r4 source '${SOURCE_NAME}': ${STACK_URL}, project ${project}, model ${model}, a 30-day token for '${user}'"
  # ADR-062 amendment D-4 (2026-10-15): this deployment's NEW project rows
  # take data_class=non_sensitive (UMRS_PROJECT_DEFAULT_DATA_CLASS in
  # compose.yaml), so commercial providers' keys are allowed for them;
  # institutional providers (here always the packaged list: Rikyu) and
  # models on this machine serve sensitive projects too. The row is made first (a
  # one-day token, the same user and project), then its class is said from
  # what the server answered, with the narrowing command for that project.
  row="$(mint_client_token "$user" "$project" 1 | ensure_stack_project "$project")" \
    || die "creating project '${project}' on the stack failed (see above; the gateway's /gw/projects: ./r4-local logs gateway-nginx umrs)"
  read -r klass owner <<<"$row"
  project_class_note "$r4" "$project" "$klass" "$owner" "$user"
  info "your active r4 source is unchanged: ${before:-none}"
  [[ "${QS_QUIET_USAGE:-0}" == 1 ]] || print_stack_usage
}

# How to use the stack, with the client this checkout installed (a bare
# `r4` may be missing from PATH, or an older one). R4_SOURCE works for every
# r4 command; R4_MODEL or R4_PROJECT_ID exported in this shell would still
# override the source's model and project, so they are named.
print_stack_usage() {
  local r4; r4="$(r4_bin)"
  cat <<EOF
Use the stack; your active r4 source stays as it was.
  One command:     R4_SOURCE=${SOURCE_NAME} ${r4} chat "hello"
  This shell:      source ${REPO_ROOT}/.venv/bin/activate && export R4_SOURCE=${SOURCE_NAME}
                   then: r4 chat, r4 console, r4 knowledge query "..."
  As your default: ${r4} source use ${SOURCE_NAME}   (only if you want that)
EOF
  shell_overrides_note
}

shell_overrides_note() {
  local v
  for v in R4_MODEL R4_PROJECT_ID R4_GATEWAY_URL R4_API_KEY; do
    [[ -n "${!v:-}" ]] && warn "${v} is set in this shell and overrides the '${SOURCE_NAME}' source for every r4 command here; unset ${v} to use the stack's own."
  done
  return 0
}

# ── smoke test ────────────────────────────────────────────────────────────
# Evidence, never a status: the chat's own words, the knowledge row and its
# vectors in Postgres, and the query's cited source. A job that "completed"
# while writing nothing is exactly what this must not report as a pass.
SMOKE_ROWS=()

smoke_row() { SMOKE_ROWS+=("$1|$2|$3"); }

one_line() { printf '%s' "$1" | tr '\n\t' '  ' | cut -c1-90; }

# A chat answer, not an error message. Args: output.
chat_ok() {
  [[ -n "${1//[[:space:]]/}" ]] || return 1
  ! grep -qiE 'no active endpoint|^error|traceback|unauthorized|forbidden' <<<"$1"
}

# The query's sources name the sample. Args: output file-name.
query_cites() {
  local sources="${1#*--- Sources ---}"
  [[ "$sources" != "$1" ]] && grep -qF "$2" <<<"$sources"
}

stack_sql() {
  # shellcheck disable=SC2016  # expanded by the container's sh from its own env
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"' sh "$1" </dev/null 2>/dev/null
}

# Both print nothing (and succeed) when there is nothing yet, or when the
# query fails: a caller assigning their output must not die under set -e.
sample_artifact() {
  local out
  out="$(stack_sql "SELECT artifact_id FROM knowledge_artifacts WHERE project_id = '${STACK_PROJECT}' AND status = 'active' AND metadata->>'staged_from' LIKE '%/$1' LIMIT 1" || true)"
  [[ -n "$out" ]] && head -1 <<<"$out"
  return 0
}

sample_vectors() {
  local n
  n="$(stack_sql "SELECT count(*) FROM knowledge_node_embeddings e JOIN knowledge_nodes n ON n.node_id = e.node_id WHERE n.content->>'artifact_id' = '$1'" || true)"
  n="$(head -1 <<<"$n")"
  [[ "$n" =~ ^[0-9]+$ && "$n" != 0 ]] && printf '%s' "$n"
  return 0
}

# Poll FN ARG every 5 s until it prints something, for at most LIMIT s.
wait_for() {
  local fn="$1" arg="$2" limit="$3" waited=0 out
  while (( waited <= limit )); do
    out="$("$fn" "$arg")"
    [[ -n "$out" ]] && { printf '%s' "$out"; return 0; }
    sleep "${SMOKE_POLL:-5}"; waited=$((waited + ${SMOKE_POLL:-5}))
  done
  return 1
}

# The source's own default model and project, as `R4_SOURCE=standalone r4
# chat` uses them: no --model, so a PASS proves the registration too.
smoke_chat() {
  local model="$1" out what
  what="chat (the source's model ${model}, through the gateway)"
  if out="$(r4_on_stack chat "Reply with the single word: ready" </dev/null 2>&1)" && chat_ok "$out"; then
    smoke_row "$what" PASS "$(one_line "$out")"; return 0
  fi
  smoke_row "$what" FAIL "$(one_line "$out")"; return 1
}

# The sample's knowledge row and vectors. Identical bytes dedupe to the
# artifact the project already holds, so a later run checks that artifact
# instead of uploading again (and says so). An earlier artifact that shows
# no vectors within SMOKE_EARLIER_WAIT is an ingest that never finished
# (umrs restarted mid-chain, `down` during the wait): the sample is uploaded
# again, which repoints at the stored artifact and runs its chain again.
smoke_ingest() {
  local name art n what note=""
  name="$(basename "$SAMPLE_FILE")"; what="ingest (${name}: knowledge row + vectors)"
  art="$(sample_artifact "$name")"
  if [[ -n "$art" ]]; then
    if n="$(wait_for sample_vectors "$art" "$SMOKE_EARLIER_WAIT")"; then
      smoke_row "$what" PASS "artifact ${art}, ${n} vectors (ingested by an earlier run)"; return 0
    fi
    info "artifact ${art} (${name}, from an earlier run) has no vectors after ${SMOKE_EARLIER_WAIT}s: its ingest never finished, so the sample is uploaded again"
    note=" (re-ingested: an earlier run left it without vectors)"
  fi
  smoke_upload "$name" "$what" || return 1
  art="$SMOKE_ART"
  n="$(wait_for sample_vectors "$art" "$SMOKE_TIMEOUT")" \
    || { smoke_row "$what" FAIL "artifact ${art}: no vectors after ${SMOKE_TIMEOUT}s (./r4-local logs umrs)"; return 1; }
  smoke_row "$what" PASS "artifact ${art}, ${n} vectors${note}"
}

# Upload the sample; SMOKE_ART is its artifact id once its row exists. On
# a failure the FAIL row is recorded. Called directly, never in $(...):
# smoke_row must reach this shell's SMOKE_ROWS. Args: file-name what.
SMOKE_ART=""
smoke_upload() {
  local out
  SMOKE_ART=""
  out="$(r4_on_stack knowledge upload -p "$STACK_PROJECT" "$SAMPLE_FILE" </dev/null 2>&1)" \
    || { smoke_row "$2" FAIL "upload: $(one_line "$out")"; return 1; }
  info "uploaded $1; waiting for its knowledge row and vectors (up to ${SMOKE_TIMEOUT}s; extraction runs on the local model)"
  SMOKE_ART="$(wait_for sample_artifact "$1" 300)" \
    || { smoke_row "$2" FAIL "no knowledge_artifacts row after 300s ($(one_line "$out"))"; return 1; }
}

smoke_query() {
  local name out what
  name="$(basename "$SAMPLE_FILE")"; what="retrieval (knowledge query cites ${name})"
  out="$(r4_on_stack knowledge query "$SAMPLE_QUESTION" --project "$STACK_PROJECT" </dev/null 2>&1)" \
    || { smoke_row "$what" FAIL "$(one_line "$out")"; return 1; }
  if query_cites "$out" "$name"; then
    smoke_row "$what" PASS "$(one_line "$(grep -m1 -F "$name" <<<"${out#*--- Sources ---}")")"
    return 0
  fi
  smoke_row "$what" FAIL "not cited: $(one_line "$out")"; return 1
}

print_smoke_table() {
  local row what result evidence colour
  printf '\n  %-52s %-6s %s\n' "smoke test" "result" "evidence"
  for row in ${SMOKE_ROWS[@]+"${SMOKE_ROWS[@]}"}; do
    IFS='|' read -r what result evidence <<<"$row"
    colour="$GRN"; [[ "$result" == PASS ]] || colour="$RED"
    printf '  %-52s %s%-6s%s %s\n' "$what" "$colour" "$result" "$OFF" "$evidence"
  done
  printf '\n'
}

# Never a pass without the evidence: retrieval runs only on an ingested
# sample, and any failure fails the whole test.
smoke_test() {
  local fail=0 model
  SMOKE_ROWS=()
  model="$(cfg_get STANDALONE_CHAT_MODEL)"
  smoke_chat "$model" || fail=1
  if smoke_ingest; then
    smoke_query || fail=1
  else
    fail=1
    smoke_row "retrieval (knowledge query cites $(basename "$SAMPLE_FILE"))" FAIL "skipped: nothing was ingested"
  fi
  print_smoke_table
  return "$fail"
}
