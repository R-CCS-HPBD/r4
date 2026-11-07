"""
Result Synthesizer (A-904)

Synthesize search results into coherent answers with citations.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from r4.agent.untrusted_scanner import (
        ContentSource,
        ScanResult,
        UntrustedContentScanner,
    )
    from r4.client import Client

    from .core import SearchConfig, Source, SourceType

logger = logging.getLogger(__name__)

# Metadata fields copied into the synthesis prompt — scan alongside body content.
_METADATA_SCAN_KEYS = ("summary", "deterministic_summary", "perception_summary")


@dataclass
class Citation:
    """
    A citation reference (A-905).
    """

    index: int
    source_title: str
    source_url: Optional[str]
    quote: str = ""
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "index": self.index,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "quote": self.quote,
            "context": self.context,
        }

    def format_inline(self) -> str:
        """Format as inline citation."""
        return f"[{self.index}]"

    def format_reference(self) -> str:
        """Format as reference entry."""
        if self.source_url:
            return f"[{self.index}] [{self.source_title}]({self.source_url})"
        return f"[{self.index}] {self.source_title}"


class ResultSynthesizer:
    """
    Result Synthesizer (A-904).

    Synthesizes search results into a coherent answer with citations.
    """

    SYNTHESIZE_SYSTEM_PROMPT = """You are a research assistant that synthesizes information
from multiple sources.

Your task:
1. Read the provided sources carefully
2. Synthesize the information into a clear, accurate answer
3. Use inline citations [1], [2], etc. when referencing sources
4. Be objective and mention any conflicting information
5. If the sources don't fully answer the question, say what's missing

Format:
- Use markdown formatting
- Include inline citations like [1] when using information from sources
- Be concise but thorough

Important:
- Only use information from the provided sources
- Always cite sources for claims
- Don't make up information not in the sources
"""

    def __init__(self, client: "Client", config: "SearchConfig"):
        """
        Initialize synthesizer.

        Args:
            client: R4 client
            config: Search configuration
        """
        self.client = client
        self.config = config

    async def synthesize(
        self,
        query: str,
        sources: list["Source"],
    ) -> tuple[str, list[dict[str, Any]], float, list[str]]:
        """
        Synthesize sources into an answer.

        Args:
            query: Original query
            sources: List of source documents

        Returns:
            Tuple of (answer, citations, confidence, synthesis_warnings)
        """
        if not sources:
            # No external sources — try answering from LLM knowledge
            answer, citations, confidence = await self._llm_knowledge_fallback(query)
            return answer, citations, confidence, []

        safe_sources, synthesis_warnings = self._prepare_sources_for_synthesis(sources)

        # Build source context
        source_context = self._build_source_context(safe_sources)

        # Generate synthesis prompt
        prompt = f"""Question: {query}

Sources:
{source_context}

Synthesize an answer to the question using the sources above. Use [1], [2], etc. to cite sources."""

        try:
            response = self.client.chat(
                message=prompt,
                system=self.SYNTHESIZE_SYSTEM_PROMPT,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                model=self.config.model,
            )

            answer = response.content

            # Extract citations
            citations = self._extract_citations(answer, safe_sources)

            # Calculate confidence based on sources
            confidence = self._calculate_confidence(safe_sources, citations)

            return (answer, [c.to_dict() for c in citations], confidence, synthesis_warnings)

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return (
                f"I found relevant sources but couldn't synthesize an answer: {e}",
                [],
                0.1,
                synthesis_warnings,
            )

    async def _llm_knowledge_fallback(
        self,
        query: str,
    ) -> tuple[str, list[dict[str, Any]], float]:
        """Answer from LLM knowledge when no external sources were found."""
        try:
            response = self.client.chat(
                message=query,
                system=(
                    "You are a helpful research assistant. Answer the user's "
                    "question using your training knowledge. Be concise and "
                    "accurate. Note that no external sources were consulted."
                ),
                temperature=self.config.temperature,
                model=self.config.model,
                max_tokens=self.config.max_tokens,
            )
            disclaimer = (
                "\n\n---\n*Note: This answer is based on model knowledge only "
                "— no external or RAG sources were consulted.*"
            )
            return (response.content + disclaimer, [], 0.3)
        except Exception as e:
            logger.warning(f"LLM knowledge fallback failed: {e}")
            return (
                "I couldn't find relevant information to answer this question.",
                [],
                0.0,
            )

    def _prepare_sources_for_synthesis(
        self,
        sources: list["Source"],
    ) -> tuple[list["Source"], list[str]]:
        """Scan untrusted source text before it reaches the synthesis LLM."""
        from r4.agent.untrusted_scanner import (
            get_default_scanner,
        )

        scanner = get_default_scanner()
        warnings: list[str] = []
        prepared: list[Source] = []

        for source in sources:
            content_source = _source_type_to_content_source(source.type)
            context = {
                "source_title": source.title,
                "s3_key": (source.metadata or {}).get("s3_key"),
                "url": source.url,
            }

            safe_content, content_result = _scan_text_for_synthesis(
                source.content or "",
                content_source=content_source,
                scanner=scanner,
                context=context,
            )
            warnings.extend(_warnings_from_scan_result(source.title, content_result, scanner))

            safe_meta = dict(source.metadata or {})
            for key in _METADATA_SCAN_KEYS:
                raw = safe_meta.get(key)
                if not raw:
                    continue
                processed, meta_result = _scan_text_for_synthesis(
                    str(raw),
                    content_source=content_source,
                    scanner=scanner,
                    context={**context, "metadata_field": key},
                )
                safe_meta[key] = processed
                field_label = f"{source.title} ({key})"
                warnings.extend(_warnings_from_scan_result(field_label, meta_result, scanner))

            prepared.append(
                replace(source, content=safe_content, metadata=safe_meta),
            )

        return prepared, warnings

    def _build_source_context(self, sources: list["Source"]) -> str:
        """Build source context for synthesis prompt."""
        context_parts = []

        for i, source in enumerate(sources, 1):
            # Truncate content to fit context
            content = source.content[:1500]
            if len(source.content) > 1500:
                content += "..."

            metadata_block = self._format_source_metadata(source)
            part = f"""[{i}] {source.title}
Type: {source.type.value}"""
            if metadata_block:
                part += f"\n{metadata_block}"
            part += f"""
---
{content}
---"""
            context_parts.append(part)

        return "\n\n".join(context_parts)

    @staticmethod
    def _format_source_metadata(source: "Source") -> str:
        """Render structured metadata so synthesis can use catalog fields."""
        meta = source.metadata or {}
        if not meta:
            return ""

        lines: list[str] = []
        field_labels = (
            ("s3_key", "s3_key"),
            ("state", "state"),
            ("detected_modality", "modality"),
            ("deterministic_summary", "deterministic_summary"),
            ("perception_summary", "perception_summary"),
            ("perception_model", "perception_model"),
        )
        for key, label in field_labels:
            value = meta.get(key)
            if value:
                lines.append(f"{label}: {value}")

        tags = meta.get("tags")
        if tags:
            tag_text = ", ".join(str(t) for t in tags)
            lines.append(f"tags: {tag_text}")

        filename_signals = meta.get("filename_signals")
        if filename_signals:
            lines.append(f"filename_signals: {', '.join(str(s) for s in filename_signals)}")

        if source.relevance_score > 0:
            lines.append(f"relevance_score: {source.relevance_score:.2f}")

        return "\n".join(lines)

    def _extract_citations(
        self,
        answer: str,
        sources: list["Source"],
    ) -> list[Citation]:
        """Extract citations from the answer."""
        citations = []
        seen_indices = set()

        # Find all citation patterns [1], [2], etc.
        pattern = r"\[(\d+)\]"
        matches = re.finditer(pattern, answer)

        for match in matches:
            index = int(match.group(1))
            if index in seen_indices:
                continue
            if index < 1 or index > len(sources):
                continue

            seen_indices.add(index)
            source = sources[index - 1]

            # Extract surrounding context
            start = max(0, match.start() - 100)
            end = min(len(answer), match.end() + 100)
            context = answer[start:end].strip()

            citations.append(
                Citation(
                    index=index,
                    source_title=source.title,
                    source_url=source.url,
                    context=context,
                )
            )

        return sorted(citations, key=lambda c: c.index)

    def _calculate_confidence(
        self,
        sources: list["Source"],
        citations: list[Citation],
    ) -> float:
        """
        Calculate confidence score.

        Based on:
        - Number and quality of sources
        - Relevance scores
        - Citation coverage
        """
        if not sources:
            return 0.0

        # Average relevance score
        avg_relevance = sum(s.relevance_score for s in sources) / len(sources)

        # Citation coverage (what % of sources were cited)
        cited_count = len(citations)
        citation_coverage = min(1.0, cited_count / max(3, len(sources)))

        # Source count factor (more sources = higher confidence, up to a point)
        source_factor = min(1.0, len(sources) / 5)

        # Combine factors
        confidence = (avg_relevance * 0.5) + (citation_coverage * 0.3) + (source_factor * 0.2)

        return min(1.0, max(0.0, confidence))


def _source_type_to_content_source(source_type: "SourceType") -> "ContentSource":
    """Map search source types to untrusted-content scanner sources."""
    from r4.agent.untrusted_scanner import ContentSource
    from r4.search.core import SourceType

    if source_type == SourceType.RAG_DOCUMENT:
        return ContentSource.RAG_CHUNK
    if source_type in (SourceType.CATALOG_ENTRY, SourceType.RAW_FILE):
        return ContentSource.FILE_FETCH
    return ContentSource.UNKNOWN


def _scan_text_for_synthesis(
    text: str,
    *,
    content_source: "ContentSource",
    scanner: "UntrustedContentScanner",
    context: dict[str, Any],
) -> tuple[str, "ScanResult"]:
    """Apply block / sanitize / wrap policy before synthesis."""
    from r4.agent.untrusted_scanner import RiskLevel, ScanResult, wrap_external_content

    if not text:
        return text, ScanResult(risk_score=0.0, risk_level=RiskLevel.NONE, normalized_content="")

    result = scanner.scan(text, source=content_source, context=context)

    if result.is_blocked:
        return f"[Content blocked: {result.block_reason}]", result

    processed = text
    if result.sanitized_content is not None and result.risk_level in (
        RiskLevel.HIGH,
        RiskLevel.CRITICAL,
    ):
        processed = result.sanitized_content

    if result.risk_level == RiskLevel.HIGH:
        processed = wrap_external_content(processed, content_source, metadata=context)

    return processed, result


def _warnings_from_scan_result(
    label: str,
    result: "ScanResult",
    scanner: "UntrustedContentScanner",
) -> list[str]:
    """Build user-visible warnings for concerning scan results."""
    if result.is_blocked:
        return [f"Source '{label}': content blocked (injection risk {result.risk_score:.2f})"]
    if result.risk_score >= scanner.config.warn_threshold:
        return [f"Source '{label}': elevated injection risk ({result.risk_score:.2f})"]
    return []


class AnswerFormatter:
    """
    Format answers for different output types.
    """

    @staticmethod
    def format_markdown(
        answer: str,
        sources: list["Source"],
        citations: list[Citation],
    ) -> str:
        """Format answer as markdown with references."""
        result = answer

        if sources:
            result += "\n\n---\n\n**References:**\n"
            for i, source in enumerate(sources, 1):
                result += f"\n{Citation(i, source.title, source.url).format_reference()}"

        return result

    @staticmethod
    def format_plain(
        answer: str,
        sources: list["Source"],
        citations: list[Citation],
    ) -> str:
        """Format answer as plain text."""
        # Remove markdown formatting
        result = answer
        result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)
        result = re.sub(r"\*(.+?)\*", r"\1", result)
        result = re.sub(r"```[\s\S]*?```", "[code block]", result)

        if sources:
            result += "\n\nReferences:\n"
            for i, source in enumerate(sources, 1):
                if source.url:
                    result += f"[{i}] {source.title}: {source.url}\n"
                else:
                    result += f"[{i}] {source.title}\n"

        return result

    @staticmethod
    def format_json(
        answer: str,
        sources: list["Source"],
        citations: list[Citation],
    ) -> dict[str, Any]:
        """Format answer as JSON structure."""
        return {
            "answer": answer,
            "sources": [s.to_dict() for s in sources],
            "citations": [c.to_dict() for c in citations],
        }


class IncrementalSynthesizer:
    """
    Incremental answer synthesis for streaming.

    Builds answer progressively as new sources arrive.
    """

    def __init__(self, client: "Client", config: "SearchConfig"):
        """Initialize incremental synthesizer."""
        self.client = client
        self.config = config
        self._sources: list["Source"] = []
        self._partial_answer = ""

    def add_source(self, source: "Source") -> None:
        """Add a new source."""
        self._sources.append(source)

    async def get_partial_answer(self) -> str:
        """Get current partial answer based on sources so far."""
        if not self._sources:
            return "Searching..."

        synthesizer = ResultSynthesizer(self.client, self.config)
        answer, _, _, _ = await synthesizer.synthesize(
            "Provide a brief summary of what we know so far",
            self._sources[-3:],  # Use most recent sources
        )
        self._partial_answer = answer
        return answer

    async def get_final_answer(self, query: str) -> tuple[str, list[Citation], float]:
        """Get final synthesized answer."""
        synthesizer = ResultSynthesizer(self.client, self.config)
        answer, citations, confidence, _ = await synthesizer.synthesize(query, self._sources)
        return (answer, [Citation(**c) for c in citations], confidence)

    def clear(self) -> None:
        """Clear accumulated sources."""
        self._sources.clear()
        self._partial_answer = ""
