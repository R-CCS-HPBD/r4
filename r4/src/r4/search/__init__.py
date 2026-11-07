"""
Agentic Search Module (A-901 to A-912)

Multi-hop agentic search with RAG, web, and code integration.
"""

from .core import AgenticSearch, SearchConfig, SearchResult
from .executor import SearchTool, ToolExecutor
from .iteration import (
    Gap,
    GapDetector,
    IterationConfig,
    IterationController,
    IterationDecision,
    IterationReason,
    IterationState,
)
from .pipeline import AgenticSearchPipeline
from .planner import SearchPlanner, SearchStep
from .synthesizer import Citation, ResultSynthesizer
from .trace import (
    DecisionTrace,
    ReasoningTrace,
    ToolCallTrace,
    TraceCollector,
    TraceEvent,
    TraceEventType,
    TraceFormatter,
    create_tracer,
)

__all__ = [
    # Core
    "AgenticSearch",
    "SearchConfig",
    "SearchResult",
    # Planning
    "SearchPlanner",
    "SearchStep",
    # Execution
    "ToolExecutor",
    "SearchTool",
    # Synthesis
    "ResultSynthesizer",
    "Citation",
    # Pipeline
    "AgenticSearchPipeline",
    # Iteration (A-909)
    "IterationController",
    "IterationConfig",
    "IterationDecision",
    "Gap",
    "GapDetector",
    "IterationReason",
    "IterationState",
    # Trace (A-911)
    "TraceCollector",
    "TraceEvent",
    "TraceEventType",
    "ToolCallTrace",
    "DecisionTrace",
    "ReasoningTrace",
    "TraceFormatter",
    "create_tracer",
]
