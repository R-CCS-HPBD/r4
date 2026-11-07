# r4-teamengine-spi

Team Engine SPI for the R4 platform (ADR-020, `docs/adr/020-team-engine-spi.md`).

This package defines the seam between the R4 runtime and pluggable
multi-agent orchestration engines:

- `TeamEngine` / `EngineRun` / `CompiledPlan` — adapter interfaces
- `RuntimeServices` — reverse-control brokers (tools, models, memory,
  artifacts, events, budget, workers); engines never hold credentials
- Plugin loader — `r4.team_engines` entry-point discovery with trust tiers
- `testing` — in-memory mock brokers + a stub engine for conformance tests

Adapters (e.g. `r4-engine-langgraph`) depend on this package and
`r4-contracts` — never on `r4`, `umrs`, or `gateway` internals.
