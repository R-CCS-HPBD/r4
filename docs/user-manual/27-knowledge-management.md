# 27. Knowledge Management

R4 projects accumulate a durable research layer — documents, embeddings,
knowledge graph, catalog, scientific memory, evidence — all owned by
UMRS and scoped per project. This chapter covers how that layer is
explored, searched, verified, and moved between deployments.

> **Refreshed 2026-09-24** (sections 27.3–27.5: the export/import transport,
> canonical document identity, admin-only restore). 27.1–27.2 still describe
> the implementation as of their own date.

The four surfaces:

| Surface | Where | Purpose |
|---------|-------|---------|
| **Unified query** | Backend-aware search | One entry to search across the project's knowledge backends |
| **Explorer TUI** | Interactive cockpit | Three-pane polish to *see* the research layer, with evidence and lineage |
| **Bundle export** | `r4 project export` → `r4 knowledge export-download` | Signed `.kb.tar.gz` of a project's knowledge, for sharing or DR |
| **Bundle import** | `r4 knowledge import <archive>` | Bring a bundle into your project — including one from another installation |

---

## 27.1 Unified Knowledge Query

Air-gate everything you would otherwise fetch in five routes:

```bash
r4 knowledge query "transformer optimization" \
  --scope all --limit 20
```

Output renders results ranked by score with a coverage receipt — which
backends answered and which ones honestly reported *unavailable* (plus
the reason — e.g. embeddings without a deployed service):

```
3 result(s) for "transformer optimization" [catalog; 12ms]
  catalog_hit         0.74  attention_survey.txt
      This paper surveys attention mechanisms in transformer models...
Unwired backends:
  - embeddings: query-embedding generation requires the embedding service
  - rag: RAG search requires a deployed mini-rivault instance
```

Scope the search with `--scope catalog|knowledge_graph|embeddings|rag` and
limit with `--limit N` (max 100). `--json` prints raw payloads.

### Multigraph — named knowledge graphs

Projects can host multiple named knowledge graphs (multigraph). Query selects
graphs explicitly, distinct from `--scope` (backend type):

```bash
r4 knowledge graphs                          # List registered graphs
r4 knowledge graphs --query proteins         # Discovery search by name/description
r4 knowledge graph <id-or-name>              # Show one graph
r4 knowledge graph-create "proteins-lit" --project-id mylab [--kind KIND]
r4 knowledge graph-ingest proteins-lit ./papers/ --url https://example.org/p1
r4 knowledge graph-archive proteins-lit      # Soft delete (archive is the only delete)

# Share reads beyond the tier ladder (2026-10-03):
r4 knowledge graph-access proteins-lit --set circle:proj-a/lab --set user:bob
r4 knowledge graph-access proteins-lit          # show the list
r4 knowledge graph-access proteins-lit --private

# Query specific graphs / exclude graphs (exclude wins):
r4 knowledge query "binding sites" --graphs proteins-lit,materials
r4 knowledge query "binding sites" --exclude-graphs scratch
```

Hits carry per-graph provenance. From the Companion tools, graph extension is
permission-gated: `knowledge_graph_propose` → user approval →
`knowledge_graph_decide` → `knowledge_ingest` (never silently).

Sharing semantics: the `access` list governs **reads** of the graph
(detail, content, listing) for several named users/circles/projects at once —
every active circle membership of a caller counts; `system` entries are
admin-granted only. Writes (metadata, archive, ingest) stay on the tier
ladder (owner / project / admin), and an *unset* list means the historical
behavior exactly.

### What the project knows (`r4 knowledge ls`)

Added 2026-10-24 (knowledge plan K1.3). One list of the project's knowledge,
newest first, with who added each item:

```bash
r4 knowledge ls                    # ADDED · WHO · KIND · SIZE · INDEXED · GRAPH · ID · TITLE
r4 knowledge ls --mine             # what you added (--by USER: what a member added)
r4 knowledge ls --since 7d --kind document --graph methods --json
```

`WHO` marks how each person added the item: `mika` (an upload, or their own
text), `mika (kept)` (an assistant answer they kept: proposed until a
teammate establishes it), `mika (vouched)` or `mika (agent)` (written by
their agent: proposed). `INDEXED` says whether retrieval can find the item
yet: `yes`, `pending` (the embed job has not finished) or `stale`. A bad
`--since` or a `--mine` with no user id exits 2; with `--json` an error is
printed as `{"error": …, "exit_code": …}`. `--graph` takes a name of one of
the project's graphs (or your personal ones) or a graph id. The same server list backs
`r4 knowledge docs list`, `r4 knowledge stats` and the console's Knowledge
pane, so after an upload all four show the same item with its contributor.
Another member's personal-graph item is never listed. `r4 knowledge
bundles` (formerly `list`, still an alias) lists local `.kb` bundles.

### Keeping a chat turn (`/save`)

Added 2026-10-24 (knowledge plan K1.6). In the console or the chat REPL:

```
/save                    the last answer
/save 2                  turn 2's answer (t2 works too)
/save 2 mine             your own message of turn 2
/save 2 --vouch          turn 2's answer, and you vouch for it
/save 2 --title "MOF-5 uptake" --tag mof --graph methods
/save notes.md           (unchanged) write the transcript to a file
```

With the transcript cursor on a message, `s` saves that message (the focused
row's footer reads `s save tN`, the turn number `/save` takes; on an answer,
`s` works on the turn's final reply). A save while the agent is still
answering is refused: wait for the answer, then save. A save publishes to
the project's shared graph, so the first one in a console session asks once:
press `s` again (or repeat the same `/save`) within 8 s to save; later saves
in that session do not ask (2026-10-25). The text
is taken from the session as it was shown, never re-typed by the model, and
the item records the session and turn it came from. The tier follows K-1:
your own message lands **established**; an assistant answer lands
**proposed** (origin `agent_authored_user_kept`), so a teammate establishes
it in `r4 knowledge review`, unless you vouch for it: then it lands
established with you as its establisher (origin
`agent_authored_user_vouched`). Vouch when you save: a `--vouch` on an
answer already kept is refused by name (the stored item would not change);
establish it with `r4 knowledge review` instead. The receipt prints the tier
UMRS decided, for example `saved t3 (answer) → example-project-b · proposed (kept) ·
job … — r4 knowledge ls`. Text, a title or a tag holding a credential is
refused by name, and nothing is saved: a token shape (JWT, GitHub, GitLab,
OpenAI-style, Stripe, Google, Hugging Face, Slack, AWS key id, private key,
`user:password@` in a URL, `Bearer …`), or a credential key with a
secret-looking value (`password: …`, `"api_key": "…"`); a key alone, as in
`pwd = os.getcwd()`, is not refused. The session's `/acquisition` mode
applies (`disabled` refuses, `pinned` saves into the pinned graph). The
saved item shows in `r4 knowledge ls` with your name and `(kept)` or
`(vouched)`.

The agent's `knowledge_build_from_context` tool is an agent write since
2026-10-24 (review of K1 part B): the model types its excerpt, so it needs
an approved proposal and lands proposed, like `knowledge_ingest`. To keep a
turn verbatim, use `/save`.

### Corpus verbs (same surface as `r4 rag`)

```bash
r4 knowledge upload paper.pdf [--project ID]
r4 knowledge bulk-upload ./papers/ [--project ID]
r4 knowledge docs list [--project ID]
r4 knowledge docs delete DOC_ID [--project ID] [--yes]   # same delete as below
r4 knowledge stats [--project ID]
r4 knowledge inventory [--project ID]   # Catalogue rows with canonical badges
r4 knowledge health                     # Plane reconciliation health per asset
```

### Which web sites can be ingested

Added 2026-10-25. When you ingest a URL or a git repository (`r4 knowledge
graph-ingest … --url URL`, or a URL handed to the assistant), the UMRS server
fetches it, not your workstation. It fetches only from the web sites on the
site's **ingest allowlist**. To see that list:

```bash
r4 knowledge allowlist                               # the sites, grouped
r4 knowledge allowlist --check https://doi.org/10.1038/s41586-020-2649-2
r4 knowledge allowlist --json                        # the server's answer
```

The RIKEN fronts allow every public web site, and `r4 knowledge allowlist`
says so. A stricter front lists its sites instead; `[*.]nature.com` means
`nature.com` and its subdomains. Many stricter fronts use the recommended list: DOI and index services (doi.org, Crossref, OpenAlex,
ORCID), preprint servers and data repositories (arXiv, bioRxiv, Zenodo,
figshare, OSF, Dryad), life-science and materials databases (NCBI/PubMed,
Europe PMC, RCSB PDB, UniProt, EMBL-EBI, Materials Project, NIST), the large
publishers (a DOI link redirects to the publisher, and every redirect is
checked against the list too), RIKEN's public web sites, J-STAGE and CiNii, GitHub, GitLab,
Hugging Face, Read the Docs, PyPI and Wikipedia.

`--check` tells you whether a URL's host (and port) is on the list. It exits
0 when it is and 1 when it is not. The other checks run only when the fetch
happens: the site must have a public address outside the front's own network
(private addresses and the RIKEN intranet are refused whatever the list
says), only ports 80 and 443 are used unless
the list names another, the download is capped in size and time, and the
content is scanned for credentials and personal data before it is stored.

A few DOI links fail although `--check` says allowed: IEEE and Springer
books redirect an https DOI to plain http, which is never followed, and RSC
takes more than five redirects. Ingest the publisher's https article URL
instead (for example `https://ieeexplore.ieee.org/document/771073`).

A refused URL fails with a message that names the host, for example
`domain "blog.example.net" is not on this site's ingest allowlist`. To have
a site added, ask your UMRS operator: the list is the front's `site.yaml`
setting `extra.knowledge_ingest.allowed_domains`, and a change takes effect
at the next deployment of the front.
Files on your own disk need no allowlist: `r4 knowledge upload FILE`.

Since 2026-10-25 your project's web policy applies too: the same setting
that bounds the agent's web access in that project (off, allowlist or
public), set by an admin and narrowed by the project owner
([ADR-099](../adr/099-session-policy-and-quotas.md#amendment-5-2026-10-25-knowledge-url-ingest-follows-the-web-policy)).
`r4 knowledge allowlist` prints it for the current project (`-p ID` for
another) and the rule that set it. With **off**, URL and repo ingest are
refused. With an **allowlist**, only its sites are fetched (`.arxiv.org`
means arXiv and its subdomains), and a DOI that redirects to a publisher off
the list is refused at the redirect. With **public**, the site list above
applies. `--check` applies both. To change it, ask the project owner or an
admin.

### Deleting an artifact

```bash
r4 knowledge delete art-0123abcd [--project ID]   # asks to confirm
r4 knowledge delete art-0123abcd --yes --json     # scripted
```

One transaction on the server deletes the artifact row and everything derived
from it: file, chunk and semantic nodes (and the chunks a rag upload minted),
their vectors and edges, its IR rows and its verification findings. Its
`knowledge_events` rows stay, with the artifact link cleared, so the audit
trail survives. A failure part-way deletes nothing.

After the commit the stored bytes are deleted, best-effort. Bytes another
artifact row still references (same content hash) are kept (`kept_shared`).
A failed object delete is shown as `failed` with its reason; the rows stay
deleted.

The delete acts on your project only. An id of another project, an unknown
id, or an id already deleted answers 404 and deletes nothing. It is a user
verb: agents and Companion tools cannot delete.

From the SDK: `Client().knowledge.delete_artifact(artifact_id, project_id=...)`
returns the counts per row kind and the object outcomes.

### Reviewing proposed knowledge (`r4 knowledge review`)

Added 2026-10-24 (knowledge plan K1.5). Agents and research loops write
knowledge as **proposed**; a person establishes it. So do the entities and
relations a model extracts from **your own** uploads (decision K-1, ADR-084
D-2): the document and its structure land established, the model's reading
of it lands proposed. Expect most inbox rows to come from that.

The review inbox lists, for the project's graphs and your personal graphs,
the pending graph-extension proposals (with who made them) and the proposed
nodes and edges, one line per run with its row count and origin (`agent`,
`loop`, or `extracted` for a model's extraction from an upload):

```bash
r4 knowledge review [-p PROJECT] [--graph G] [--json]   # the inbox, by run
r4 knowledge review --rows                              # every row, with its id
r4 knowledge review approve gp-1234                     # approve a proposal
r4 knowledge review reject gp-1234                      # decline it
r4 knowledge review establish art-5678                  # promote one row
r4 knowledge review establish --run loop-42             # every row of one run
```

The inbox shows the newest rows of each graph and says when a graph has
more. `establish --run` and `establish <row-id>` reach a row however old it
is. The run is the loop id for loop findings and the artifact id for a
model's extraction from an upload. `establish --run` promotes the run's
nodes and the edges that leave them in one action (ADR-084 D-2 "establish
run"). `approve` and `reject` need no current project.

Decisions and promotions are always sent as your act, never as the agent's:
the server refuses an agent decision or promotion by name, and the command
refuses to decide when the assistant runs it in its shell. Rows show their
origin and run; `r4 knowledge ls` shows who added each item (K1.3).

From the SDK: `Client().knowledge.graph_proposed(graph_id, run_id=...)` and
`Client().knowledge.establish(graph_id, row_id, kind="node"|"edge")`.

### Sources in answers

Since 2026-10-24 (knowledge plan K1.4), every answer that used a knowledge
tool (`search_docs`, `rag_query`, `knowledge_query`) lists its sources with
session labels (`[S3]`), and the assistant can read a passage with its
context through `open_passage`. The answer ends with a short footer naming
the sources and the citation check.

In the console, an answer's citation block is headed **Citations:** when the
answer cites passages, and **Retrieved (not cited):** when it cites none of
the passages its turn retrieved; the reply header says `cited N` or
`retrieved N` accordingly. Put the transcript cursor on the reply and press
`o` to open a preview of the passage. `←`/`→` or `[`/`]` step through the
sources, `↑`/`↓` and PgUp/PgDn scroll a long passage, and Esc closes the
preview. The preview fetches the passage under the server's access check: a
passage you may not read is not shown.

---

## 27.2 Explorer TUI (`r4 knowledge explore`)

The interactive cockpit — three panes in one frame:

```
┌ Sections ──────────┬ Documents ────────────────┬ Detail ───────────────┐
│ ◐ artifacts     42 │ ◐ paper_on_mofs.pdf    │ Title: paper_on_mofs    │
│ ◑ findings      3  │ ◑ attention_notes.md   │ Lineage: derived from   │
│ ✓ memory        12 │ ✓ kb_query_notes.md    │ artifact 'a2l-src-01'…  │
│ ◐ plans         2  │                        │ Ancestors: 1 nodes      │
└────────────────────┴────────────────────────────────────────────────────┘
```

- **Sections** — the four knowledge surfaces of the project: artifacts,
  findings, memory, plans, each with a readiness badge (✓ ready,
  ◐ partial, ✗ insufficient).
- **Documents** — rows for the selected section, again with badges.
- **Detail** — the selected row's story. For artifacts this includes the
  **A2L lineage** (which source/artifact the content derived from).
- Keys: arrows/Tab to pan across panes, `e` to **export** the project's
  `.kb` bundle, `r` to refresh, `q` to quit.

Launch:

```bash
r4 knowledge explore            # current project
r4 knowledge explore proteomics # explicit project
```

Healthy use: check coverage *after* ingest, before kicking off an
autonomous research loop (Section 26) — fix the 3 findings first.

---

## 27.3 Bundle Export (signed `.kb.tar.gz`)

A bundle is the portable snapshot of what a project knows: artifacts
(metadata), documents, knowledge-graph nodes and edges with their node
vectors, embeddings, catalog rows and A2L links. It also carries the
project's Scientific Catalog record (name, version, contributors, licence)
and an integrity manifest (content hash + HMAC signature). The export is a
durable UMRS job:

```bash
r4 project export proteomics            # starts the job; prints the archive key
r4 project export proteomics --with-raw # + original files (owner; licence; see below)
r4 knowledge export-download <job-id>   # when done: fetches <job-id>.kb.tar.gz
r4 knowledge info <job-id>.kb.tar.gz    # per-table counts + format version
```

- The archive lands under your project's own storage prefix
  (`<project>/knowledge-exports/<job>.kb.tar.gz`); only members of the project
  can download it.
- Only rows the reconciliation check calls complete are exported. The
  manifest records what was left out and why.
- `--strict` exits non-zero when the export is partial.
- **Raw materials (opt-in):** `r4 project export --with-raw` also carries
  the original files (papers, code, data), so the receiving installation
  can re-extract and re-embed from source.
  - Only the project owner or an admin can do this, and the project's
    Scientific Catalog must state a licence (`r4 catalog patch … --licence`).
  - A sensitive project (or one with no data class set) also needs
    `--acknowledge-sensitive`. The release is audited and recorded in the
    bundle.
  - Caps are 64 MiB per file and 1.5 GiB in total. Files skipped by the
    caps, unreadable ones and ones excluded by a catalog `raw` redaction
    rule are counted in the manifest's `raw` block.
- Every artifact row carries a **canonical document identity**
  (`document_id = kdoc-…`), computed from the content, not the file name.
  Text that differs only in line endings, a BOM, Unicode form or trailing
  whitespace gets the same id.

`r4 knowledge export` (without `project`) still exists: it writes a
**client-side projection** — graph and catalog only, unsigned — for quick
local inspection. It is not a full bundle.

---

## 27.4 Bundle Import (`r4 knowledge import`)

```bash
r4 knowledge import <job-id>.kb.tar.gz                      # into your current project
r4 knowledge import other-site.kb.tar.gz --allow-unsigned   # a bundle this site cannot verify
r4 knowledge import <job-id>.kb.tar.gz --no-raw             # knowledge only, decline raw files
```

The archive is uploaded and restored **into your current project** (every
row is retargeted to it). Output:

```
✓ Imported (verified) into project proteomics-v2
  node count      : 57
  edge count      : 41
  document count  : 38
  embedding count : 198
  Same document already present under another id: 1
    art-3f2a = local art-91c0 (kdoc-5e1d…)
```

Properties you can rely on:

- **Verified or explicit.** A signed bundle is checked against this site's
  key. The signature covers every table's contents, not only the manifest
  (bundles exported since 2026-09-24; older ones show "manifest verified;
  table contents not pinned"). An unsigned bundle, or one signed by another
  installation, needs `--allow-unsigned`.
- **Hostile-input safe.** Only regular files and directories are extracted:
  no absolute or `..` paths, with member-count and size caps.
- **Idempotent.** Re-importing skips what is already there (artifacts
  deduplicate on content per project; graph rows upsert). The same id with
  different content aborts with a deterministic diff instead of overwriting.
- **Same document, reported.** An incoming document already present under a
  different id is listed; nothing is merged.
- **Vectors only under a trusted model.** Node vectors import only when this
  site's knowledge embedder matches the bundle's model (name and dimension).
  The rest are counted for re-embedding.
- **Catalog travels.** The Scientific Catalog record in the manifest is
  re-registered for your project.
- **Raw files, verified first.** When the bundle carries raw files, every
  one is re-hashed before anything is written: one mismatched or unlisted
  file refuses the whole import. Each file is then stored in this
  installation's own storage and linked to its artifact. `--no-raw` takes
  the knowledge and declines the files.

### Admin: server-side restore (`r4 knowledge restore`)

Restoring a bundle **directory that already sits on the server**
(`r4 knowledge restore /srv/kb/<bundle> [--retarget P] [--no-verify]`) is an
operator/admin action. Users get `bundle.forbidden`; use
`r4 knowledge import` instead.

---

## 27.5 Recipes

**Move a project to another installation:**

```bash
r4 project export proteomics && r4 knowledge export-download <job-id>
# on the other installation, in the destination project:
r4 project switch proteomics && r4 knowledge import <job-id>.kb.tar.gz --allow-unsigned
r4 knowledge explore proteomics       # sanity-inspect
```

**Check whether the research layer is fit for autonomous operation:**

```bash
r4 knowledge explore            # visual pass (3 findings open?)
r4 knowledge query "baseline claim X" --limit 5
r4 knowledge query "result" --scope catalog --json | jq '.query.results | length'
```

**Audit what enters a project from a shared bundle:**
`r4 knowledge info bundle.kb.tar.gz` lists per-table counts and the declared
format version — compare with the published counts before importing.
