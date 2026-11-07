"""
Search Planner (A-902)

Query decomposition and search planning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from r4.client import Client

    from .core import SearchConfig

logger = logging.getLogger(__name__)


class StepType(Enum):
    """Type of search step."""

    RAG_QUERY = "rag_query"
    WEB_SEARCH = "web_search"
    CODE_SEARCH = "code_search"
    PAPER_SEARCH = "paper_search"
    LAB_CATALOG_SEARCH = "lab_catalog_search"
    LAB_FILE_READ = "lab_file_read"
    FOLLOW_UP = "follow_up"
    SYNTHESIZE = "synthesize"


@dataclass
class SearchStep:
    """A single step in the search plan."""

    step_type: StepType
    query: str
    reasoning: str = ""
    depends_on: list[int] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_type": self.step_type.value,
            "query": self.query,
            "reasoning": self.reasoning,
            "depends_on": self.depends_on,
            "parameters": self.parameters,
            "order": self.order,
        }


class SearchPlanner:
    """
    Search Planner (A-902).

    Decomposes complex queries into search steps.
    """

    PLANNER_SYSTEM_PROMPT = """You are a search planning assistant. Your task is to
decompose a user query into a series of search steps.

Available search types:
- lab_catalog_search: Search lab data catalog (metadata grep over uploaded files)
- lab_file_read: Read uploaded lab file content (s3_key)
- rag_query: Search internal documents/knowledge base
- web_search: Search the web for information
- code_search: Search code repositories
- paper_search: Search academic papers (if enabled)

Lab data catalog notes:
- lab_catalog_search returns metadata only — use it to discover files;
  then lab_file_read for content.
- Uploads are indexed automatically; catalog_search before file_read to find s3_key.
- summary is greppable; for structured files (CSV/JSON) deterministic_summary (columns/keys)
  is authoritative - perception_summary may be LLM-generated and is tagged llm_summarized.
- Tags: vision_captioned, llm_summarized, perception_skipped (no model available),
  catalog_sample_truncated (large file sampled).
- For exact structure or analysis, always lab_file_read the file; do not trust
  summaries alone for CSV column names or numeric values.

For each step, provide:
1. step_type: One of the available types
2. query: The specific search query for this step
3. reasoning: Why this step is needed

Respond with a JSON array of steps. Example:
```json
[
  {"step_type": "rag_query", "query": "internal documentation on X",
   "reasoning": "Check internal docs first"},
  {"step_type": "web_search", "query": "latest research on X",
   "reasoning": "Get external context"}
]
```

Be strategic:
- Start with internal/RAG sources when available
- Use specific, targeted queries
- Minimize steps while being thorough
- Consider dependencies between steps
"""

    def __init__(self, client: "Client", config: "SearchConfig"):
        """
        Initialize search planner.

        Args:
            client: R4 client
            config: Search configuration
        """
        self.client = client
        self.config = config

    async def create_plan(
        self, query: str, *, warnings: list[str] | None = None
    ) -> list[SearchStep]:
        """
        Create a search plan for a query.

        Args:
            query: User's query
            warnings: Optional list to append warning messages to.

        Returns:
            List of SearchStep objects
        """
        self._warnings = warnings if warnings is not None else []

        # For shallow depth, use simple heuristic planning
        if self.config.depth.value == "shallow":
            plan = self._quick_plan(query)
        else:
            plan = await self._llm_plan(query)

        from .catalog_ground_truth import enrich_plan_with_raw_reads

        return enrich_plan_with_raw_reads(
            plan,
            query,
            enable_raw_read=self.config.enable_raw_read,
        )

    def _quick_plan(self, query: str) -> list[SearchStep]:
        """Create a quick plan without LLM."""
        steps = []
        order = 0

        if self.config.enable_catalog and self.config.project_id:
            steps.append(
                SearchStep(
                    step_type=StepType.LAB_CATALOG_SEARCH,
                    query=query,
                    reasoning="Search lab data catalog metadata",
                    order=order,
                )
            )
            order += 1

        # Always try RAG first if enabled
        if self.config.enable_rag and self.config.project_id:
            steps.append(
                SearchStep(
                    step_type=StepType.RAG_QUERY,
                    query=query,
                    reasoning="Search internal documents",
                    order=order,
                )
            )
            order += 1

        # Add web search if enabled
        if self.config.enable_web:
            steps.append(
                SearchStep(
                    step_type=StepType.WEB_SEARCH,
                    query=query,
                    reasoning="Search web for additional context",
                    order=order,
                )
            )
            order += 1

        # Add code search if relevant
        if self.config.enable_code and self._is_code_query(query):
            steps.append(
                SearchStep(
                    step_type=StepType.CODE_SEARCH,
                    query=query,
                    reasoning="Search code for implementation details",
                    order=order,
                )
            )

        return steps

    async def _llm_plan(self, query: str) -> list[SearchStep]:
        """Create a plan using LLM."""
        # Build context
        available_sources = []
        if self.config.enable_rag and self.config.project_id:
            available_sources.append("rag_query (internal documents)")
        if self.config.enable_catalog and self.config.project_id:
            available_sources.append("lab_catalog_search (lab data catalog)")
        if self.config.enable_raw_read and self.config.project_id:
            available_sources.append("lab_file_read (requires s3_key in parameters)")
        if self.config.enable_web:
            available_sources.append("web_search")
        if self.config.enable_code:
            available_sources.append("code_search")
        if self.config.enable_papers:
            available_sources.append("paper_search")

        prompt = f"""Plan a search strategy for this query:

Query: {query}

Available sources: {", ".join(available_sources)}
Max steps: {self.config.max_steps}
Depth: {self.config.depth.value}

Respond with a JSON array of search steps."""

        try:
            response = self.client.chat(
                message=prompt,
                system=self.PLANNER_SYSTEM_PROMPT,
                temperature=0.3,
                model=self.config.model,
            )

            # Parse JSON response
            steps = self._parse_plan_response(response.content)

            # Validate and filter steps
            return self._validate_plan(steps)

        except Exception as e:
            msg = f"LLM planning failed: {e}, falling back to quick plan"
            logger.warning(msg)
            self._warnings.append(msg)
            return self._quick_plan(query)

    def _parse_plan_response(self, response: str) -> list[SearchStep]:
        """Parse LLM response into SearchSteps."""
        # Extract JSON from response
        import re

        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if not json_match:
            logger.warning("No JSON array found in plan response")
            return []

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse plan JSON: {e}")
            return []

        steps = []
        for i, item in enumerate(data):
            try:
                step_type_str = item.get("step_type", "")
                if step_type_str not in [st.value for st in StepType]:
                    continue

                steps.append(
                    SearchStep(
                        step_type=StepType(step_type_str),
                        query=item.get("query", ""),
                        reasoning=item.get("reasoning", ""),
                        depends_on=item.get("depends_on", []),
                        parameters=item.get("parameters", {}),
                        order=i,
                    )
                )
            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid step in plan: {e}")
                continue

        return steps

    def _validate_plan(self, steps: list[SearchStep]) -> list[SearchStep]:
        """Validate and filter plan steps."""
        valid_steps = []

        for step in steps:
            # Skip if source not enabled
            if step.step_type == StepType.RAG_QUERY and not self.config.enable_rag:
                continue
            if step.step_type == StepType.LAB_CATALOG_SEARCH and not self.config.enable_catalog:
                continue
            if step.step_type == StepType.LAB_FILE_READ and not self.config.enable_raw_read:
                continue
            if step.step_type == StepType.WEB_SEARCH and not self.config.enable_web:
                continue
            if step.step_type == StepType.CODE_SEARCH and not self.config.enable_code:
                continue
            if step.step_type == StepType.PAPER_SEARCH and not self.config.enable_papers:
                continue

            # Skip empty queries
            if not step.query.strip():
                if not (step.step_type == StepType.LAB_FILE_READ and step.parameters.get("auto")):
                    continue

            valid_steps.append(step)

            # Limit to max steps
            if len(valid_steps) >= self.config.max_steps:
                break

        return valid_steps

    def _is_code_query(self, query: str) -> bool:
        """Check if query is likely about code."""
        code_keywords = [
            "code",
            "function",
            "class",
            "method",
            "implement",
            "api",
            "library",
            "bug",
            "error",
            "fix",
            "debug",
        ]
        query_lower = query.lower()
        return any(kw in query_lower for kw in code_keywords)


@dataclass
class QueryDecomposition:
    """
    Query decomposition result (A-902).

    Breaks down complex queries into sub-queries.
    """

    original: str
    sub_queries: list[str]
    intent: str
    entities: list[str] = field(default_factory=list)


class QueryDecomposer:
    """
    Decompose complex queries into simpler sub-queries.
    """

    DECOMPOSE_PROMPT = """Decompose this query into simpler sub-queries.

Query: {query}

Respond with:
1. intent: The main intent/goal of the query
2. entities: Key entities mentioned
3. sub_queries: List of simpler queries that together answer the original

Respond in JSON:
```json
{{
  "intent": "...",
  "entities": ["..."],
  "sub_queries": ["query 1", "query 2", ...]
}}
```
"""

    def __init__(self, client: "Client"):
        """Initialize decomposer."""
        self.client = client

    async def decompose(self, query: str) -> QueryDecomposition:
        """Decompose a query into sub-queries."""
        response = self.client.chat(
            message=self.DECOMPOSE_PROMPT.format(query=query),
            temperature=0.3,
        )

        # Parse response
        try:
            import re

            json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return QueryDecomposition(
                    original=query,
                    sub_queries=data.get("sub_queries", [query]),
                    intent=data.get("intent", ""),
                    entities=data.get("entities", []),
                )
        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback to original query
        return QueryDecomposition(
            original=query,
            sub_queries=[query],
            intent="unknown",
        )
