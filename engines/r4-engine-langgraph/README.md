# r4-engine-langgraph

LangGraph adapter for the R4 Team Engine SPI (ADR-020).

`TeamSpec` compiles into a LangGraph `StateGraph`:

- one node per `AgentSpec` — LLM calls go through the run's `ModelBroker`
  (never a provider SDK), budget-metered and audited by the runtime
- `EdgeSpec` defines the DAG (conditions rejected in v1 — validate fails fast)
- checkpointing uses a LangGraph `MemorySaver`; blobs live in the run's
  `ArtifactBroker`

Install: `uv pip install r4-engine-langgraph` (or `uv sync --all-packages`
from the monorepo). The adapter registers itself on the
`r4.team_engines` entry-point group; `r4 team engines` then shows
`langgraph` alongside `native`.
