# UMRS/R4 Boundary Specification

> Design reference for RC1 and beyond.
> **UMRS owns scientific knowledge. R4 orchestrates scientific research.**

---

## 1. Boundary Principle

The boundary between UMRS and R4 is defined by a single principle:

> **UMRS owns scientific knowledge. R4 orchestrates scientific research.**

This means:

- UMRS is the **single source of truth** for all scientific knowledge.
- R4 is the **orchestration layer** that coordinates scientific research workflows.
- R4 **never** stores scientific knowledge locally.
- R4 **always** accesses scientific knowledge through UMRS APIs.
- UMRS **never** orchestrates research workflows.
- UMRS **always** provides knowledge management APIs for R4 to consume.

---

## 2. Ownership Matrix

For every capability, the owner is either UMRS or R4.

| Capability | Owner | Rationale |
|-----------|-------|-----------|
| Scientific memory (generated code, experiment logs, plots, analysis, hypotheses, findings, failed experiments) | UMRS | Durable, searchable, reusable across applications |
| Knowledge model (Paper, Notebook, Code, Dataset, Algorithm, Hypothesis, Observation, Conclusion, Question, Assumption, Finding, Evidence, Reference) | UMRS | First-class scientific object lifecycle, reusable |
| Knowledge graph (typed relationships between scientific objects) | UMRS | Graph storage, traversal, query, reusable |
| Artifact catalog (object metadata, modality detection, text extraction, perception summaries, stats) | UMRS | Postgres `catalog_objects` metadata + S3 blobs, reusable |
| Embeddings (embedding creation, storage, retrieval, updates) | UMRS | pgvector-backed embedding management, reusable |
| RAG (chunking, embedding, indexing, hybrid search, reranking, context assembly) | UMRS | RAG pipeline, reusable |
| Search (unified search across catalog, knowledge graph, embeddings, RAG) | UMRS | Unified search interface, reusable |
| Evidence (evidence items with source, content, relevance score, citation) | UMRS | Evidence tracking, reusable |
| Experiment registry (experiment definitions, parameters, status, results, artifacts) | UMRS | Experiment lifecycle, reusable |
| Provenance (directed acyclic graph of what produced what) | UMRS | Provenance tracking, reusable |
| Context (conversations, messages, memory items, conversation summaries, memory vectors) | UMRS | Context store, reusable |
| Research planning (research objectives, hypotheses, experiments, validation methods, expected outcomes) | R4 | Research-specific, orchestration |
| Workflow orchestration (pipeline stages, phase transitions, workflow audit trail) | R4 | Orchestration logic |
| Agent coordination (multi-agent orchestration, agent spawning, agent governance) | R4 | Agent orchestration |
| Project lifecycle (project creation, ingestion, understanding, readiness, research, completion) | R4 | Project orchestration |
| User interfaces (CLI, TUI, Jupyter, MCP apps) | R4 | User-facing |
| Chat interaction (knowledge verification chat, companion, research director) | R4 | Chat orchestration |
| Experiment execution policies (when to run experiments, how to validate, when to stop) | R4 | Execution policies |
| Autonomous research (autonomous research loop, hypothesis revision, branching) | R4 | Autonomous orchestration |
| Verification chat (evidence-backed Q&A over UMRS knowledge) | R4 | Verification orchestration |

---

## 3. UMRS Public API Specification

### 3.1 `umrs.api.scientific_memory`

**Purpose:** Durable store for all artifacts generated during scientific research.

**Owner:** UMRS

**Public Interface:**
```python
class ScientificMemoryAPI:
    def add(self, *, project_id, entry_type, content, source, metadata) -> str
    def get(self, entry_id: str) -> MemoryEntry | None
    def list_entries(self, *, project_id, entry_type, status, limit) -> list[MemoryEntry]
    def search(self, query: str, *, project_id, entry_type, limit) -> list[MemoryEntry]
    def update_status(self, entry_id: str, status: MemoryEntryStatus) -> bool
    def count(self, *, project_id, entry_type, status) -> int
    def get_timeline(self, project_id: str) -> list[MemoryEntry]
```

**Data Ownership:** UMRS owns all memory entries.

**Persistence Model:** UMRS context store (Postgres-backed). Entries are immutable once created (except for status changes).

---

### 3.2 `umrs.api.knowledge_model`

**Purpose:** First-class scientific object model with lifecycle management.

**Owner:** UMRS

**Public Interface:**
```python
class KnowledgeModelAPI:
    def create_object(self, *, project_id, object_type, content, metadata) -> str
    def get_object(self, object_id: str) -> KnowledgeObject | None
    def update_object(self, object_id: str, *, content, metadata) -> bool
    def delete_object(self, object_id: str) -> bool
    def list_objects(self, *, project_id, object_type, status, limit) -> list[KnowledgeObject]
    def search_objects(self, query: str, *, project_id, object_type, limit) -> list[KnowledgeObject]
    def get_object_history(self, object_id: str) -> list[ObjectVersion]
```

**Data Ownership:** UMRS owns all scientific objects.

**Persistence Model:** PostgreSQL `knowledge_objects` table with JSONB content, version tracking, and soft deletes.

---

### 3.3 `umrs.api.knowledge_graph`

**Purpose:** Typed relationship graph between scientific objects.

**Owner:** UMRS

**Public Interface:**
```python
class KnowledgeGraphAPI:
    def add_node(self, *, node_id, node_type, title, metadata) -> None
    def add_edge(self, *, source_id, target_id, relation_type, weight, confidence, metadata) -> None
    def get_node(self, node_id: str) -> KnowledgeNode | None
    def get_outgoing(self, node_id: str) -> list[KnowledgeEdge]
    def get_incoming(self, node_id: str) -> list[KnowledgeEdge]
    def get_neighbors(self, node_id: str) -> list[str]
    def get_related(self, node_id: str, relation_type: RelationType) -> list[str]
    def find_path(self, source_id: str, target_id: str, max_depth: int = 5) -> list[str] | None
    def query(self, query: GraphQuery) -> list[KnowledgeNode]
```

**Data Ownership:** UMRS owns the knowledge graph.

**Persistence Model:** PostgreSQL `knowledge_nodes` and `knowledge_edges` tables with typed relationships, confidence scores, and metadata.

---

### 3.4 `umrs.api.artifact_catalog`

**Purpose:** Object metadata tracking with modality detection, text extraction, perception summaries, and per-project stats.

**Owner:** UMRS

**Public Interface:**
```python
class ArtifactCatalogAPI:
    def ingest_object(self, *, s3_key, project_id, owner, acl_scope) -> dict
    def search_catalog(self, query: str, *, project_id, limit) -> list[CatalogHit]
    def read_catalog_object(self, *, s3_key, project_id, offset, length) -> CatalogReadResponse
    def get_object_meta(self, s3_key: str) -> CatalogObjectMeta | None
    def project_stats(self, project_id: str) -> dict
```

**Data Ownership:** UMRS owns all catalog metadata.

**Persistence Model:** Postgres `catalog_objects` rows are the metadata source of truth (migration `0054`); extracted text and structured-knowledge blobs remain in S3 under `_data_catalog/`. Requires `umrs_db` (503 when the pool is unavailable, same as jobs).

---

### 3.5 `umrs.api.embeddings`

**Purpose:** Embedding creation, storage, retrieval, and updates.

**Owner:** UMRS

**Public Interface:**
```python
class EmbeddingsAPI:
    def create_embedding(self, *, object_id, text, model, project_id) -> str
    def get_embedding(self, embedding_id: str) -> Embedding | None
    def search_similar(self, *, query_embedding, top_k, project_id) -> list[EmbeddingMatch]
    def update_embedding(self, embedding_id: str, *, text, model) -> bool
    def delete_embedding(self, embedding_id: str) -> bool
```

**Data Ownership:** UMRS owns all embeddings.

**Persistence Model:** pgvector `embeddings` table with vector storage, model metadata, and project scoping.

---

### 3.6 `umrs.api.rag`

**Purpose:** RAG pipeline with chunking, embedding, indexing, hybrid search, reranking, and context assembly.

**Owner:** UMRS

**Public Interface:**
```python
class RAGAPI:
    def index_document(self, *, object_id, content, chunker_spec, embedder_spec, project_id) -> str
    def search(self, query: str, *, top_k, threshold, project_id) -> list[SearchResult]
    def assemble_context(self, query: str, *, token_budget, project_id) -> RAGContext
```

**Data Ownership:** UMRS owns all RAG indexes.

**Persistence Model:** pgvector for vector storage, S3 for index artifacts, PostgreSQL for pipeline definitions and RAG profiles.

---

### 3.7 `umrs.api.search`

**Purpose:** Unified search across catalog, knowledge graph, embeddings, and RAG.

**Owner:** UMRS

**Public Interface:**
```python
class SearchAPI:
    def search(self, query: str, *, scope, depth, project_id, limit) -> SearchResults
    def search_catalog(self, query: str, *, project_id, limit) -> list[CatalogHit]
    def search_knowledge_graph(self, query: str, *, project_id, limit) -> list[KnowledgeNode]
    def search_embeddings(self, query: str, *, project_id, limit) -> list[EmbeddingMatch]
    def search_rag(self, query: str, *, project_id, limit) -> list[SearchResult]
```

**Data Ownership:** UMRS owns the unified search interface.

**Persistence Model:** Composite — delegates to catalog, knowledge graph, embeddings, and RAG subsystems.

---

### 3.8 `umrs.api.evidence`

**Purpose:** Evidence items with source, content, relevance score, and citation.

**Owner:** UMRS

**Public Interface:**
```python
class EvidenceAPI:
    def add_evidence(self, *, project_id, source_type, source_id, content, relevance_score, citation) -> str
    def get_evidence(self, evidence_id: str) -> Evidence | None
    def list_evidence(self, *, project_id, source_type, limit) -> list[Evidence]
    def search_evidence(self, query: str, *, project_id, limit) -> list[Evidence]
    def get_evidence_for_claim(self, claim_id: str) -> list[Evidence]
```

**Data Ownership:** UMRS owns all evidence.

**Persistence Model:** PostgreSQL `evidence` table with source type, content, relevance score, citation, and project scoping.

---

### 3.9 `umrs.api.experiment_registry`

**Purpose:** Experiment definitions, parameters, status, results, and artifacts.

**Owner:** UMRS

**Public Interface:**
```python
class ExperimentRegistryAPI:
    def create_experiment(self, *, project_id, hypothesis_id, protocol, parameters, validation_method) -> str
    def get_experiment(self, experiment_id: str) -> Experiment | None
    def update_experiment_status(self, experiment_id: str, status: ExperimentStatus) -> bool
    def add_experiment_result(self, experiment_id: str, *, results, artifacts, logs) -> str
    def list_experiments(self, *, project_id, hypothesis_id, status, limit) -> list[Experiment]
    def get_experiment_history(self, project_id: str) -> list[Experiment]
```

**Data Ownership:** UMRS owns all experiment records.

**Persistence Model:** PostgreSQL `experiments` table with JSONB protocol, parameters, results, and artifacts.

---

### 3.10 `umrs.api.provenance`

**Purpose:** Directed acyclic graph of what produced what.

**Owner:** UMRS

**Public Interface:**
```python
class ProvenanceAPI:
    def record_provenance(self, *, object_id, inputs, model_refs, code_versions, artifacts) -> str
    def get_provenance(self, object_id: str) -> ProvenanceRecord | None
    def get_provenance_lineage(self, object_id: str) -> list[ProvenanceRecord]
    def list_provenance(self, *, project_id, limit) -> list[ProvenanceRecord]
    def diff_provenance(self, object_id_a: str, object_id_b: str) -> ProvenanceDiff
```

**Data Ownership:** UMRS owns all provenance records.

**Persistence Model:** PostgreSQL `provenance` table with JSONB inputs, model refs, code versions, and artifacts.

---

### 3.11 `umrs.api.context`

**Purpose:** Conversations, messages, memory items, conversation summaries, and memory vectors.

**Owner:** UMRS

**Public Interface:**
```python
class ContextAPI:
    def create_conversation(self, *, project_id, title) -> str
    def get_conversation(self, conversation_id: str) -> Conversation | None
    def list_conversations(self, *, project_id, limit) -> list[Conversation]
    def append_messages(self, conversation_id: str, messages: list[Message]) -> None
    def pack_context(self, *, conversation_id, token_budget) -> ContextPack
    def store_memory(self, *, project_id, key, value, source, metadata) -> str
    def search_memory(self, query: str, *, project_id, limit) -> list[MemoryItem]
```

**Data Ownership:** UMRS owns all context data.

**Persistence Model:** PostgreSQL `conversations`, `messages`, `conversation_summaries`, `memory_items`, and `memory_vectors` tables.

---

## 4. R4 Public Interface Specification

### 4.1 `r4.research_planner`

**Purpose:** Generate research plans from project knowledge, external knowledge, and scientific memory.

**Owner:** R4

**Public Interface:**
```python
class ResearchPlanner:
    def plan_research(self, *, project_id, research_goal, umrs_client) -> ResearchPlan
    def generate_objectives(self, *, research_goal, knowledge_base) -> list[ResearchObjective]
    def generate_hypotheses(self, *, objective, knowledge_base, external_refs) -> list[ResearchHypothesis]
    def generate_experiments(self, *, hypothesis, knowledge_base, memory_store) -> list[ResearchExperiment]
```

**Data Ownership:** R4 owns the research plan. R4 queries UMRS for knowledge.

---

### 4.2 `r4.workflow_orchestrator`

**Purpose:** Orchestrate the scientific workflow pipeline.

**Owner:** R4

**Public Interface:**
```python
class WorkflowOrchestrator:
    def run_project_workflow(self, *, project_id, directories, research_goal, max_iterations, auto_approve) -> WorkflowResult
    def get_workflow_status(self, project_id: str) -> WorkflowStatus
    def get_workflow_transitions(self, project_id: str) -> list[WorkflowTransition]
    def replay_workflow(self, project_id: str) -> WorkflowResult
```

**Data Ownership:** R4 owns the workflow orchestration logic. R4 stores transitions in UMRS as provenance.

---

### 4.3 `r4.autonomous_research`

**Purpose:** Execute the autonomous research loop.

**Owner:** R4

**Public Interface:**
```python
class AutonomousResearchLoop:
    def start_loop(self, *, project_id, research_goal, max_iterations, umrs_client) -> ResearchLoop
    def run(self, loop: ResearchLoop) -> ResearchLoop
    def pause(self, loop: ResearchLoop) -> None
    def resume(self, loop: ResearchLoop) -> None
    def get_loop_status(self, loop_id: str) -> LoopStatus
```

**Data Ownership:** R4 owns the research loop execution logic. R4 stores loops in UMRS.

---

### 4.4 `r4.verification_chat`

**Purpose:** Evidence-backed Q&A over UMRS knowledge.

**Owner:** R4

**Public Interface:**
```python
class VerificationChat:
    def ask(self, question: str, *, project_id, umrs_client) -> VerificationAnswer
    def list_questions(self) -> list[str]
    def get_evidence(self, answer_id: str) -> list[Evidence]
```

**Data Ownership:** R4 owns the verification chat logic. R4 stores answers in UMRS.

---

### 4.5 `r4.project_manager`

**Purpose:** Manage project lifecycle.

**Owner:** R4

**Public Interface:**
```python
class ProjectManager:
    def create_project(self, *, project_id, name, description, storage_path) -> ResearchProject
    def ingest_directory(self, project: ResearchProject, *directories) -> int
    def understand(self, project: ResearchProject) -> ExtractedKnowledge
    def verify(self, project: ResearchProject, question: str) -> VerificationAnswer
    def check_readiness(self, project: ResearchProject) -> ResearchReadinessReport
    def approve_readiness(self, project: ResearchProject, user_id: str) -> bool
    def plan_research(self, project: ResearchProject, research_goal: str) -> ResearchPlan
    def run_research(self, project: ResearchProject, max_iterations: int) -> ResearchLoop
    def get_project_status(self, project_id: str) -> ProjectStatus
    def list_projects(self) -> list[ResearchProject]
```

**Data Ownership:** R4 owns the project lifecycle logic. R4 stores projects in UMRS.

---

## 5. Data Flow Across the Boundary

### 5.1 Ingestion

```
R4 ProjectManager.ingest_directory()
  ↓ (calls)
UMRS.api.artifact_catalog.ingest_object()
  ↓ (returns)
artifact_id
  ↓ (calls)
UMRS.api.knowledge_model.create_object()
  ↓ (returns)
object_id
  ↓ (calls)
UMRS.api.embeddings.create_embedding()
  ↓ (returns)
embedding_id
  ↓ (calls)
UMRS.api.knowledge_graph.add_node() and add_edge()
```

### 5.2 Verification

```
R4 VerificationChat.ask(question)
  ↓ (calls)
UMRS.api.search.search(query)
  ↓ (returns)
SearchResults
  ↓ (calls)
UMRS.api.evidence.get_evidence_for_claim(claim_id)
  ↓ (returns)
list[Evidence]
  ↓ (builds)
VerificationAnswer with evidence
```

### 5.3 Research

```
R4 AutonomousResearchLoop.run(loop)
  ↓ (calls)
UMRS.api.experiment_registry.create_experiment()
  ↓ (returns)
experiment_id
  ↓ (calls)
UMRS.api.jobs.create_job() to submit experiment as job
  ↓ (returns)
job_id
  ↓ (calls)
UMRS.api.jobs.get_job() to track experiment status
  ↓ (returns)
JobStatus
  ↓ (calls)
UMRS.api.scientific_memory.add() to register results
  ↓ (calls)
UMRS.api.knowledge_graph.add_edge() to link results to hypotheses
  ↓ (calls)
UMRS.api.evidence.add_evidence() to register evidence
  ↓ (calls)
UMRS.api.provenance.record_provenance() to track provenance
```

---

## 6. Boundary Enforcement

### 6.1 Import Rules

| Rule | Enforcement |
|------|-------------|
| R4 must not import from `umrs.data_catalog.store` | Code review + linting |
| R4 must not import from `umrs.api.context._ContextStore` | Code review + linting |
| R4 must not import from `umrs.api.vectors` directly | Code review + linting |
| R4 must not import `SentenceTransformerEmbeddings` directly | Code review + linting |
| R4 must not access pgvector directly | Code review + linting |
| R4 must not access S3 directly | Code review + linting |

### 6.2 Data Flow Rules

| Rule | Enforcement |
|------|-------------|
| R4 must not store scientific knowledge locally | Code review + architecture review |
| R4 must not create embeddings locally | Code review + linting |
| R4 must not store scientific memory in local JSON files | Code review + architecture review |
| R4 must not store knowledge graph in local memory | Code review + architecture review |

### 6.3 API Usage Rules

| Rule | Enforcement |
|------|-------------|
| R4 must use UMRS APIs for all knowledge management | Code review + architecture review |
| R4 must use UMRS APIs for all knowledge search | Code review + architecture review |
| R4 must use UMRS APIs for all knowledge storage | Code review + architecture review |
| R4 must use UMRS APIs for all knowledge retrieval | Code review + architecture review |

---

## 7. Boundary Violation Detection

The following violations should be detected by automated checks:

1. **R4 importing from UMRS internal modules** (e.g., `umrs.data_catalog.store`)
   - Detection: Import boundary validator
   - Severity: Hard-fail

2. **R4 creating embeddings locally**
   - Detection: Linting for `SentenceTransformerEmbeddings` imports in R4
   - Severity: Hard-fail

3. **R4 storing scientific knowledge in local memory**
   - Detection: Architecture review of new R4 modules
   - Severity: Hard-fail

4. **R4 accessing UMRS storage directly** (e.g., pgvector, S3)
   - Detection: Import boundary validator
   - Severity: Hard-fail

5. **R4 duplicating UMRS functionality**
   - Detection: Architecture review of new R4 modules
   - Severity: Hard-fail

---

## 8. Boundary Summary

The boundary between UMRS and R4 is defined by a single principle:

> **UMRS owns scientific knowledge. R4 orchestrates scientific research.**

This means:

- UMRS is the **single source of truth** for all scientific knowledge.
- R4 is the **orchestration layer** that coordinates scientific research workflows.
- R4 **never** stores scientific knowledge locally.
- R4 **always** accesses scientific knowledge through UMRS APIs.
- UMRS **never** orchestrates research workflows.
- UMRS **always** provides knowledge management APIs for R4 to consume.

The key architectural rule is:

- **R4 orchestrates scientific research.**
- **UMRS owns scientific knowledge.**
