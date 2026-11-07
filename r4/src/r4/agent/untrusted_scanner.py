"""Untrusted Content Scanner for detecting prompt injection in tool outputs.

Implements T-209 to T-216 from Security Hardening Phase 1:
- Text normalization (Unicode, confusables)
- Multi-layer detection (keywords, structure, heuristics)
- Risk scoring with configurable thresholds
- Content wrapping with clear boundaries
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Content Boundaries (T-213)
# =============================================================================

EXTERNAL_CONTENT_START = "[EXTERNAL_CONTENT_START]"
EXTERNAL_CONTENT_END = "[EXTERNAL_CONTENT_END]"
TOOL_OUTPUT_START = "[TOOL_OUTPUT_START]"
TOOL_OUTPUT_END = "[TOOL_OUTPUT_END]"
RAG_CHUNK_START = "[RAG_CHUNK_START]"
RAG_CHUNK_END = "[RAG_CHUNK_END]"


class ContentSource(Enum):
    """Source type for untrusted content."""

    TOOL_OUTPUT = auto()
    FILE_FETCH = auto()
    RAG_CHUNK = auto()
    USER_INPUT = auto()
    UNKNOWN = auto()


# =============================================================================
# Unicode Normalization (T-210)
# =============================================================================

# Common homoglyphs (confusables) that could be used to bypass detection
HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic lookalikes
    "\u0430": "a",  # Cyrillic а
    "\u0435": "e",  # Cyrillic е
    "\u043e": "o",  # Cyrillic о
    "\u0440": "p",  # Cyrillic р
    "\u0441": "c",  # Cyrillic с
    "\u0443": "y",  # Cyrillic у
    "\u0445": "x",  # Cyrillic х
    "\u0456": "i",  # Ukrainian і
    "\u0458": "j",  # Cyrillic ј
    # Greek lookalikes
    "\u03b1": "a",  # Greek α
    "\u03b5": "e",  # Greek ε
    "\u03bf": "o",  # Greek ο
    "\u03c1": "p",  # Greek ρ
    # Fullwidth characters
    "\uff41": "a",
    "\uff42": "b",
    "\uff43": "c",
    "\uff44": "d",
    "\uff45": "e",
    "\uff46": "f",
    "\uff47": "g",
    "\uff48": "h",
    "\uff49": "i",
    "\uff4a": "j",
    "\uff4b": "k",
    "\uff4c": "l",
    "\uff4d": "m",
    "\uff4e": "n",
    "\uff4f": "o",
    "\uff50": "p",
    "\uff51": "q",
    "\uff52": "r",
    "\uff53": "s",
    "\uff54": "t",
    "\uff55": "u",
    "\uff56": "v",
    "\uff57": "w",
    "\uff58": "x",
    "\uff59": "y",
    "\uff5a": "z",
    # Mathematical variants
    "\U0001d41a": "a",  # Mathematical sans-serif
    "\U0001d5ba": "a",  # Mathematical bold
    # Other confusables
    "\u0131": "i",  # Turkish dotless i
    "\u0237": "j",  # Dotless j
    "\u1d00": "a",  # Small capital A
    "\u1d04": "c",  # Small capital C
}

# Zero-width characters to strip
ZERO_WIDTH_CHARS = [
    "\u200b",  # Zero-width space
    "\u200c",  # Zero-width non-joiner
    "\u200d",  # Zero-width joiner
    "\u2060",  # Word joiner
    "\ufeff",  # Zero-width no-break space (BOM)
    "\u00ad",  # Soft hyphen
    "\u034f",  # Combining grapheme joiner
    "\u2063",  # Invisible separator
    "\u180e",  # Mongolian vowel separator
]


def normalize_text(text: str) -> str:
    """
    Normalize text for injection detection.

    Applies:
    - Unicode NFKC normalization
    - Homoglyph replacement
    - Zero-width character removal
    - Case normalization

    Args:
        text: Input text to normalize

    Returns:
        Normalized text for pattern matching
    """
    if not text:
        return ""

    # Unicode NFKC normalization (compatibility decomposition + canonical composition)
    normalized = unicodedata.normalize("NFKC", text)

    # Remove zero-width characters
    for char in ZERO_WIDTH_CHARS:
        normalized = normalized.replace(char, "")

    # Replace homoglyphs
    result = []
    for char in normalized:
        result.append(HOMOGLYPH_MAP.get(char, char))

    # Case normalization for detection (preserve original for display)
    return "".join(result).lower()


# =============================================================================
# Detection Patterns (T-211)
# =============================================================================

# Keywords that indicate prompt injection attempts
INJECTION_KEYWORDS: list[tuple[str, float]] = [
    # Direct instruction override
    (r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+instructions?\b", 0.9),
    (
        r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)\b",
        0.9,
    ),
    (r"\bforget\s+(?:everything|all)\s+(?:you\s+)?(?:know|learned|were\s+told)\b", 0.85),
    (r"\boverride\s+(?:your|the|all)\s+(?:instructions?|rules?|guidelines?)\b", 0.9),
    (r"\bact\s+as\s+(?:if|though)\s+you\s+(?:have|had)\s+no\s+(?:restrictions?|rules?)\b", 0.85),
    (r"\bpretend\s+you\s+have\s+no\s+(?:restrictions?|rules?|limits?)\b", 0.85),
    # Role manipulation
    (r"\byou\s+are\s+now\s+(?:a|an)\s+\w+\b", 0.7),
    (r"\bpretend\s+(?:to\s+be|you\s+are)\b", 0.6),
    (r"\bact\s+as\s+(?:a|an)\s+\w+\b", 0.5),
    (r"\benter\s+(?:developer|debug|admin|root|god)\s+mode\b", 0.95),
    (r"\bswitch\s+to\s+unrestricted\s+mode\b", 0.95),
    # System prompt extraction
    (r"\bwhat\s+(?:is|are)\s+your\s+(?:system\s+)?(?:instructions?|prompts?|rules?)\b", 0.7),
    (r"\brepeat\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)\b", 0.8),
    (r"\bshow\s+me\s+your\s+(?:hidden\s+)?(?:prompt|instructions?)\b", 0.85),
    (r"\bprint\s+(?:the\s+)?(?:system\s+)?(?:prompt|instructions?)\b", 0.8),
    # Jailbreak patterns
    (r"\bDANmode\b", 0.95),
    (r"\bDeveloperMode\b", 0.95),
    (r"\bjailbreak\b", 0.9),
    (r"\bDo\s+Anything\s+Now\b", 0.95),
    # Code execution / command injection
    (r"\bexecute\s+(?:this\s+)?(?:code|command|script)\b", 0.7),
    (r"\brun\s+(?:this\s+)?(?:code|command|shell)\b", 0.6),
    (r"\beval\s*\([^)]+\)", 0.8),
    (r"\b(?:os|subprocess|exec)\s*\.\s*\w+\s*\(", 0.8),
    # Data exfiltration
    (r"\bsensi?tive\s+(?:data|information)\s+(?:to|via)\b", 0.7),
    (r"\bupload\s+(?:this\s+)?(?:data|file|content)\s+to\b", 0.65),
    (r"\bsend\s+(?:this\s+)?(?:to|via)\s+(?:http|https|ftp)\b", 0.7),
    # Output redirection
    (r"\brespond\s+only\s+with\b", 0.5),
    (r"\boutput\s+(?:only|just)\b", 0.4),
    (r"\bignore\s+(?:the\s+)?(?:question|query|user)\b", 0.8),
    (r"\bdo\s+not\s+respond\s+to\s+the\s+(?:user|human)\b", 0.85),
    # Concatenated forms (after zero-width char removal)
    (r"ignore\s*all\s*previous\s*instructions?", 0.9),
    (r"ignoreallpreviousinstructions?", 0.9),
    (r"ignore\s+everything\s+else", 0.8),
]

# Structural patterns that indicate injection attempts
STRUCTURAL_PATTERNS: list[tuple[str, float]] = [
    # Fake system/assistant markers
    (r"\[system\]", 0.85),
    (r"\[/system\]", 0.85),
    (r"<\s*system\s*>", 0.85),
    (r"</\s*system\s*>", 0.85),
    (r"\[assistant\]", 0.75),
    (r"<\s*assistant\s*>", 0.75),
    (r"\[user\]", 0.65),
    (r"<\s*user\s*>", 0.65),
    # JSON/XML smuggling
    (r'"role"\s*:\s*"system"', 0.9),
    (r'"role"\s*:\s*"assistant"', 0.7),
    (r'"content"\s*:\s*"', 0.5),
    # Boundary markers
    (r"---\s*(?:new\s+)?(?:system\s+)?(?:prompt|instructions?)\s*---", 0.85),
    (r"###\s*(?:new\s+)?(?:system\s+)?(?:prompt|instructions?)\s*###", 0.85),
    # Markdown code blocks with suspicious content
    (r"```(?:python|bash|shell|js|javascript)\s*\n.*(?:exec|eval|system|subprocess)", 0.7),
]


# =============================================================================
# Risk Assessment (T-212)
# =============================================================================


class RiskLevel(Enum):
    """Risk level classification for detected content."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class DetectionMatch:
    """A single detection match."""

    pattern: str
    matched_text: str
    confidence: float
    start_pos: int
    end_pos: int
    category: str = "keyword"  # keyword, structural, heuristic


@dataclass
class ScanResult:
    """Result of untrusted content scanning."""

    risk_score: float  # 0.0 to 1.0
    risk_level: RiskLevel
    matches: list[DetectionMatch] = field(default_factory=list)
    normalized_content: str = ""
    sanitized_content: Optional[str] = None
    is_blocked: bool = False
    block_reason: Optional[str] = None

    def summary(self) -> str:
        """Human-readable summary."""
        if not self.matches:
            return f"No injection detected (risk: {self.risk_score:.2f})"

        categories = {}
        for m in self.matches:
            categories[m.category] = categories.get(m.category, 0) + 1

        parts = [f"{k}: {v}" for k, v in categories.items()]
        return f"Risk {self.risk_level.value} ({self.risk_score:.2f}): {', '.join(parts)}"


@dataclass
class ScannerConfig:
    """Configuration for UntrustedContentScanner."""

    # Risk thresholds
    threshold_low: float = 0.3
    threshold_medium: float = 0.5
    threshold_high: float = 0.7
    threshold_critical: float = 0.9

    # Action thresholds
    block_threshold: float = 0.8  # Auto-block above this score
    warn_threshold: float = 0.5  # Log warning above this score

    # Detection settings
    enable_keyword_detection: bool = True
    enable_structural_detection: bool = True
    enable_heuristic_detection: bool = True

    # Content handling
    wrap_content: bool = True
    sanitize_on_high_risk: bool = True

    # Custom patterns
    custom_patterns: list[tuple[str, float]] = field(default_factory=list)

    # Project-specific overrides
    project_id: Optional[str] = None


# =============================================================================
# Heuristic Detection (T-211)
# =============================================================================


def detect_instruction_density(text: str) -> float:
    """
    Detect high density of instruction-like language.

    Returns a score based on imperative verb density.
    """
    normalized = normalize_text(text)
    words = normalized.split()
    if len(words) < 10:
        return 0.0

    # Imperative verbs commonly used in instructions
    imperatives = {
        "do",
        "don't",
        "always",
        "never",
        "must",
        "should",
        "ensure",
        "make",
        "let",
        "allow",
        "prevent",
        "stop",
        "continue",
        "proceed",
        "output",
        "respond",
        "reply",
        "say",
        "tell",
        "answer",
        "print",
        "ignore",
        "disregard",
        "forget",
        "override",
        "bypass",
        "skip",
    }

    imperative_count = sum(1 for word in words if word in imperatives)
    density = imperative_count / len(words)

    # High instruction density suggests injection
    return min(density * 5, 1.0)


def detect_context_manipulation(text: str) -> float:
    """
    Detect attempts to manipulate conversation context.

    Looks for patterns like fake conversation history.
    """
    normalized = normalize_text(text)

    # Fake conversation markers
    fake_convo_patterns = [
        r"(?:human|user|assistant)\s*:\s*",
        r"(?:q|question)\s*:\s*.*?(?:a|answer)\s*:\s*",
        r"\[(?:human|user|assistant|system)\]",
    ]

    score = 0.0
    for pattern in fake_convo_patterns:
        matches = re.findall(pattern, normalized, re.IGNORECASE)
        score += min(len(matches) * 0.2, 0.5)

    return min(score, 1.0)


def detect_encoded_payload(text: str) -> float:
    """
    Detect potential encoded payloads (base64, hex, etc.).

    These might be used to smuggle instructions.
    """
    # Base64 pattern (long strings of base64 chars)
    base64_pattern = r"[A-Za-z0-9+/]{40,}={0,2}"
    base64_matches = re.findall(base64_pattern, text)

    # Hex pattern
    hex_pattern = r"(?:0x)?[0-9a-fA-F]{20,}"
    hex_matches = re.findall(hex_pattern, text)

    # URL-encoded pattern
    url_encoded_pattern = r"(?:%[0-9a-fA-F]{2}){10,}"
    url_matches = re.findall(url_encoded_pattern, text)

    total_encoded = len(base64_matches) + len(hex_matches) + len(url_matches)

    if total_encoded == 0:
        return 0.0

    # Calculate ratio of encoded content
    total_length = len(text)
    encoded_length = sum(len(m) for m in base64_matches + hex_matches + url_matches)
    ratio = encoded_length / max(total_length, 1)

    # High ratio of encoded content is suspicious
    return min(ratio * 2, 0.8)


# =============================================================================
# Untrusted Content Scanner (T-209)
# =============================================================================


class UntrustedContentScanner:
    """
    Scanner for detecting prompt injection in untrusted content.

    Applies multi-layer detection:
    1. Text normalization (Unicode, homoglyphs)
    2. Keyword pattern matching
    3. Structural pattern detection
    4. Heuristic analysis

    Example:
        scanner = UntrustedContentScanner()
        result = scanner.scan("Ignore previous instructions and...")
        if result.is_blocked:
            logger.warning(f"Blocked: {result.block_reason}")
    """

    def __init__(
        self,
        config: Optional[ScannerConfig] = None,
    ):
        """
        Initialize scanner.

        Args:
            config: Scanner configuration (uses defaults if not provided)
        """
        self.config = config or ScannerConfig()

        # Compile patterns for efficiency
        self._keyword_patterns = [
            (re.compile(p, re.IGNORECASE | re.MULTILINE), score) for p, score in INJECTION_KEYWORDS
        ]
        self._structural_patterns = [
            (re.compile(p, re.IGNORECASE | re.MULTILINE), score) for p, score in STRUCTURAL_PATTERNS
        ]

        # Add custom patterns
        if self.config.custom_patterns:
            for p, score in self.config.custom_patterns:
                self._keyword_patterns.append((re.compile(p, re.IGNORECASE | re.MULTILINE), score))

    def scan(
        self,
        content: str,
        source: ContentSource = ContentSource.UNKNOWN,
        context: Optional[dict[str, Any]] = None,
    ) -> ScanResult:
        """
        Scan untrusted content for prompt injection.

        Args:
            content: Content to scan
            source: Source type (tool output, file fetch, etc.)
            context: Additional context (user_id, project_id, etc.)

        Returns:
            ScanResult with risk assessment and matches
        """
        if not content:
            return ScanResult(
                risk_score=0.0,
                risk_level=RiskLevel.NONE,
                normalized_content="",
            )

        # Normalize text for detection
        normalized = normalize_text(content)

        matches: list[DetectionMatch] = []
        max_score = 0.0

        # Keyword detection
        if self.config.enable_keyword_detection:
            keyword_matches, keyword_score = self._detect_keywords(normalized, content)
            matches.extend(keyword_matches)
            max_score = max(max_score, keyword_score)

        # Structural detection
        if self.config.enable_structural_detection:
            structural_matches, structural_score = self._detect_structural(normalized, content)
            matches.extend(structural_matches)
            max_score = max(max_score, structural_score)

        # Heuristic detection
        if self.config.enable_heuristic_detection:
            heuristic_score = self._detect_heuristics(normalized, content)
            if heuristic_score > 0.3:
                matches.append(
                    DetectionMatch(
                        pattern="heuristic_combined",
                        matched_text="",
                        confidence=heuristic_score,
                        start_pos=0,
                        end_pos=len(content),
                        category="heuristic",
                    )
                )
            max_score = max(max_score, heuristic_score)

        # Calculate combined risk score
        risk_score = self._calculate_risk_score(matches, max_score)
        risk_level = self._get_risk_level(risk_score)

        # Determine if content should be blocked
        is_blocked = risk_score >= self.config.block_threshold
        block_reason = None
        if is_blocked:
            top_matches = sorted(matches, key=lambda m: m.confidence, reverse=True)[:3]
            block_reason = (
                f"High injection risk ({risk_score:.2f}): "
                f"{', '.join(m.pattern for m in top_matches)}"
            )

        # Generate sanitized content if needed
        sanitized_content = None
        if self.config.sanitize_on_high_risk and risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            sanitized_content = self._sanitize_content(content, matches)

        result = ScanResult(
            risk_score=risk_score,
            risk_level=risk_level,
            matches=matches,
            normalized_content=normalized,
            sanitized_content=sanitized_content,
            is_blocked=is_blocked,
            block_reason=block_reason,
        )

        # Log warnings for concerning content
        if risk_score >= self.config.warn_threshold:
            logger.warning(
                "Potential injection detected",
                extra={
                    "risk_score": risk_score,
                    "risk_level": risk_level.value,
                    "source": source.name,
                    "match_count": len(matches),
                    "project_id": self.config.project_id,
                    "context": context,
                },
            )

        return result

    def _detect_keywords(
        self,
        normalized: str,
        original: str,
    ) -> tuple[list[DetectionMatch], float]:
        """Detect injection keywords."""
        matches = []
        max_score = 0.0

        for pattern, base_score in self._keyword_patterns:
            for match in pattern.finditer(normalized):
                # Map position back to original text (approximate)
                start = match.start()
                end = match.end()

                matches.append(
                    DetectionMatch(
                        pattern=pattern.pattern[:50],
                        matched_text=match.group()[:100],
                        confidence=base_score,
                        start_pos=start,
                        end_pos=end,
                        category="keyword",
                    )
                )
                max_score = max(max_score, base_score)

        return matches, max_score

    def _detect_structural(
        self,
        normalized: str,
        original: str,
    ) -> tuple[list[DetectionMatch], float]:
        """Detect structural injection patterns."""
        matches = []
        max_score = 0.0

        for pattern, base_score in self._structural_patterns:
            for match in pattern.finditer(original):  # Use original for structural
                matches.append(
                    DetectionMatch(
                        pattern=pattern.pattern[:50],
                        matched_text=match.group()[:100],
                        confidence=base_score,
                        start_pos=match.start(),
                        end_pos=match.end(),
                        category="structural",
                    )
                )
                max_score = max(max_score, base_score)

        return matches, max_score

    def _detect_heuristics(
        self,
        normalized: str,
        original: str,
    ) -> float:
        """Apply heuristic detection rules."""
        scores = [
            detect_instruction_density(original),
            detect_context_manipulation(original),
            detect_encoded_payload(original),
        ]

        # Weighted average of heuristic scores
        weights = [0.4, 0.3, 0.3]
        return sum(s * w for s, w in zip(scores, weights))

    def _calculate_risk_score(
        self,
        matches: list[DetectionMatch],
        max_score: float,
    ) -> float:
        """Calculate combined risk score from all detections."""
        if not matches:
            return 0.0

        # Use max score as base, boost by number of matches
        match_boost = min(len(matches) * 0.05, 0.2)

        # Category diversity boost
        categories = set(m.category for m in matches)
        diversity_boost = (len(categories) - 1) * 0.1 if len(categories) > 1 else 0

        return min(max_score + match_boost + diversity_boost, 1.0)

    def _get_risk_level(self, score: float) -> RiskLevel:
        """Map risk score to risk level."""
        if score >= self.config.threshold_critical:
            return RiskLevel.CRITICAL
        elif score >= self.config.threshold_high:
            return RiskLevel.HIGH
        elif score >= self.config.threshold_medium:
            return RiskLevel.MEDIUM
        elif score >= self.config.threshold_low:
            return RiskLevel.LOW
        return RiskLevel.NONE

    def _sanitize_content(
        self,
        content: str,
        matches: list[DetectionMatch],
    ) -> str:
        """
        Sanitize content by redacting detected injection patterns.

        Args:
            content: Original content
            matches: Detected matches

        Returns:
            Sanitized content with injections redacted
        """
        if not matches:
            return content

        # Sort matches by position (descending) to replace from end
        sorted_matches = sorted(matches, key=lambda m: m.start_pos, reverse=True)

        result = content
        for match in sorted_matches:
            if match.confidence >= 0.7:  # Only redact high-confidence matches
                redaction = f"[REDACTED: {match.category}]"
                result = result[: match.start_pos] + redaction + result[match.end_pos :]

        return result


# =============================================================================
# Content Wrapping (T-213)
# =============================================================================


def wrap_external_content(
    content: str,
    source: ContentSource,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """
    Wrap external content with clear boundaries.

    This helps the LLM distinguish untrusted content from system instructions.

    Args:
        content: Content to wrap
        source: Source type
        metadata: Optional metadata to include

    Returns:
        Wrapped content string
    """
    if source == ContentSource.TOOL_OUTPUT:
        start_marker = TOOL_OUTPUT_START
        end_marker = TOOL_OUTPUT_END
    elif source == ContentSource.RAG_CHUNK:
        start_marker = RAG_CHUNK_START
        end_marker = RAG_CHUNK_END
    else:
        start_marker = EXTERNAL_CONTENT_START
        end_marker = EXTERNAL_CONTENT_END

    # Include metadata if provided
    meta_str = ""
    if metadata:
        safe_meta = {k: str(v)[:100] for k, v in metadata.items()}
        meta_str = f" source={safe_meta.get('source', 'unknown')}"

    return f"{start_marker}{meta_str}\n{content}\n{end_marker}"


def unwrap_content(content: str) -> tuple[str, Optional[ContentSource]]:
    """
    Unwrap content and detect its source type.

    Args:
        content: Potentially wrapped content

    Returns:
        Tuple of (unwrapped content, detected source or None)
    """
    for marker_start, marker_end, source in [
        (TOOL_OUTPUT_START, TOOL_OUTPUT_END, ContentSource.TOOL_OUTPUT),
        (RAG_CHUNK_START, RAG_CHUNK_END, ContentSource.RAG_CHUNK),
        (EXTERNAL_CONTENT_START, EXTERNAL_CONTENT_END, ContentSource.FILE_FETCH),
    ]:
        if marker_start in content and marker_end in content:
            # Extract content between markers
            start_idx = content.find(marker_start) + len(marker_start)
            # Skip metadata line if present
            if content[start_idx:].startswith(" "):
                start_idx = content.find("\n", start_idx) + 1
            elif content[start_idx:].startswith("\n"):
                start_idx += 1

            end_idx = content.find(marker_end)
            if end_idx > start_idx:
                return content[start_idx:end_idx].strip(), source

    return content, None


# =============================================================================
# Integration Helpers
# =============================================================================


def scan_tool_output(
    tool_name: str,
    output: Any,
    scanner: Optional[UntrustedContentScanner] = None,
    context: Optional[dict[str, Any]] = None,
) -> tuple[Any, ScanResult]:
    """
    Scan tool output for injection and optionally wrap it.

    Args:
        tool_name: Name of the tool that produced the output
        output: Tool output (string or JSON-serializable)
        scanner: Scanner instance (creates default if not provided)
        context: Additional context

    Returns:
        Tuple of (processed output, scan result)
    """
    if scanner is None:
        scanner = UntrustedContentScanner()

    # Convert output to string for scanning
    if isinstance(output, str):
        content = output
    elif isinstance(output, dict | list):
        import json

        content = json.dumps(output)
    else:
        content = str(output)

    # Scan content
    ctx = {"tool_name": tool_name}
    if context:
        ctx.update(context)

    result = scanner.scan(content, source=ContentSource.TOOL_OUTPUT, context=ctx)

    # Process output based on scan result
    if result.is_blocked:
        # Return error instead of potentially malicious content
        processed = f"[Content blocked: {result.block_reason}]"
    elif result.sanitized_content is not None:
        processed = result.sanitized_content
    else:
        processed = output

    return processed, result


def scan_file_content(
    url: str,
    content: str,
    scanner: Optional[UntrustedContentScanner] = None,
    context: Optional[dict[str, Any]] = None,
) -> tuple[str, ScanResult]:
    """
    Scan fetched file content for injection.

    Args:
        url: Source URL of the file
        content: File content
        scanner: Scanner instance (creates default if not provided)
        context: Additional context

    Returns:
        Tuple of (processed content, scan result)
    """
    if scanner is None:
        scanner = UntrustedContentScanner()

    # Scan content
    ctx = {"url": url}
    if context:
        ctx.update(context)

    result = scanner.scan(content, source=ContentSource.FILE_FETCH, context=ctx)

    # Process content based on scan result
    if result.is_blocked:
        processed = f"[File content blocked: {result.block_reason}]"
    elif result.sanitized_content is not None:
        processed = result.sanitized_content
    else:
        processed = content

    return processed, result


def scan_rag_chunk(
    document_id: str,
    chunk_content: str,
    scanner: Optional[UntrustedContentScanner] = None,
    context: Optional[dict[str, Any]] = None,
) -> tuple[str, ScanResult]:
    """
    Scan RAG chunk for injection.

    Args:
        document_id: Source document ID
        chunk_content: Chunk content
        scanner: Scanner instance (creates default if not provided)
        context: Additional context

    Returns:
        Tuple of (processed content, scan result)
    """
    if scanner is None:
        scanner = UntrustedContentScanner()

    # Scan content
    ctx = {"document_id": document_id}
    if context:
        ctx.update(context)

    result = scanner.scan(chunk_content, source=ContentSource.RAG_CHUNK, context=ctx)

    # Process content based on scan result
    if result.is_blocked:
        processed = f"[RAG chunk blocked: {result.block_reason}]"
    elif result.sanitized_content is not None:
        processed = result.sanitized_content
    else:
        processed = chunk_content

    return processed, result


# =============================================================================
# Default Scanner Instance
# =============================================================================

_default_scanner: Optional[UntrustedContentScanner] = None


def get_default_scanner() -> UntrustedContentScanner:
    """Get or create the default scanner instance."""
    global _default_scanner
    if _default_scanner is None:
        _default_scanner = UntrustedContentScanner()
    return _default_scanner


def configure_default_scanner(config: ScannerConfig) -> None:
    """Configure the default scanner."""
    global _default_scanner
    _default_scanner = UntrustedContentScanner(config)
