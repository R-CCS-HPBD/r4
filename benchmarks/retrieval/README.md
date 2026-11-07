# Retrieval + inference eval (ADR-093 C0)

Measures what retrieval does for **inference**, on the stand-alone stack, with
the stack's own chat model (llama3.2:3b by default). It does not measure retrieval in
isolation. Every arm runs through a surface the agent or a user reaches:
the K3 facade, the companion's own `knowledge_query` / `rag_query` tools,
`/gw/rag/query`, or the ADR-093 verb `GET /gw/rag/retrieve`.

*Updated 2026-10-10: `retrieve:<preset>` arms, pack prompts, the scorer gate,
sufficiency metrics, rationale coverage and the offline replica. Review fixes
the same day: outages and server fallbacks are errors, never results; other
artifacts' passages keep their rank; undefined rates read None with their n;
`compare` carries the S1 receipts and the errors it leaves out; `parity` and
`headroom` commands; provenance names the code, server, models and corpus;
the replica chunks the text the live graph holds.*

*Updated 2026-10-10 (ADR-093 D-14, wave C): the `retrieve:large` preset (the
large profile the rikyu readers kimi-k3 and glm-5.3 retrieve with) and the
remote verdict reader (`infer --reader-config DIR --reader-model M`).*

*Updated 2026-10-11 (ADR-093 D-14, C23): the `tool:*` arms build the
companion's tools for the run's reader (the remote reader's model, else
`--model`), so `tool:knowledge_query` asks K3 for that reader's profile
`k_max` (small 4, medium 6, large 10) as a session of it does, and the
provenance records that model as `tool_reader`. Since the name alone does
not fix the sizing, a run with a `tool:*` arm also records what it resolved
to (`tool_sizing`: scale, its basis, profile, K3 limit; `R4_MODEL_SCALE` is
kept beside `R4_RAG_*`), and a `retrieve` file of a `tool:*` or agent arm
names a non-default `--model` (`-m-<model>`), because a `retrieve`
comparison does not hold the model fixed. `compare` (and `parity`) hold the
tool sizing fixed when every file they pair ran `tool:knowledge_query`, the
one tool arm its reader sizes: the factor `tool_sizing` is the K3 limit it
sent. The profile is not part of it (the tool never sends one; the profile
only picks the limit), and `tool:rag_query` carries no sizing at all (it
sends `top_k` 5 and no profile whatever the reader). A file written before
C23, which records none, reads as inferred (K3 limit 10, the limit the tool
then always asked for). Since C23b (2026-10-11) the tool also shows each hit
within its reader's share of the profile's pack, counted in the pack's own
estimated tokens (`r4_contracts.context_pack.estimate_tokens`: 4 ASCII
characters a token, a Japanese character a token by itself): `pack_tokens` /
`k_max`, at least 50 (small 300, medium 333, large 800; about 1,200, 1,332
and 3,200 characters of English, 300, 333 and 800 of Japanese). A hit cut
there ends at a word in the share's last fifth when it can and says
`content_clipped`; for a sized reader the tool's JSON carries non-ASCII text
as itself, not as `\uXXXX` escapes. With no reader, or one no profile
resolves for, the tool keeps 10 hits of exactly 200 characters whatever the
script, escapes non-ASCII as before C23b, and is recorded as the 50 tokens
those come to in ASCII text. Every string K3 returns goes through
`sanitize_text` on both paths (round 2, 2026-10-11): hidden characters
(bidi, zero-width, the whole tag block, C0/C1 controls) are dropped before
the cut, so they use none of a hit's share, and `[n]` look-alikes and
`<passages>` tags are neutralised. The unsized output is therefore
byte-identical to pre-C23b only where `sanitize_text` changes nothing. The
share is recorded as `tool_sizing.knowledge_query_tokens` and held fixed
beside the K3 limit; a file written before C23b reads as inferred at 50
tokens (its 200 characters of English), and a file carrying only the interim
`knowledge_query_chars` key reads as unrecorded.*

*Updated 2026-10-10 (C21 review, second pass): a verdict is read only from a
final answer (a reply cut at its output limit is an error row, and a
reasoning reader gets no 200-token limit); rate limits, timeouts and dropped
connections are retried; an expired login stops the run; bare SciFact ids
and passage numbers are cited as the reader meant them; gates, `headroom`
and `compare` count errored and missing claims and report the factors and
metric definitions their files differ in; records carry `schema_version`
(B0 files are schema 1) and `rescore` rewrites a B0 file under the current
definitions; provenance hashes untracked files, the imported r4 packages,
`r4_contracts` and the rag container's code.*

## The probe: SciFact

[SciFact](https://github.com/allenai/scifact) dev provides 300 scientific claims.
188 of them have evidence abstracts that SUPPORT or CONTRADICT the claim. The other 112 cite abstracts that
do not settle the claim, so the correct answer is NOT_ENOUGH_INFO. This gives:

- **retrieval qrels**: recall@k, MRR@10 and nDCG@10 against each claim's
  evidence documents;
- **an inference task**: the model reads exactly the context an arm
  produced and answers a verdict plus the sources it used. The scores are
  accuracy, macro-F1, `unsupported_verdict` (a verdict asserted when the
  corpus holds no evidence), `wrong_abstain_when_shown` (NOT_ENOUGH_INFO
  although the evidence was in the context) and citation precision.

The gate tier is the full corpus: graph `scifact-eval` in project `default`
holds all 5,183 abstracts (ADR-093 Context, decided 2026-10-10). The seeded
1k subset (the 283 documents the dev claims cite plus 717 distractors, seed
20261010) is kept as the record of the S0 baseline and as a replica tier.

Claim sets (`--claims`):

| Set | What | Use |
|---|---|---|
| `dev` | 300 SciFact dev claims | reported |
| `tune` | SciFact train (809 claims) | every constant is tuned here and only here |
| `nonsense` | `NONSENSE_EVAL_V1`: 20 word salads + 10 off-domain questions, ids from 900001 | out-of-KB; gold NOT_ENOUGH_INFO |

At the full tier every cited document is in the corpus, so there is no
separate out-of-KB claim set: in-KB abstention is the 112 dev NEI claims,
out-of-KB abstention is the nonsense probes.

## Running it

```bash
# data, once (not vendored; CC BY-NC 2.0)
mkdir -p ~/data/scifact && cd ~/data/scifact
curl -sSLO https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz && tar -xzf data.tar.gz

# the corpus into its own graph. LLM extraction off: one 3B extraction per
# document takes hours, and the base eval does not use the extracted graph.
STANDALONE_PROFILE=dev UMRS_KNOWLEDGE_LLM_EXTRACT=0 ./deploy/standalone/standalone.sh up
export XDG_CONFIG_HOME=<a throwaway r4 config, logged in from `standalone.sh token`>
.venv/bin/python -I -c "import sys; sys.path.insert(0,'benchmarks/retrieval'); \
  import scifact, pathlib; scifact.write_docs(scifact.load(pathlib.Path('$HOME/data/scifact/data'), distractors=None), pathlib.Path('/tmp/scifact-corpus'))"
.venv/bin/r4 knowledge graph-create scifact-eval -p default
.venv/bin/r4 knowledge graph-ingest scifact-eval -p default --file /tmp/scifact-corpus

R=".venv/bin/python benchmarks/retrieval/run.py"; D=~/data/scifact/data
# retrieval over the claims with evidence, then inference over all 300
$R retrieve --data $D --arms k3-embeddings,legacy,tool:knowledge_query
$R infer    --data $D --arms closed,oracle,legacy,k3-embeddings
$R infer    --data $D --claims nonsense --arms legacy
$R answer   --data $D                                  # /gw/rag/query as R4 calls it, E-B
$R answer   --data $D --threshold 0.7                  # the B0 bracket (file name -t0.7)
$R infer    --data $D --arms agent                     # 20 claims per label
# the ADR-093 verb, once the slice is live
$R retrieve --data $D --arms retrieve:legacy,retrieve:dense,retrieve:fused
$R retrieve --data $D --claims nonsense --arms retrieve:dense
$R infer    --data $D --arms retrieve:dense --prompt pack-soft --scorer-gate
# the large profile (D-14): its retrieval on the stack, read by the stack's 3B ...
$R infer    --data $D --arms retrieve:large
# ... and the same passages read by a rikyu model (ADR-093 MD-14)
$R infer    --data $D --arms retrieve:large,closed,oracle \
            --reader-config <XDG dir logged into rikyu> --reader-model kimi-k3
$R sufficiency results/<dev retrieve:dense>.json results/<nonsense retrieve:dense>.json
$R compare  results/<A>.json results/<B>.json
$R parity   results/<legacy B0>.json results/<retrieve:legacy>.json   # receipt 4
$R headroom results/<arm>.json results/<closed>.json results/<oracle>.json
$R repro    results/<run 1>.json results/<run 2>.json   # S9: does a run reproduce itself?
$R rescore  --data $D results/<a B0 file>.json   # writes <name>-rescored.json beside it
```

Common flags: `--claims`, `--project` (default `default`), `--graph`
(default `scifact-eval`), `--limit N` (agent: N per label), `--label`
(a file-name suffix).

## Arms

One factor per arm (`arms.py`; `register` adds one):

| Arm | Context |
|---|---|
| `closed` | nothing (the closed-book bracket) |
| `oracle` | the gold evidence abstracts (cited docs for NEI claims): the upper bracket |
| `legacy` | `/gw/rag/query`'s own passages (top 5 at cosine >= 0.7), its layout and its prompt |
| `k3-embeddings`, `k3-all` | the K3 facade, top 5 of 20 |
| `tool:knowledge_query`, `tool:rag_query` | the companion tool's output string (`knowledge_query` searches `--graph`) |
| `retrieve:<preset>` | `GET /gw/rag/retrieve` with explicit knobs and `trace=1` |
| `agent` | one fresh companion session per claim, every tool pre-approved |

`retrieve:<preset>` knobs (every knob explicit, the request sizes included,
so the arm measures one factor whatever the site defaults are; "sizes" is
`k_max=4`, `pool=50`, `per_artifact=2`, the ADR-093 defaults):

| Preset | Knobs |
|---|---|
| `legacy` | `threshold=0.7`, `k_max=5`, lexical/rerank/merge off; the whole project, no graph (only that runs the core's frozen pre-ADR-093 statement, the receipt-4 parity path /gw/rag/query used in B0) |
| `dense` | adaptive cut, sizes, lexical/rerank/merge off |
| `fused` | adaptive cut, sizes, lexical on, rerank/merge off |
| `rerank` | adaptive cut, sizes, lexical on, rerank on, merge off |
| `merge` | adaptive cut, sizes, lexical on, merge on, rerank off |
| `default` | lexical/rerank/merge `auto` and the site's sizes (the site's own defaults) |
| `large` | `profile=large`, `reader_model=kimi-k3`, adaptive cut, `k_max` and `pool` read from `RETRIEVAL_PROFILES["large"]` (since ADR-093 MD-14, 2026-10-13, the medium sizes: `k_max=6`, `pool=50`; runs before that date record `k_max=10`, `pool=100` in their `preset`), `per_artifact=2`, lexical on, rerank off, merge on: the `merge` preset at the ADR-093 D-14 large profile's sizes (`r4_contracts.models.RETRIEVAL_PROFILES["large"]`); the profile also sets the partial cap (6), which no request knob reaches |

A stage a preset turns `on` must report `ok` in the lane report (`lex` and
`ident` for lexical, with `fusion` `ok` or `off` when only one ranking had
results; `rerank`; `merge`); otherwise the server fell back (fused ran as
dense, rerank as fused, RRF as concatenation) and the row is an error
(`PresetNotMeasured`), never a result under the preset's name. A preset
that names a `profile` also needs the server to report it
(`lanes.profile.name`): a server that ignored it cut with the small
profile's partial cap. Each summary counts the lane statuses (`lane_status`;
the profile report counts by its name).

A retrieve arm calls `client.rag.retrieve` when the r4 client has it and
the method takes every knob of the preset (an r4 before D-14 takes no
`profile` or `reader_model`), else a raw GET with the same parameters. Its retrieval metrics read the pre-cut
ranking (`trace.candidates`; the kept passages in legacy mode, which has no
trace); its context is what the cut kept. Each row records the server's
`sufficiency`, the lane report, `timing_ms`, the kept passages and the
sufficiency features. A client that fell back to `/gw/rag/query` (a server
without the verb) is recorded as an error, never as a result.

An outage is never a result either: the server answers HTTP 200 with
`sufficiency.basis == "unavailable"` when the query encoder times out, and a
failed `dense` or `null` lane reports `unavailable`. Those, a companion
tool's error report (`knowledge_error` JSON, `[embedding_backend_unavailable]`,
`Error ...`), and /rag/query's own failure answers (`spine query failed`,
`knowledge database unavailable`, `Could not generate an answer`, or its
metadata's lane report) raise `TransientError`: the claim is retried, and an
outage that persists is an error row. A K3 answer whose lexical lane
(`rag_lane`, the `k3-all` scope) reports `unavailable` is not a `k3-all`
result either: retried when the reason names an outage that heals, else
not measured (a store without `search_text`); each K3 row records
`rag_lane`. A preset stage that did not run for a reason that heals
(`reranker unreachable (ReadTimeout)`, a timeout) is retried the same way;
any other reason is `PresetNotMeasured`.

Retries (`infer.with_retries`, waits 10 s to 600 s): a gateway 5xx, a rate
limit (429, waiting at least its `Retry-After`, at most 900 s), a timeout, a
dropped connection and a `TransientError`. A refused or expired login
(401/403, on the stack or the remote reader; r4 never refreshes an OIDC
token) is not retried: it stops the run with no file written, never a file
of error rows. A retrieve call waits up to 120 s on both paths (the client
method's own default is 10 s); each row records the bound in
`meta.retrieve_timeout_s`. In `retrieve`
mode a retrieve arm runs every claim, not only those with evidence: its
verdict on NEI and nonsense claims is the point.

## Prompts and the scorer gate

`--prompt` sets the verdict prompt for every arm in the command (default:
each arm's own: `legacy` for `legacy`, `pack-soft` for `retrieve:*`, `json`
otherwise):

| Prompt | Layout |
|---|---|
| `json` | the S0 reader: context first, claim last, JSON verdict |
| `legacy` | rag_spine's wording: question first, strict abstention |
| `pack-soft`, `pack-strict`, `quote` | `r4_contracts.context_pack.render_pack` with the SOFT / STRICT / QUOTE instruction (passages first, question last), then the JSON verdict format |

A pack prompt renders the arm's own passages, so the same passages can be
read under every prompt (the C4 factorial); `--order edge` puts the best
passages at both ends when there are three or more (it changes only pack
prompts, so only they carry `-o-edge` in the file name). A reply of exactly
`NOT IN KNOWLEDGE BASE` (the pack's abstention phrase, a trailing period
allowed) parses as NOT_ENOUGH_INFO; a reply that only mentions it does not.
A reply with no JSON verdict is unparsed; its label is read from prose only
when exactly one label word appears with no negation before it in its
sentence ("do not support" is NOT_ENOUGH_INFO, never SUPPORT). A verdict is
read only from the reply's final answer: a reply cut at its output limit
(`finish_reason` length), one that holds only reasoning (an unclosed
`<think>`) and an empty one are error rows (`ReplyNotScored`), never a
verdict, and a closed `<think>` block is dropped before parsing. Every
verdict row, and every such error row, records `finish_reason`,
`completion_tokens` and the `max_tokens` it asked for.
`--scorer-gate` answers NOT_ENOUGH_INFO with no model call when a
`retrieve:*` arm reports `insufficient` (`gated: true` in the row,
`gated_rate` in the summary). A gated row's reader is shown nothing: its
`context_tokens`, `context_chars`, `passages_shown` and `context` read zero,
and what the cut kept moves under `retrieved`. Its `gold_in_context` still
says whether the cut kept the evidence, so a gate that withholds evidence
counts in `wrong_abstain_when_shown`.

## The large profile and the remote reader (ADR-093 D-14)

The stand-alone stack reads with `llama3.2:3b`; the full remote deployment
reads with rikyu's kimi-k3 and glm-5.3 (frontier MoE models with 1M-token
windows). The large profile is the same retrieval code with larger knobs, so
the stack measures it directly (`retrieve:large`). Its *reader* effect needs
the large model itself:

- `infer --reader-config DIR --reader-model M` sends **only the verdict
  call** through a second r4 client built from `DIR` (an `XDG_CONFIG_HOME`
  directory, e.g. one logged into the rikyu gateway with
  `XDG_CONFIG_HOME=DIR r4 login`). Retrieval, the arms and the provenance
  queries stay on the stand-alone stack, so both readers see the same
  passages. The reader offers `chat_completion` and nothing else.
- No remote reader is used unless `--reader-config` is given: there is no
  environment fallback, and the two flags go together. `retrieve` and
  `answer` do not take them, and the `agent` arm refuses them (a companion
  session retrieves and reads through one client on the stack).
- The reader resolves its config as an r4 process started with
  `XDG_CONFIG_HOME=DIR` and no `R4_*` variables would: the stand-alone run's
  gateway, key, profile and source never reach it, and r4's per-process
  credential and project singletons are swapped for its own around every
  call. A directory with no r4 config, no credential, or the stack's own
  gateway is refused (`ReaderConfigError`).
- The result file names the reader (`-r-<model>`), records `reader_model`
  beside `model` (the stack's), and its provenance carries `reader`: the
  gateway URL and the route the model resolves to (user info, query and
  fragment stripped), the model, its D-14 profile, the r4 profile name and
  the directory. Never a key or token. A stack-read file records
  `reader: {kind: stack, model, retrieval_profile}`.
- The verdict prompt is the same for every reader. The output budget is
  sized to the reader: the stack's 3B keeps B0's `max_tokens=200`, so its
  runs pair with B0; a reasoning family (ADR-087 D-2: glm-5, kimi-k2/k3,
  deepseek-r1, qwen3, gpt-oss, ...) or a large reader sends none, so the
  provider's default applies (rikyu: 32,768), as the D-14 large profile
  answers. A reply that still ends at its limit is an error row, never a
  verdict read from cut reasoning; the file records `verdict_max_tokens`
  and `compare` reports it as a factor.
- The reader's login is checked before each arm, and a refused credential
  stops the run (`ReaderConfigError`, naming
  `XDG_CONFIG_HOME=DIR r4 login`).
- Eval traffic to rikyu (SciFact claims and the retrieved public abstracts,
  about 300-600 calls per arm) was allowed by the maintainer for kimi-k3 and
  glm-5.3 on 2026-10-10 (ADR-093 MD-14).

## Measures

- **Retrieval** (pre-cut, deduped to 20 docs): recall@{1,3,5,10,20},
  MRR@10, nDCG@10. A passage of another artifact in the project (no SciFact
  doc) keeps its rank as `other:<artifact id>` (`other:<node id>` when the
  surface names no artifact): it pushes the evidence down like any miss,
  and two chunks of one such artifact take one rank, as two chunks of one
  SciFact doc do.
- **Context** (after the cut): `ctx_precision` (share of shown passages from
  evidence docs; an `other:` passage counts as a miss, and `intruders` per row
  counts them), `ctx_recall` (any evidence shown), `passages`,
  `context_tokens` (the ADR-085 D-2 estimate of what the reader is shown),
  `rationale_coverage` (share of the claim's rationale sentences found whole
  inside one passage as the prompt renders it: a pack shows each passage
  whole, the json and legacy prompts show the arm's context string, cut as
  the arm cuts it; a sentence split across two chunks counts only when a
  merged passage holds it). `shown_refs` keeps the shown passages' node ids
  in order (`parity` compares them).
- **Sufficiency** (`retrieve:*` arms; `run.py sufficiency` pools a dev and a
  nonsense file): insufficient rate on nonsense (gate >= 0.9),
  false-insufficient on in-KB evidence claims (gate <= 0.05), insufficient
  rate on NEI claims (reported), and the AUROC (Mann-Whitney) of `z_null`,
  `gap12`, `lexical` and `rerank_p`, evidence claims against nonsense and
  against NEI. Each group reports `errors`, and a gate is judged on
  `rate_itt`, every errored claim counted as the gate's failure (not
  insufficient on nonsense, insufficient on evidence): a scorer cannot pass
  by failing its hard claims. `rate` stays the rate over judged claims.
- **E-A verdicts**: accuracy (bootstrap CI), macro-F1, recall by label,
  `unsupported_verdict`, `wrong_abstain_when_shown`, citation precision,
  `phantom_rate`, parse rate, `gated_rate`, seconds and prompt tokens. A
  cited ref that names nothing the reader was shown is kept as
  `phantom:<ref>` and counts against citation precision. A ref names a doc
  by its name, its artifact id, or its bare SciFact id when that doc was
  shown (`14717500`, `[14717500]`, `14717500.md`: the context labels it
  `scifact-14717500.md`); it names passages only when it is a passage
  reference (`2`, `[2]`, `passage 2`, `1, 2`, `[1][3]`). `GNB2`, `0.5` or
  `HR 1.2` name no passage: they are phantoms. A conditional rate whose
  selection is empty is `null` (undefined), never 0.0, and every one
  carries its denominator as `<key>_n`.
- **E-B** (`answer`): abstention on evidence vs NEI claims, citation hits
  (grouped labels such as `[1, 2]` count each number), latency. By default
  no threshold is sent, as R4 calls /rag/query since ADR-093 wave A;
  `--threshold 0.7` is the B0 bracket. `abstained` is the server's own
  post-check (`metadata.abstained`: a cited answer that names a gap is a
  partial answer, `partial_answer`, not an abstention) when the server
  reports it, else the phrase anywhere in the answer (B0's rule, kept in
  every row as `abstained_phrase`); `abstained_basis` says which, the
  summary adds `abstain_rate_evidence_phrase` and `abstain_disagreement`,
  and each row records the server's `profile` (`lanes.profile.name`).
- **Latency** (`latency` in every summary): p50 and p95 of
  `retrieve_seconds`, `infer_seconds`, prompt tokens (a gated row costs 0)
  and each stage of the server's `timing_ms`.

`compare` reports paired deltas over matched claims, with a seeded bootstrap
95% interval (2,000 resamples, seed 20261010) and `p_not_better` (the share
of resamples where B is no better than A), plus an exact McNemar on verdict
flips. On judged files it adds the S1 receipts: `wrong_abstain_when_shown`
(paired over claims whose evidence both arms showed) and
`unsupported_verdict` (over claims without evidence), each lower-is-better
with `ci_excludes_zero`; `macro_f1` bootstrapped over resampled claims; and
`non_inferior` on accuracy and macro-F1 (lower CI bound >= -3 points). It
names what the pairing left out (`errors`, `unpaired`, `complete`), warns
when anything was, and adds `accuracy_itt`, which scores an errored claim
as wrong. A claim only one file ran that carries nothing `compare` reads
(an NEI row of a `retrieve:*` arm in retrieve mode, which runs all 300
claims) is counted apart as `unpaired_unscored` and does not make the
pairing incomplete. On two `answer` files it pairs the E-B receipts
(`abstain_rate_evidence`, `abstain_rate_when_shown`, `abstain_rate_nei`,
`cites_evidence_rate`), reading both files with one abstention rule
(`abstained_rule`: the server flag only when every row carries it, else
the phrase). With 300 claims, an accuracy change smaller than about 6-7
points should not be read as an improvement unless the interval excludes
zero.

`parity A B` counts claims whose shown passages have identical node ids in
identical order (doc ids for files written before `shown_refs`; the B0
legacy files keep only doc ids). B0's harness dropped another artifact's
passage from `shown`, so on the doc-id basis a claim that matches once the
`other:` entries are set aside is `undecidable`, never identical nor a
mismatch; receipt 4 on such claims needs a legacy run that recorded
`shown_refs` (B0'). `headroom ARM CLOSED ORACLE` gives
`headroom_used = (arm - closed) / (oracle - closed)` for accuracy and
macro-F1 on the claims all three files judged, `completeness` per file
(errors, claims the others ran that it did not) and `accuracy_itt` (every
claim all three hold, an errored claim scored wrong). `repro A B` takes two
runs of one configuration and counts, over the claims both judged, the same
verdict, reply, shown passages and citations (`same_<field>`, with
`same_<field>_n` the pairs that recorded the field); `verdict_differs` names
the claims whose verdict moved, and `reproduces` holds only when none did
and neither run errored or left a claim out.

Every comparison also reports what its files did not hold fixed and what
they define differently, and warns on stderr:

- `factors`: `differ` (the claim set, the stack model, the remote reader,
  the prompt, the pack order, the scorer gate, `verdict_max_tokens`, the
  /rag/query threshold, the query encoding `query_prefix`, the corpus
  `chunks_md5`), `unrecorded` (a file that cannot show a factor) and
  `inferred` (a B0 file's value taken from B0's harness: no remote reader,
  200 tokens, no gate, for `answer` the 0.7 threshold its files never
  recorded, and the `b0` query encoding its server ran, so a receipt-4
  parity run under `UMRS_RAG_QUERY_PREFIX=fixed` is named as differing).
  When every file ran `tool:knowledge_query`, `tool_sizing` is a factor
  too: the K3 limit the tool asked for (`provenance.tool_sizing`, C23); a
  file written before C23 reads as inferred at 10, and a sizing that failed
  is `unrecorded`. A pair with any other arm (`tool:rag_query`, `retrieve:*`,
  the agent, the brackets) leaves it out: one side made no sized call.
- `definitions`: every record carries `schema_version` (2; B0's files,
  which carry none, are schema 1). When the files' schemas differ in a
  field the comparison reads (`ranked`, `shown`, `verdict`, `abstained`,
  `answer_cited`), the report names the change. `run.py rescore` rewrites
  a B0 file's verdicts (read from the stored replies; a reply that used
  its 200 tokens becomes an error row), citations and E-B abstention fields
  under schema 2 into `<name>-rescored.json`; `ranked` and `shown` stay v1
  (B0 never recorded another artifact's passage), and a row whose stored
  reply or answer was cut, or whose `[n]` numbering a dropped passage
  shifted, keeps its v1 citations and says so (`rescore` in the row).

## The offline replica

`offline_lanes.py` replays the dense, BM25, IDF-lexical and RRF lanes in
numpy over cached vectors (the platform chunker's 256-token windows over the
exact text the live graph ingested, `scifact.doc_text`; the staged
e5-base-v2), so a lane change can be sized without the stack:

```bash
.venv/bin/python benchmarks/retrieval/offline_lanes.py build --data $D \
    --model-dir <staged e5-base-v2 directory> --out ~/.cache/adr093/scifact-e5-base-v2
.venv/bin/python benchmarks/retrieval/offline_lanes.py eval [--tier full|1k] [--split dev|tune]
```

`build` never downloads a model (`HF_HUB_OFFLINE=1`; `--model-dir` must be
a local directory) and writes `meta.json` naming the text scheme. `eval`
reads `--cache`, else `ADR093_REPLICA_DIR`, else
`~/.cache/adr093/scifact-e5-base-v2`, and reports against its scheme's
reference:

| Scheme | Chunks full / 1k | nDCG@10 full (dense, BM25, RRF) | 1k |
|---|---|---|---|
| `ingested-md-v1` (`build`: `# title\n\nabstract\n`, as the live graph) | 9,377 / 1,842 | 0.838, 0.814, 0.870 | 0.945, 0.899, 0.950 |
| `title-abstract-v0` (the G-0 prototype's cache, no `meta.json`) | 9,356 / 1,839 | 0.834, 0.817, 0.876 | 0.937, 0.900, 0.950 |

The live graph holds the first scheme's chunks (9,377 in Postgres), so the
live-vs-replica comparison reads its numbers: the live 1k dense nDCG@10
(0.911) sits 3.4 points under it, not 2.6, so the chunk text does not explain
that gap. The test `test_the_replica_reproduces_the_full_corpus_record`
checks a cache against its scheme's reference within ±0.005 when the cache is
present and skips, naming the path, when it is not. `--split tune` scores
only claims whose evidence docs are all in the tier's corpus and reports how
many it dropped. The cross-encoder lane is not replayed (it needs the staged
reranker).

## Gate probes: S6, S8, S9 (ADR-093)

Three slices gate on measurements no `run.py` arm makes. Each has its own
harness here; the gate wording is ADR-093's table, and each definition below
is recorded in the result file it produces (`definitions`). All three write
under `results/` (`ADR093_RESULTS`), and none mints a token or reads a
credential: a step marked (M) is the maintainer's.

### S6: the repaired consumers (`consumer_probe.py`)

Gate: "search_agentic grounded ≥ 90% on 30 claims. Loop digest finds the
gold doc in ≥ 3 of 5 goals." ("Tests fail before and pass after" and
"cleanup commit separate" are review receipts, not measurements.)

```bash
P=".venv/bin/python benchmarks/retrieval/consumer_probe.py"
$P plan --data $D                  # the seeded selection; no stack call
$P run  --data $D                  # both legs, one file: <stamp>-consumers-dev-s6.json
$P run  --data $D --legs loop      # one leg; --scope/--depth change search_agentic's
```

- **search_agentic leg.** The companion tool built as a session of `--model`
  builds it (`get_builtin_tools(client, session_model=...)`), with its own
  defaults (`depth=shallow`, `scope=all`), the bare claim as the query, over
  30 dev claims: a seeded draw (`scifact.SEED`) of 10 per label, the ten
  NOT_ENOUGH_INFO claims reported apart (`nei`). Every `client.rag.retrieve`
  call the tool makes is recorded. Per claim: the answer, every cited `[n]`
  and the source it names, the cited and the retrieved SciFact docs,
  `cites_gold`, `gold_retrieved`, latency and the retrieve calls (with the
  `k_max`, `profile` and `reader_model` sent).
- **grounded**, all of: the tool answered; the answer cites at least one
  `[n]` (each number of `[1, 2]` counted); every cited number names a source
  the search handed its synthesizer (`sources[n-1]`, else the tool's own
  citation entry for `n`; anything else is a phantom); at least one cited
  source is a passage a `rag.retrieve` call of this search returned. The
  gate reads `grounded_itt` over all 30 claims: an errored claim is not
  grounded. A retrieve that raised inside the search is an outage (retried,
  then an error row), never a consumer result.
- **Loop leg.** The call a local loop iteration makes before its model turn
  (`LocalAgentIterationExecutor._knowledge_context`: `rag.retrieve` at the
  loop's `k_max` 4 with the loop model's reader fields, rendered by the shared
  pack), for 5 goals made from 5 more dev evidence claims (`GOAL_TEMPLATE`, a
  seeded draw disjoint from the 30). No model call. The gold doc "appears"
  when a gold evidence doc is among the passages the pack renders; a graph
  digest or "no passage addresses this goal" finds none. Gate: 3 of 5, an
  errored goal not found. UMRS's own loop (`SpineKnowledgeStore`, where MD-6
  removed the recency fallback) has no route short of running a loop; it
  calls the same retrieval core.
- (M) The stack up and an eval r4 config, as for every arm.

### S8: portable chunk text (`portable_probe.py`, `s8_second_install.sh`)

Gate: "md5 100%. Import retrieval within 0.01 nDCG. Without the flag the
bundle has the v1.2 shape. Offline chunk count matches the export. v1.3 r@5
≥ v1.2."

`benchmarks/retrieval/s8_second_install.sh steps` prints the whole run in
order with its (M) steps. In short:

```bash
P=".venv/bin/python benchmarks/retrieval/portable_probe.py"; S=benchmarks/retrieval/s8_second_install.sh
# source installation (its eval config; COMPOSE_PROJECT_NAME unset)
$P export --project default --chunk-text --out ~/s8-bundles   # job, wait, download: default-v13.kb.tar.gz
$P export --project default --out ~/s8-bundles                # default-v12.kb.tar.gz
$P verify --v13 ~/s8-bundles/default-v13.kb.tar.gz --v12 ~/s8-bundles/default-v12.kb.tar.gz
$R retrieve --data $D --arms retrieve:dense --graph "" --label s8-source
# second installation, once per bundle (node ids are installation-global)
$S up; eval "$($S env)"; export XDG_CONFIG_HOME=<its own r4 config>
$P import ~/s8-bundles/default-v13.kb.tar.gz --target-compose-project ai4s-s8
$R retrieve --data $D --arms retrieve:dense --graph "" --label s8-v13
$S wipe --yes && $S up            # then the v1.2 bundle, --label s8-v12
$P gate --verify <verify file> --source <s8-source> --v13 <s8-v13> --v12 <s8-v12>
```

- **md5**: every chunk text of the v1.3 bundle hashes to its
  `content.chunk_md5`; the manifest says `1.3` with `chunk_text: true`;
  `chunk_text_summary.missing` and `truncated` are 0. Chunks of artifacts that
  are not active carry no text by design (`no_artifact`, reported).
- **v1.2 shape**: version `1.2`; no `chunk_text`/`chunk_text_summary` in the
  manifest and no `text`/`text_header`/`text_truncated` on any node; the
  v1.3 bundle's manifest keys, tables and chunk-row keys minus those; the same
  chunk nodes; no raw objects.
- **offline count**: `RAGClient.load_bundle` (what `r4 chat --knowledge`
  loads) holds as many chunks as the bundle has chunk nodes and as the v1.3
  manifest declares, for both bundles.
- **import nDCG**: |mean nDCG@10 of the v1.3 import − the source's| ≤ 0.01,
  `retrieve:dense`, paired on claim ids, both files complete. **r@5**: mean
  recall@5 of the v1.3 import ≥ the v1.2 import's, same rule. Both sides
  search the whole project (`--graph ""`): a bundle is a whole project, and
  an import lands in the target project's default graph.
- `verify` also checks that `artifacts.jsonl` lists every artifact a chunk
  names. umrs `bundle_export._dump_core_tables` writes one page of
  `list_artifacts` (`page_size` 500) while the manifest declares the total,
  so on the 5,183-abstract project it should warn: the import would restore
  500 artifacts and the import gate cannot pass until the export pages.
- **The second installation** is a second compose project
  (`COMPOSE_PROJECT_NAME=ai4s-s8`, `s8_second_install.env`, exported before
  `standalone.sh` runs; dev profile for the same mounted code; compose
  profile `core` only, no chat model). It shares `env/.env.secrets` (a fixed
  path in `standalone.sh`), the umrs image and, by tag, the gateway images.
  `wipe --yes` resets only that project and refuses the source's name;
  `import` refuses unless `COMPOSE_PROJECT_NAME` names the second project.
- **Ports.** `deploy/standalone/compose.yaml` and `compose.dev.yaml` publish
  fixed host ports (8080, 5555, 5556-5563; dev adds 8082, 5432, 9000, 8111,
  8081) and read no variable, so today the script runs in **sequential**
  mode: (M) the source stack is stopped (`standalone.sh down`, volumes kept)
  while the second one runs, and `up` refuses while 127.0.0.1:8080 answers.
  Parallel mode needs a deployment change (the maintainer's): each published
  port read from the names in `s8_second_install.env` with today's value as
  the default, e.g. `"127.0.0.1:${STANDALONE_GATEWAY_PORT:-8080}:8080"`, and
  the ZMQ data range checked against what UMRS advertises to clients.
- (M) Before the `--chunk-text` export: project `default` registered with an
  owner and a data class, and either owned by the export's submitter or
  `non_sensitive` (`PATCH /gw/projects/default`, `{"action":
  "set_data_class", "data_class": "non_sensitive"}`); D-12 fails closed
  otherwise. (M) A token and an isolated r4 config for the second
  installation (`up` prints the command). (M) `--allow-unsigned` on an
  import only if the server refuses the bundle's signature.

### S9: confirm against B0 (`s9_confirm.sh`)

Gate (pre-registered verbatim in the script header): "Final configuration vs
B0 with two seeds, headroom_used, retrieve p95 ≤ 1.5 s." plus, for the agent
arm, row S5 (MD-2).

```bash
S9_DRY_RUN=1 S9_DATA=$D benchmarks/retrieval/s9_confirm.sh   # print every command
S9_DATA=$D XDG_CONFIG_HOME=<eval config> ADR093_REPO_ROOT=<deployed checkout> \
    benchmarks/retrieval/s9_confirm.sh
```

- Runs `retrieve:dense` (retrieve mode, dev and nonsense), `retrieve:dense`
  + pack-soft infer on dev and tune twice (`-S9-run1`, `-S9-run2`) and on
  nonsense, `run.py answer` on dev and nonsense, the agent with
  `R4_RAG_ROUTER=on` at `--limit 60` on dev and the nonsense set, and fresh
  pack-soft closed/oracle brackets (`S9_FRESH_BRACKETS=0` skips them). B0
  files are rescored to schema 2 once when no `-rescored` file exists.
- Writes `<stamp>-s9-*.json`: `compare` of each run against its B0 file,
  `repro` of run 1 against run 2 (dev, tune), `headroom` against the B0 and
  the fresh brackets, `sufficiency`, and `latency`, which judges the one
  number the row fixes: `summary.latency.retrieve_seconds.p95` of the dev
  retrieve run ≤ 1.5 s (`timing_ms.*` p95 beside it).
- "Two seeds" is two runs: r4's `Client.chat_completion` takes no `seed`, so
  none reaches ollama and `run.py` has no `--seed`; at temperature 0, `repro`
  records whether run 2 reproduces run 1. "vs B0" and headroom_used carry no
  threshold in the row; the reports are for the maintainer to read against
  it. The documentation half of the row is not a measurement.
- (M) The stack up with the final defaults and an eval r4 config.

## Notes

- Results are JSON under `results/` (git-ignored: per-claim rows derived from
  SciFact), one file per arm, with every claim's ranking, verdict and the
  model's raw reply, so a regression can be traced to one claim. A file name
  carries the non-default reader options (`-p-<prompt>`, `-o-edge`, `-gate`).
- Each file records its provenance (a field that cannot be read says
  `unavailable: <why>`):
  - `code`: parent and umrs commits, `dirty` counting untracked files, the
    sha256 of `git status --porcelain` and of `git diff HEAD`, a sha256 over
    the content of every untracked file (`untracked_sha256`), and content
    hashes of the running harness copy, of the r4 and `r4_contracts`
    packages this process imported (`imported`: a frozen copy run through
    `PYTHONPATH` is not the checkout `parent` describes), and of the
    checkout's umrs code the containers mount: the retrieval code
    (`knowledge/retrieval/`, `api/rag_retrieve.py`, `rag_spine.py`,
    `rag.py`, and `knowledge_query.py`, the K3 route), the rag service's
    (`api/rag_service.py`, `rag/search.py`, ...) and `r4_contracts`;
  - `server`: what the umrs and the rag containers see, from
    `server_probe.py` run inside each: the same hashes (both containers
    mount `r4_contracts`), PID 1's start time, `matches_checkout`, and
    `edited_after_start` (a bind-mounted tree edited after the restart is not
    what runs), each over both containers;
  - `models`: the chat model's ollama digest (`/api/tags`, reached from the
    umrs container; `ADR093_OLLAMA_URL` overrides `http://models:11434`) and
    the embedder and reranker the rag service loaded (`/rag/stats`);
  - `corpus`: the graph's id, artifact, chunk and vector counts, the newest
    vector time, the embedding models, an md5 over node ids, chunk md5s and
    embedded-text hashes, the chunk scheme, the project-wide chunk counts
    (project-wide arms search them too), and `search_text`, what the lexical
    lanes search: the chunks that carry it and an md5 over it (a partial and
    a full backfill differ), or why not (no column before migration 0120);
  - every `UMRS_RAG_*`/`UMRS_KNOWLEDGE_*`/`OLLAMA_*` value the umrs and
    models services run with, the `R4_RAG_*` environment, and the Postgres,
    pgvector, pg_trgm and unaccent versions;
  - `reader`: who read the verdicts (the stack's model, or the remote
    reader's gateway URL, route and model; never a credential). `null` for
    `retrieve` (nothing reads verdicts); `answer` names no model, so its
    profile is the server's (`server-resolved`; each E-B row records it).
- The agent arm records every retrieval surface a turn used
  (`knowledge.query`, `rag.query`, `rag.retrieve`, and
  `router:rag.retrieve` for the pre-retrieval router) and the router's
  decision per turn (`routes`).
- The harness reads artifact names and store versions from Postgres with
  `standalone.sh exec`, the store access that works in both profiles. It
  clears `XDG_CONFIG_HOME` for that call: podman finds its machine
  connection there.
- `ADR093_REPO_ROOT` and `ADR093_RESULTS` let a frozen copy of the harness
  run against the live repository's stack and results directory.

## Committed aggregate records (MD-10)

*Added 2026-10-11 (ADR-093 MD-10: the maintainer allows committing aggregate metrics, never SciFact text).* `results/` stays uncommitted, because its rows carry claims, passages and replies. `records.py` writes what may be committed: per run, the arm, claim set, label, model, prompt and preset numbers; the code commits and content digests (no paths); and every number of the summary. Strings longer than 64 characters and keys with whitespace are dropped, so no claim text can travel inside a summary.

```bash
.venv/bin/python benchmarks/retrieval/records.py benchmarks/retrieval/results/2026*.json \
    --out benchmarks/retrieval/records/adr093-2026-10-11.json
```

