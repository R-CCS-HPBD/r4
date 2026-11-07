"""MCP Tool Chain Security Scanner (T-280 to T-285).

Scans tool outputs before they flow to the next tool in a chain.

Features:
- T-280: Inter-tool output scanning
- T-281: Injection pattern detection in tool outputs
- T-282: Sensitive data detection (credentials, PII)
- T-283: Command injection prevention
- T-284: Chain depth limiting
- T-285: Audit logging for all tool chains

Security Rationale:
Tool chains can be exploited when:
1. Tool A's output contains injection payloads
2. Tool B trusts Tool A's output and executes malicious content
3. Attacker uses tool chains to bypass single-tool restrictions
4. Sensitive data from one tool leaks to another with different ACL

This module scans all tool outputs before they can be passed to another tool.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


class ChainRiskLevel(str, Enum):
    """Risk level for tool chain content."""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass
class ChainScannerConfig:
    """Configuration for tool chain scanner."""

    # Risk thresholds
    warn_threshold: float = 0.5
    block_threshold: float = 0.8

    # Chain depth limits
    max_chain_depth: int = 10

    # Content limits
    max_output_length: int = 1_000_000  # 1MB

    # Detection toggles
    scan_for_injection: bool = True
    scan_for_credentials: bool = True
    scan_for_commands: bool = True
    scan_for_pii: bool = True

    # Allow/block specific patterns
    blocked_output_patterns: List[str] = field(default_factory=list)

    # Audit settings
    audit_all_chains: bool = True
    audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None


# =============================================================================
# Detection Patterns
# =============================================================================

# Injection patterns (similar to content_scanner but focused on tool output)
TOOL_INJECTION_PATTERNS: List[tuple] = [
    # System prompt injection
    (
        re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?\b", re.IGNORECASE),
        0.9,
        "injection",
    ),
    (
        re.compile(r"\boverride\s+(?:your|the|all)\s+(?:instructions?|rules?)\b", re.IGNORECASE),
        0.9,
        "injection",
    ),
    (re.compile(r"\[system\]", re.IGNORECASE), 0.8, "structural_injection"),
    (re.compile(r'"role"\s*:\s*"system"', re.IGNORECASE), 0.85, "json_injection"),
    # Tool manipulation
    (re.compile(r"\bcall\s+(?:tool|function)\s+", re.IGNORECASE), 0.7, "tool_manipulation"),
    (
        re.compile(r"\bexecute\s+(?:next|following)\s+(?:tool|command)\b", re.IGNORECASE),
        0.75,
        "tool_manipulation",
    ),
    (
        re.compile(r"\binvoke\s+(?:the\s+)?(?:shell|bash|cmd)\b", re.IGNORECASE),
        0.85,
        "shell_injection",
    ),
]

# Shell/command injection patterns
SHELL_INJECTION_PATTERNS: List[tuple] = [
    (
        re.compile(r";\s*(?:rm|wget|curl|nc|bash|sh|python|perl)\s", re.IGNORECASE),
        0.95,
        "command_injection",
    ),
    (re.compile(r"\|\s*(?:sh|bash|python|perl|ruby)\b", re.IGNORECASE), 0.9, "pipe_injection"),
    (re.compile(r"`[^`]+`"), 0.7, "backtick_execution"),
    (re.compile(r"\$\([^)]+\)"), 0.7, "subshell_execution"),
    (re.compile(r"&&\s*(?:rm|wget|curl|chmod)\s", re.IGNORECASE), 0.85, "chained_command"),
    (re.compile(r">\s*/(?:tmp|dev|etc)/", re.IGNORECASE), 0.8, "system_file_redirect"),
]

# Credential patterns
CREDENTIAL_PATTERNS: List[tuple] = [
    (
        re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?", re.IGNORECASE),
        0.8,
        "password",
    ),
    (
        re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[^\s'\"]{10,}['\"]?", re.IGNORECASE),
        0.85,
        "api_key",
    ),
    (
        re.compile(r"(?:secret|token)\s*[:=]\s*['\"]?[^\s'\"]{10,}['\"]?", re.IGNORECASE),
        0.8,
        "secret",
    ),
    (re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), 0.95, "aws_access_key"),
    (re.compile(r"sk-[a-zA-Z0-9]{32,}", re.IGNORECASE), 0.95, "openai_key"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}", re.IGNORECASE), 0.95, "github_token"),
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", re.IGNORECASE), 0.95, "private_key"),
]

# PII patterns
PII_PATTERNS: List[tuple] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), 0.5, "email"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), 0.4, "phone"),
    (re.compile(r"\b\d{3}[-]?\d{2}[-]?\d{4}\b"), 0.6, "ssn"),
    (re.compile(r"\b(?:\d{4}[-\s]?){4}\b"), 0.5, "credit_card"),
]


# =============================================================================
# Scan Results
# =============================================================================


@dataclass
class ChainMatch:
    """A detected pattern match in tool output."""

    pattern_name: str
    matched_text: str
    confidence: float
    category: str
    start_pos: int
    end_pos: int


@dataclass
class ChainScanResult:
    """Result of scanning tool output."""

    risk_score: float
    risk_level: ChainRiskLevel
    matches: List[ChainMatch] = field(default_factory=list)
    is_blocked: bool = False
    block_reasons: List[str] = field(default_factory=list)
    sanitized_output: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def passed(self) -> bool:
        """Check if output passed security scan."""
        return not self.is_blocked


# =============================================================================
# Chain Context
# =============================================================================


@dataclass
class ToolChainContext:
    """Tracks context through a tool chain."""

    chain_id: str
    depth: int = 0
    tool_history: List[str] = field(default_factory=list)
    cumulative_risk: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: Optional[str] = None
    project_id: Optional[str] = None

    def add_tool(self, tool_name: str, risk: float) -> None:
        """Record a tool invocation."""
        self.tool_history.append(tool_name)
        self.depth += 1
        self.cumulative_risk = max(self.cumulative_risk, risk)


# =============================================================================
# Main Scanner
# =============================================================================


class ToolChainScanner:
    """
    Scans tool outputs for security risks before passing to next tool.

    Usage:
        scanner = ToolChainScanner()
        result = scanner.scan_output(
            tool_output="Some output text",
            source_tool="filesystem",
            target_tool="code",
            context=chain_context
        )
        if not result.passed():
            raise SecurityError(f"Tool output blocked: {result.block_reasons}")
    """

    def __init__(self, config: Optional[ChainScannerConfig] = None):
        """Initialize scanner."""
        self.config = config or ChainScannerConfig()

    def _limit_violation_result(
        self,
        tool_output: str,
        source_tool: str,
        context: Optional[ToolChainContext],
    ) -> Optional[ChainScanResult]:
        if len(tool_output) > self.config.max_output_length:
            return ChainScanResult(
                risk_score=1.0,
                risk_level=ChainRiskLevel.BLOCKED,
                is_blocked=True,
                block_reasons=[
                    f"Output exceeds max length ({len(tool_output)} > "
                    f"{self.config.max_output_length})"
                ],
                metadata={"source_tool": source_tool, "length": len(tool_output)},
            )

        if context and context.depth >= self.config.max_chain_depth:
            return ChainScanResult(
                risk_score=1.0,
                risk_level=ChainRiskLevel.BLOCKED,
                is_blocked=True,
                block_reasons=[
                    f"Chain depth limit exceeded ({context.depth} >= {self.config.max_chain_depth})"
                ],
                metadata={"source_tool": source_tool, "depth": context.depth},
            )

        return None

    def _scan_patterns(
        self,
        tool_output: str,
        patterns: List[tuple],
        match_category: str,
        matches: List[ChainMatch],
        *,
        mask_visible_chars: Optional[int] = None,
        risk_weight: float = 1.0,
    ) -> float:
        max_score = 0.0
        for pattern, confidence, pattern_name in patterns:
            for match in pattern.finditer(tool_output):
                matched = match.group()
                matched_text = (
                    self._masked_match(matched, mask_visible_chars)
                    if mask_visible_chars is not None
                    else matched[:100]
                )
                matches.append(
                    ChainMatch(
                        pattern_name=pattern_name,
                        matched_text=matched_text,
                        confidence=confidence,
                        category=match_category,
                        start_pos=match.start(),
                        end_pos=match.end(),
                    )
                )
                max_score = max(max_score, confidence * risk_weight)
        return max_score

    @staticmethod
    def _masked_match(matched: str, visible_chars: int) -> str:
        return matched[:visible_chars] + "***" if len(matched) > visible_chars else "***"

    def _scan_enabled_patterns(self, tool_output: str, matches: List[ChainMatch]) -> float:
        max_score = 0.0
        if self.config.scan_for_injection:
            max_score = max(
                max_score,
                self._scan_patterns(
                    tool_output,
                    TOOL_INJECTION_PATTERNS,
                    "injection",
                    matches,
                ),
            )
        if self.config.scan_for_commands:
            max_score = max(
                max_score,
                self._scan_patterns(
                    tool_output,
                    SHELL_INJECTION_PATTERNS,
                    "command_injection",
                    matches,
                ),
            )
        if self.config.scan_for_credentials:
            max_score = max(
                max_score,
                self._scan_patterns(
                    tool_output,
                    CREDENTIAL_PATTERNS,
                    "credential",
                    matches,
                    mask_visible_chars=10,
                ),
            )
        if self.config.scan_for_pii:
            max_score = max(
                max_score,
                self._scan_patterns(
                    tool_output,
                    PII_PATTERNS,
                    "pii",
                    matches,
                    mask_visible_chars=4,
                    risk_weight=0.7,
                ),
            )
        return max_score

    def _blocked_pattern_reasons(self, tool_output: str) -> List[str]:
        reasons: List[str] = []
        for blocked_pattern in self.config.blocked_output_patterns:
            if re.search(blocked_pattern, tool_output, re.IGNORECASE):
                reasons.append(f"Blocked pattern: {blocked_pattern[:50]}")
        return reasons

    def _assess_risk(
        self,
        max_score: float,
        matches: List[ChainMatch],
        block_reasons: List[str],
    ) -> tuple[ChainRiskLevel, bool, List[str]]:
        if max_score >= self.config.block_threshold or block_reasons:
            if not block_reasons:
                block_reasons = self._generated_block_reasons(matches)
            return ChainRiskLevel.BLOCKED, True, block_reasons
        return self._risk_level_for_score(max_score), False, block_reasons

    def _generated_block_reasons(self, matches: List[ChainMatch]) -> List[str]:
        high_risk_matches = [m for m in matches if m.confidence >= self.config.block_threshold]
        return [f"{m.category}: {m.pattern_name}" for m in high_risk_matches[:3]]

    def _risk_level_for_score(self, max_score: float) -> ChainRiskLevel:
        if max_score >= self.config.warn_threshold:
            return ChainRiskLevel.HIGH
        if max_score >= 0.3:
            return ChainRiskLevel.MEDIUM
        if max_score > 0:
            return ChainRiskLevel.LOW
        return ChainRiskLevel.SAFE

    def scan_output(
        self,
        tool_output: str,
        source_tool: str,
        target_tool: Optional[str] = None,
        context: Optional[ToolChainContext] = None,
    ) -> ChainScanResult:
        """Scan tool output for security risks."""
        matches: List[ChainMatch] = []
        limit_result = self._limit_violation_result(tool_output, source_tool, context)
        if limit_result:
            return limit_result

        max_score = self._scan_enabled_patterns(tool_output, matches)
        block_reasons = self._blocked_pattern_reasons(tool_output)
        if block_reasons:
            max_score = 1.0
        risk_level, is_blocked, block_reasons = self._assess_risk(
            max_score,
            matches,
            block_reasons,
        )

        result = ChainScanResult(
            risk_score=max_score,
            risk_level=risk_level,
            matches=matches,
            is_blocked=is_blocked,
            block_reasons=block_reasons,
            metadata={
                "source_tool": source_tool,
                "target_tool": target_tool,
                "output_length": len(tool_output),
                "match_count": len(matches),
            },
        )

        if context:
            context.add_tool(source_tool, max_score)
            result.metadata["chain_depth"] = context.depth
            result.metadata["chain_id"] = context.chain_id

        # Audit logging
        if self.config.audit_all_chains:
            self._audit(result, source_tool, target_tool, context)

        return result

    def _audit(
        self,
        result: ChainScanResult,
        source_tool: str,
        target_tool: Optional[str],
        context: Optional[ToolChainContext],
    ) -> None:
        """Log audit event."""
        log_data = {
            "event": "tool_chain_scan",
            "source_tool": source_tool,
            "target_tool": target_tool,
            "risk_score": result.risk_score,
            "risk_level": result.risk_level.value,
            "is_blocked": result.is_blocked,
            "match_count": len(result.matches),
            "timestamp": result.timestamp.isoformat(),
        }

        if context:
            log_data["chain_id"] = context.chain_id
            log_data["chain_depth"] = context.depth
            log_data["user_id"] = context.user_id
            log_data["project_id"] = context.project_id

        if result.is_blocked:
            log_data["block_reasons"] = result.block_reasons
            logger.warning(f"Tool chain output BLOCKED: {source_tool}", extra=log_data)
        elif result.risk_level in (ChainRiskLevel.HIGH, ChainRiskLevel.MEDIUM):
            logger.warning(f"Tool chain output high risk: {source_tool}", extra=log_data)
        else:
            logger.debug(f"Tool chain output scanned: {source_tool}", extra=log_data)

        if self.config.audit_callback:
            try:
                self.config.audit_callback(log_data)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

    def create_sanitized_output(
        self,
        tool_output: str,
        result: ChainScanResult,
    ) -> str:
        """
        Create a sanitized version of tool output.

        Replaces detected sensitive patterns with safe placeholders.

        Args:
            tool_output: Original tool output.
            result: Scan result with matches.

        Returns:
            Sanitized output string.
        """
        if not result.matches:
            return tool_output

        # Sort matches by position (reverse order for replacement)
        sorted_matches = sorted(result.matches, key=lambda m: m.start_pos, reverse=True)

        sanitized = tool_output
        for match in sorted_matches:
            # Generate placeholder based on category
            if match.category == "credential":
                placeholder = f"[REDACTED_{match.pattern_name.upper()}]"
            elif match.category == "pii":
                placeholder = f"[REDACTED_{match.pattern_name.upper()}]"
            elif match.category in ("injection", "command_injection"):
                placeholder = "[BLOCKED_CONTENT]"
            else:
                placeholder = "[REDACTED]"

            sanitized = sanitized[: match.start_pos] + placeholder + sanitized[match.end_pos :]

        return sanitized


# =============================================================================
# Integration
# =============================================================================


_global_scanner: Optional[ToolChainScanner] = None


def configure_chain_scanner(
    config: Optional[ChainScannerConfig] = None,
) -> ToolChainScanner:
    """
    Configure global tool chain scanner.

    Args:
        config: Optional configuration.

    Returns:
        Configured scanner instance.
    """
    global _global_scanner
    _global_scanner = ToolChainScanner(config)
    return _global_scanner


def scan_tool_output(
    tool_output: str,
    source_tool: str,
    target_tool: Optional[str] = None,
    context: Optional[ToolChainContext] = None,
    raise_on_block: bool = True,
) -> ChainScanResult:
    """
    Scan tool output using global scanner.

    Args:
        tool_output: Output from a tool.
        source_tool: Tool that produced the output.
        target_tool: Tool that will receive the output.
        context: Chain context for tracking.
        raise_on_block: If True, raise exception when blocked.

    Returns:
        ChainScanResult.

    Raises:
        SecurityError: If output blocked and raise_on_block=True.
    """
    scanner = _global_scanner or ToolChainScanner()
    result = scanner.scan_output(tool_output, source_tool, target_tool, context)

    if result.is_blocked and raise_on_block:
        from umrs.model_verification import SecurityError

        raise SecurityError(
            f"Tool chain output blocked from {source_tool}: {', '.join(result.block_reasons)}"
        )

    return result


def create_chain_context(
    chain_id: Optional[str] = None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> ToolChainContext:
    """
    Create a new tool chain context for tracking.

    Args:
        chain_id: Unique chain identifier (auto-generated if None).
        user_id: User who initiated the chain.
        project_id: Project context.

    Returns:
        New ToolChainContext instance.
    """
    import uuid

    return ToolChainContext(
        chain_id=chain_id or str(uuid.uuid4()),
        user_id=user_id,
        project_id=project_id,
    )
