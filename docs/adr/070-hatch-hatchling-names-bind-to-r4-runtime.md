# ADR-070: Hatch / Hatchling — the names bind to the R4 runtime

**Status:** Accepted (records the de-facto answer the tree already implements; gap row D-20)
**Date:** 2026-09-24
**Relates to:** ADR-022 (default MCP stdio adapters), ADR-023 (default MCP hardening contract), ADR-029 (MCP twin registration convergence), D-53 (naming), D-23 (catalog names the MCP servers)

## Context

The design uses **Hatch** and **Hatchling** as capability names for the
custom-MCP-server lane: Hatch builds, packages and publishes MCP servers;
Hatchling discovers, pulls and runs them where the user chooses
(design §"Custom MCP servers via Hatch/Hatchling"). The names read like
external projects, and at least one upstream community project plus an
unrelated PyPI build tool (`hatchling`) share the spelling — so "do we
depend on an upstream, or is this us?" is a real question a contributor
or agent could answer wrongly.

The tree answers unambiguously: both roles are implemented by R4 itself.

- **Hatch (build/publish)**: `r4 mcp hatch create / install / connect /
  share / search` (`r4/src/r4/_cli/mcp.py`) — packaging, publishing and
  the community-manifest package format.
- **Hatchling (discover/pull/run)**: R4's own bridge
  (`r4/src/r4/integrations/hatchling.py`) — discovers, pulls and starts
  registry packages; plus the in-repo default registry
  (`mcp-servers/hatch-registry/`, six packages, ADR-022/023) and the
  server-side instance accounting lane (`MCP_SERVER` durable kind, D-22).
- **Upstream externals**: any community "Hatchling" project remains an
  optional external source of packages — never a runtime dependency;
  R4 never imports or requires it.

## Decision

**"Hatch" and "Hatchling" in the design and the code name the R4-side
runtime — R4's own packaging CLI and its own discover/pull/run bridge —
not any upstream project.** Spelling stays (D-53); no identifier
migrates. Where docs must disambiguate, the PyPI build tool is "the
Python `hatchling` package" and ours is "Hatch/Hatchling (R4)".

Consequences:

- No dependency work, no import seams to upstream — the lane is and
  stays self-contained (contracts fence unaffected, ADR-005).
- The Hatch/Hatchling wording in the design is read as OUR capability
  contract: the lifecycle sentences ("Hatchling starts, health-checks,
  restarts and stops servers; UMRS accounts for them like any other
  session") bind to R4 code (the bridge + the `MCP_SERVER` durable
  lane), which is what D-22 tracks.
- An upstream package registry, if ever adopted, is a NEW decision
  (fetch source policy) — not a reading of this one.

## Verification pointers

- `r4/src/r4/_cli/mcp.py` — the `hatch` verb family (create/install/
  connect/share/search)
- `r4/src/r4/integrations/hatchling.py` — the discover/pull/run bridge
- `mcp-servers/hatch-registry/` — the default registry (six packages)
- `umrs/src/umrs/jobs/mcp_server.py` — the server-side instance lane
