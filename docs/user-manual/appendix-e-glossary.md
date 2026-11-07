# Appendix E — Glossary

| Term | Definition |
|------|-----------|
| **a2l** | Audio-to-LLM module for speech-based interaction with the platform |
| **Companion** | `r4 companion` — JS/Ink terminal-based interactive chat UI |
| **ComposeTarget** | ExecutionTarget implementation for Docker Compose (dev/test) |
| **Context Pack** | Assembled context (summary + recent messages + memory) for LLM requests |
| **Context Store** | UMRS subsystem for durable conversation history, summaries, and memory |
| **Durable Job** | A crash-safe Job/Attempt tracked in Postgres with retry semantics |
| **EgressGuard** | Mandatory proxy controlling all outbound network access from mini-RiVault |
| **Endpoint** | A running Mini-RiVault instance exposing LLM/RAG/MCP services |
| **Execution Authority** | Immutable label (`slurm`, `k8s`, `compose`) assigned once per job |
| **ExecutionTarget** | Abstraction for resource allocation (SlurmTarget, K8sTarget, ComposeTarget) |
| **Gateway** | Platform proxy handling auth, routing, rate-limiting, and auditing |
| **GenAI** | Deployment name for the platform (same project as RiVault) |
| **HPC** | High-Performance Computing — primary deployment target (Slurm/Singularity) |
| **K8sTarget** | ExecutionTarget implementation for Kubernetes |
| **LiteLLM** | OpenAI-compatible proxy in front of vLLM, providing model routing |
| **MCP** | Model Context Protocol — standard for tool-calling server integration |
| **Mini-RiVault** | Self-contained security boundary (vLLM + LiteLLM + RAG + MCP per endpoint) |
| **Model Registry** | UMRS subsystem for model metadata, ACLs, staging, and download jobs |
| **ModelRef** | Typed reference to a model location (`filesystem_path`, `pvc_ref`, `hf_repo_ref`) |
| **OIDC** | OpenID Connect — identity federation for enterprise SSO |
| **Pipeline SDK** | Local Python SDK for building multi-step data processing pipelines |
| **Plugin** | User-defined computation unit registered and executed through UMRS |
| **Presigned URL** | Time-limited, unguessable S3 URL for file access without credentials |
| **r4** | Client package — CLI, SDK, and companion for platform interaction |
| **r4-contracts** | Shared DTOs and schemas between r4 and other packages |
| **RAG** | Retrieval-Augmented Generation — document-aware LLM responses |
| **Reconciler** | UMRS component that recovers in-flight jobs after server restart |
| **RiVault** | Logical role/identity of the platform (deployment name is GenAI) |
| **RuntimeWrapper** | Abstraction for container launch inside an allocation |
| **SingularityWrapper** | RuntimeWrapper implementation for Singularity/Apptainer containers |
| **SlurmTarget** | ExecutionTarget implementation for Slurm HPC job scheduler |
| **Source** | External LLM provider (OpenAI, Gemini, etc.) registered via `r4 source` |
| **Tier** | Endpoint ownership level: `system`, `project`, or `user` |
| **UMRS** | Unified Model Runtime Service — data plane, orchestration, and storage |
| **vLLM** | High-performance LLM inference engine running inside mini-RiVault |
| **Workflow** | Server-side registered computation graph uploaded via `r4 workflow upload` |
| **ZMQ** | ZeroMQ — data-plane transport for file and binary operations |
