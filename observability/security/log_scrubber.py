"""Log Scrubbing Pipeline (T-251 to T-253).

Phase 8 of Security Hardening: Secure Observability.

This module provides:
- PII scrubbing from logs
- Secrets/API key redaction
- Configurable scrubbing patterns
- Log sanitization pipeline

Attack Model:
Sensitive data can leak through:
1. Verbose error messages
2. Debug logging with request content
3. Stack traces with variable values
4. Third-party library logs

Defense:
- Pattern-based scrubbing of known sensitive data
- Regex-based redaction of API keys, tokens
- Email/phone/SSN detection and masking
- Configurable scrubbing rules per log source
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Types
# =============================================================================


class ScrubType(str, Enum):
    """Type of sensitive data being scrubbed."""

    API_KEY = "api_key"
    TOKEN = "token"
    PASSWORD = "password"
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    CUSTOM = "custom"


@dataclass
class ScrubPattern:
    """Pattern definition for log scrubbing."""

    name: str
    scrub_type: ScrubType
    pattern: re.Pattern
    replacement: str = "[REDACTED]"
    enabled: bool = True

    def scrub(self, text: str) -> tuple[str, int]:
        """Scrub text and return (scrubbed_text, match_count)."""
        if not self.enabled:
            return text, 0

        count = len(self.pattern.findall(text))
        scrubbed = self.pattern.sub(self.replacement, text)
        return scrubbed, count


@dataclass
class ScrubResult:
    """Result of scrubbing operation."""

    original_length: int
    scrubbed_length: int
    redactions: dict[str, int]  # scrub_type -> count

    @property
    def total_redactions(self) -> int:
        return sum(self.redactions.values())

    def to_dict(self) -> dict:
        return {
            "original_length": self.original_length,
            "scrubbed_length": self.scrubbed_length,
            "total_redactions": self.total_redactions,
            "redactions": self.redactions,
        }


# =============================================================================
# Built-in Patterns
# =============================================================================


# API Keys and Tokens
API_KEY_PATTERNS = [
    # Generic API key patterns
    ScrubPattern(
        name="generic_api_key",
        scrub_type=ScrubType.API_KEY,
        pattern=re.compile(
            r'\b(api[_-]?key|apikey|api_token)\s*[=:]\s*["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            re.IGNORECASE,
        ),
        replacement=r"\1=[API_KEY_REDACTED]",
    ),
    # Bearer tokens
    ScrubPattern(
        name="bearer_token",
        scrub_type=ScrubType.TOKEN,
        pattern=re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
        replacement="Bearer [TOKEN_REDACTED]",
    ),
    # OpenAI API keys
    ScrubPattern(
        name="openai_key",
        scrub_type=ScrubType.API_KEY,
        pattern=re.compile(r"sk-[a-zA-Z0-9]{20,}"),
        replacement="[OPENAI_KEY_REDACTED]",
    ),
    # GitHub tokens
    ScrubPattern(
        name="github_token",
        scrub_type=ScrubType.TOKEN,
        pattern=re.compile(r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}"),
        replacement="[GITHUB_TOKEN_REDACTED]",
    ),
    # AWS credentials
    ScrubPattern(
        name="aws_access_key",
        scrub_type=ScrubType.API_KEY,
        pattern=re.compile(r"AKIA[A-Z0-9]{16}"),
        replacement="[AWS_KEY_REDACTED]",
    ),
    ScrubPattern(
        name="aws_secret_key",
        scrub_type=ScrubType.API_KEY,
        pattern=re.compile(
            r'(aws_secret|secret_key)\s*[=:]\s*["\']?[a-zA-Z0-9+/]{40}["\']?', re.IGNORECASE
        ),
        replacement=r"[AWS_SECRET_REDACTED]",
    ),
]

# Passwords
PASSWORD_PATTERNS = [
    ScrubPattern(
        name="password_field",
        scrub_type=ScrubType.PASSWORD,
        pattern=re.compile(
            r'(password|passwd|pwd|secret)\s*[=:]\s*["\']?[^\s"\']+["\']?', re.IGNORECASE
        ),
        replacement=r"\1=[PASSWORD_REDACTED]",
    ),
    ScrubPattern(
        name="password_json",
        scrub_type=ScrubType.PASSWORD,
        pattern=re.compile(r'"(password|passwd|pwd|secret)"\s*:\s*"[^"]*"', re.IGNORECASE),
        replacement=r'"\1": "[PASSWORD_REDACTED]"',
    ),
]

# PII Patterns
PII_PATTERNS = [
    # Email addresses
    ScrubPattern(
        name="email",
        scrub_type=ScrubType.EMAIL,
        pattern=re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
        replacement="[EMAIL_REDACTED]",
    ),
    # Phone numbers (various formats)
    ScrubPattern(
        name="phone_us",
        scrub_type=ScrubType.PHONE,
        pattern=re.compile(r"\b(\+1\s?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
        replacement="[PHONE_REDACTED]",
    ),
    ScrubPattern(
        name="phone_intl",
        scrub_type=ScrubType.PHONE,
        pattern=re.compile(r"\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}"),
        replacement="[PHONE_REDACTED]",
    ),
    # SSN
    ScrubPattern(
        name="ssn",
        scrub_type=ScrubType.SSN,
        pattern=re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"),
        replacement="[SSN_REDACTED]",
    ),
    # Credit card numbers (basic Luhn-valid patterns)
    ScrubPattern(
        name="credit_card",
        scrub_type=ScrubType.CREDIT_CARD,
        pattern=re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        replacement="[CARD_REDACTED]",
    ),
]

# IP Address (optional - may want to keep for debugging)
IP_PATTERNS = [
    ScrubPattern(
        name="ipv4",
        scrub_type=ScrubType.IP_ADDRESS,
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
        replacement="[IP_REDACTED]",
        enabled=False,  # Disabled by default (useful for debugging)
    ),
]


# =============================================================================
# Scrubbing Configuration
# =============================================================================


@dataclass
class ScrubConfig:
    """Configuration for log scrubbing."""

    # Enable/disable categories
    scrub_api_keys: bool = True
    scrub_passwords: bool = True
    scrub_pii: bool = True
    scrub_ips: bool = False  # Disabled by default

    # Custom patterns
    custom_patterns: list[ScrubPattern] = field(default_factory=list)

    # Logging
    log_redaction_count: bool = True
    max_log_length: int = 10000  # Truncate logs longer than this


# =============================================================================
# Log Scrubber
# =============================================================================


class LogScrubber:
    """Log scrubbing pipeline.

    Scrubs sensitive data from log messages before output.

    Example:
        scrubber = LogScrubber()

        # Scrub a log message
        clean, result = scrubber.scrub("API key: sk-abc123...")
        # clean = "API key: [OPENAI_KEY_REDACTED]..."
    """

    def __init__(self, config: Optional[ScrubConfig] = None):
        """Initialize scrubber.

        Args:
            config: Scrubbing configuration
        """
        self.config = config or ScrubConfig()
        self._patterns = self._build_patterns()

    def _build_patterns(self) -> list[ScrubPattern]:
        """Build active scrubbing patterns."""
        patterns = []

        if self.config.scrub_api_keys:
            patterns.extend(API_KEY_PATTERNS)

        if self.config.scrub_passwords:
            patterns.extend(PASSWORD_PATTERNS)

        if self.config.scrub_pii:
            patterns.extend(PII_PATTERNS)

        if self.config.scrub_ips:
            for p in IP_PATTERNS:
                p.enabled = True
            patterns.extend(IP_PATTERNS)

        patterns.extend(self.config.custom_patterns)

        return patterns

    def scrub(self, text: str) -> tuple[str, ScrubResult]:
        """Scrub sensitive data from text.

        Args:
            text: Text to scrub

        Returns:
            Tuple of (scrubbed_text, scrub_result)
        """
        if not text:
            return text, ScrubResult(
                original_length=0,
                scrubbed_length=0,
                redactions={},
            )

        original_length = len(text)
        redactions: dict[str, int] = {}

        # Apply each pattern
        scrubbed = text
        for pattern in self._patterns:
            scrubbed, count = pattern.scrub(scrubbed)
            if count > 0:
                type_name = pattern.scrub_type.value
                redactions[type_name] = redactions.get(type_name, 0) + count

        # Truncate if needed
        if len(scrubbed) > self.config.max_log_length:
            scrubbed = scrubbed[: self.config.max_log_length] + "... [TRUNCATED]"

        result = ScrubResult(
            original_length=original_length,
            scrubbed_length=len(scrubbed),
            redactions=redactions,
        )

        if result.total_redactions > 0 and self.config.log_redaction_count:
            logger.debug(f"Scrubbed {result.total_redactions} sensitive items")

        return scrubbed, result

    def add_pattern(self, pattern: ScrubPattern) -> None:
        """Add a custom scrubbing pattern."""
        self._patterns.append(pattern)


# =============================================================================
# Logging Filter
# =============================================================================


class ScrubFilter(logging.Filter):
    """Logging filter that scrubs sensitive data.

    Add to any logger to automatically scrub log messages.

    Example:
        import logging

        logger = logging.getLogger()
        logger.addFilter(ScrubFilter())
    """

    def __init__(self, scrubber: Optional[LogScrubber] = None):
        """Initialize filter.

        Args:
            scrubber: LogScrubber instance
        """
        super().__init__()
        self._scrubber = scrubber or LogScrubber()

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and scrub log record."""
        if record.msg:
            # Scrub the message
            if isinstance(record.msg, str):
                scrubbed, _ = self._scrubber.scrub(record.msg)
                record.msg = scrubbed

            # Scrub args if they exist
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(
                        self._scrubber.scrub(str(arg))[0] if isinstance(arg, str) else arg
                        for arg in record.args
                    )
                elif isinstance(record.args, dict):
                    record.args = {
                        k: self._scrubber.scrub(str(v))[0] if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }

        return True


# =============================================================================
# Structured Log Scrubber
# =============================================================================


class StructuredLogScrubber:
    """Scrubber for structured (JSON) logs.

    Recursively scrubs sensitive data from structured log entries.
    """

    # Fields that should always be redacted
    SENSITIVE_FIELDS = {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "api_token",
        "token",
        "bearer",
        "authorization",
        "auth",
        "credit_card",
        "cc_number",
        "cvv",
        "ssn",
        "social_security",
    }

    def __init__(self, scrubber: Optional[LogScrubber] = None):
        """Initialize structured scrubber.

        Args:
            scrubber: LogScrubber for text values
        """
        self._scrubber = scrubber or LogScrubber()

    def scrub(self, data: Any, depth: int = 0, max_depth: int = 10) -> Any:
        """Scrub structured data.

        Args:
            data: Data to scrub (dict, list, or primitive)
            depth: Current recursion depth
            max_depth: Maximum recursion depth

        Returns:
            Scrubbed data
        """
        if depth > max_depth:
            return "[MAX_DEPTH_EXCEEDED]"

        if data is None:
            return None

        if isinstance(data, str):
            scrubbed, _ = self._scrubber.scrub(data)
            return scrubbed

        if isinstance(data, dict):
            return self._scrub_dict(data, depth)

        if isinstance(data, (list, tuple)):
            return [self.scrub(item, depth + 1, max_depth) for item in data]

        # Primitives (int, float, bool) - pass through
        return data

    def _scrub_dict(self, data: dict, depth: int) -> dict:
        """Scrub dictionary data."""
        result = {}

        for key, value in data.items():
            key_lower = str(key).lower()

            # Check if key is sensitive
            if any(s in key_lower for s in self.SENSITIVE_FIELDS):
                result[key] = "[REDACTED]"
            else:
                result[key] = self.scrub(value, depth + 1)

        return result


# =============================================================================
# Global Instance
# =============================================================================


_log_scrubber: Optional[LogScrubber] = None


def get_log_scrubber(config: Optional[ScrubConfig] = None) -> LogScrubber:
    """Get global log scrubber instance."""
    global _log_scrubber
    if _log_scrubber is None:
        _log_scrubber = LogScrubber(config)
    return _log_scrubber


def scrub_text(text: str) -> str:
    """Convenience function to scrub text.

    Args:
        text: Text to scrub

    Returns:
        Scrubbed text
    """
    scrubber = get_log_scrubber()
    scrubbed, _ = scrubber.scrub(text)
    return scrubbed


def install_scrub_filter(logger_name: Optional[str] = None) -> None:
    """Install scrubbing filter on logger.

    Args:
        logger_name: Logger name (None for root logger)
    """
    target_logger = logging.getLogger(logger_name)
    target_logger.addFilter(ScrubFilter(get_log_scrubber()))
