"""Content scanner for detecting sensitive data in agent workflows.

Implements hybrid sensitivity detection:
- Automated scanning (PII, secrets, research patterns)
- User-provided tags for explicit marking
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# =============================================================================
# Sensitivity Flags (exported to egress.py)
# =============================================================================


class SensitivityLevel(Flag):
    """Flags indicating types of sensitive content detected."""

    NONE = 0
    PII = auto()  # Personal identifiable information
    SECRETS = auto()  # API keys, passwords, tokens
    RESEARCH_DATA = auto()  # Unpublished research data
    PROPRIETARY = auto()  # Proprietary algorithms/code
    MEDICAL = auto()  # Medical/health data (HIPAA-adjacent)
    FINANCIAL = auto()  # Financial data
    CREDENTIALS = auto()  # Login credentials
    USER_TAGGED = auto()  # Explicitly tagged by user


# =============================================================================
# Pattern Definitions
# =============================================================================

# PII patterns
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Email addresses
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", re.IGNORECASE)),
    # Phone numbers (various formats)
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b")),
    # Social Security Numbers (US)
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Credit card numbers
    ("credit_card", re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b")),
    # Japanese phone numbers
    ("jp_phone", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{4}\b")),
    # IP addresses
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # Date of birth patterns
    (
        "dob",
        re.compile(
            r"\b(?:DOB|birth\s*date|born)\s*[:=]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE
        ),
    ),
]

# Secret/credential patterns
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # AWS keys
    ("aws_access_key", re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"\b[A-Za-z0-9/+=]{40}\b")),
    # Generic API keys
    (
        "api_key",
        re.compile(
            r"\b(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}['\"]?", re.IGNORECASE
        ),
    ),
    # Bearer tokens
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b")),
    # Private keys
    ("private_key", re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----")),
    # GitHub tokens
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b")),
    # Slack tokens
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # OpenAI keys
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{48}\b")),
    # Generic password patterns
    (
        "password",
        re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?", re.IGNORECASE),
    ),
    # Connection strings
    (
        "connection_string",
        re.compile(r"\b(?:mongodb|postgres|mysql|redis)://[^\s]+@[^\s]+", re.IGNORECASE),
    ),
]

# Research data patterns (common in scientific workflows)
RESEARCH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Molecular data
    ("smiles", re.compile(r"\b[CNOPSFIBrcnops@+\-\[\]\(\)=#$]{10,}\b")),  # SMILES notation
    ("inchi", re.compile(r"\bInChI=1S?/[A-Za-z0-9+\-\(\)/,]+\b")),  # InChI
    # Gene sequences
    ("dna_sequence", re.compile(r"\b[ATCG]{20,}\b")),
    ("protein_sequence", re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]{15,}\b")),
    # PDB identifiers
    ("pdb_id", re.compile(r"\b[0-9][A-Za-z0-9]{3}\b")),
    # UniProt identifiers
    ("uniprot_id", re.compile(r"\b[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9]\b")),
    # arXiv identifiers
    ("arxiv_id", re.compile(r"\b(?:arXiv:)?\d{4}\.\d{4,5}(?:v\d+)?\b")),
    # DOI
    ("doi", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)),
]

# Medical data patterns
MEDICAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # ICD codes
    ("icd_code", re.compile(r"\b[A-Z]\d{2}(?:\.\d{1,2})?\b")),
    # Medical record numbers (generic)
    ("mrn", re.compile(r"\b(?:MRN|medical\s*record)\s*[:=]?\s*\d{6,}\b", re.IGNORECASE)),
    # Drug names (generic pattern for common suffixes)
    (
        "drug_name",
        re.compile(r"\b[A-Za-z]+(?:mab|nib|cept|tide|cin|mycin|vastatin|pril)\b", re.IGNORECASE),
    ),
]


# =============================================================================
# Scan Result
# =============================================================================


@dataclass
class ScanMatch:
    """A single match from content scanning."""

    pattern_name: str
    matched_text: str
    start_pos: int
    end_pos: int
    category: SensitivityLevel

    def redacted(self) -> str:
        """Return a redacted version of the matched text."""
        if len(self.matched_text) <= 4:
            return "****"
        return f"{self.matched_text[:2]}...{self.matched_text[-2:]}"


@dataclass
class ScanResult:
    """Result of content scanning."""

    sensitivity: SensitivityLevel
    matches: list[ScanMatch] = field(default_factory=list)
    user_tags: list[str] = field(default_factory=list)

    @property
    def is_sensitive(self) -> bool:
        """Check if any sensitivity was detected."""
        return self.sensitivity != SensitivityLevel.NONE

    def summary(self) -> str:
        """Human-readable summary of findings."""
        if not self.is_sensitive:
            return "No sensitive content detected"

        parts = []
        if SensitivityLevel.PII in self.sensitivity:
            pii_matches = [m for m in self.matches if m.category == SensitivityLevel.PII]
            parts.append(f"PII ({len(pii_matches)} matches)")
        if SensitivityLevel.SECRETS in self.sensitivity:
            secret_matches = [m for m in self.matches if m.category == SensitivityLevel.SECRETS]
            parts.append(f"Secrets ({len(secret_matches)} matches)")
        if SensitivityLevel.RESEARCH_DATA in self.sensitivity:
            research_matches = [
                m for m in self.matches if m.category == SensitivityLevel.RESEARCH_DATA
            ]
            parts.append(f"Research data ({len(research_matches)} matches)")
        if SensitivityLevel.MEDICAL in self.sensitivity:
            medical_matches = [m for m in self.matches if m.category == SensitivityLevel.MEDICAL]
            parts.append(f"Medical data ({len(medical_matches)} matches)")
        if SensitivityLevel.USER_TAGGED in self.sensitivity:
            parts.append(f"User tagged: {', '.join(self.user_tags)}")

        return "; ".join(parts)


# =============================================================================
# Content Scanner
# =============================================================================


class ContentScanner:
    """Scans content for sensitive data patterns.

    Implements hybrid detection:
    1. Automated pattern matching (PII, secrets, research data)
    2. User-provided sensitivity tags
    3. Optional NER for enhanced entity detection (requires spaCy)

    Example:
        scanner = ContentScanner()
        result = scanner.scan("My email is user@example.com")
        if result.is_sensitive:
            print(result.summary())
    """

    def __init__(
        self,
        enable_pii: bool = True,
        enable_secrets: bool = True,
        enable_research: bool = True,
        enable_medical: bool = True,
        enable_ner: bool = False,
        custom_patterns: list[tuple[str, re.Pattern[str], SensitivityLevel]] | None = None,
    ):
        """Initialize the content scanner.

        Args:
            enable_pii: Scan for PII patterns
            enable_secrets: Scan for secret/credential patterns
            enable_research: Scan for research data patterns
            enable_medical: Scan for medical data patterns
            enable_ner: Enable spaCy NER for enhanced detection (slower)
            custom_patterns: Additional patterns as (name, pattern, category) tuples
        """
        self.enable_pii = enable_pii
        self.enable_secrets = enable_secrets
        self.enable_research = enable_research
        self.enable_medical = enable_medical
        self.enable_ner = enable_ner
        self.custom_patterns = custom_patterns or []

        # Lazy load spaCy if NER is enabled
        self._nlp: Any = None

    def _get_nlp(self) -> Any:
        """Lazy load spaCy NLP model."""
        if self._nlp is None and self.enable_ner:
            try:
                import spacy

                try:
                    self._nlp = spacy.load("en_core_web_sm")
                except OSError:
                    # Model not installed, disable NER
                    self.enable_ner = False
                    self._nlp = None
            except ImportError:
                # spaCy not installed, disable NER
                self.enable_ner = False
                self._nlp = None
        return self._nlp

    def scan(
        self,
        content: str,
        user_tags: list[str] | None = None,
        max_matches_per_category: int = 10,
    ) -> ScanResult:
        """Scan content for sensitive data.

        Args:
            content: Text content to scan
            user_tags: User-provided sensitivity tags (e.g., ["confidential", "proprietary"])
            max_matches_per_category: Limit matches per pattern category to avoid noise

        Returns:
            ScanResult with detected sensitivity and matches
        """
        matches: list[ScanMatch] = []
        sensitivity = SensitivityLevel.NONE

        # User tags
        if user_tags:
            sensitivity |= SensitivityLevel.USER_TAGGED

        # PII patterns
        if self.enable_pii:
            pii_matches = self._scan_patterns(
                content, PII_PATTERNS, SensitivityLevel.PII, max_matches_per_category
            )
            if pii_matches:
                sensitivity |= SensitivityLevel.PII
                matches.extend(pii_matches)

        # Secret patterns
        if self.enable_secrets:
            secret_matches = self._scan_patterns(
                content, SECRET_PATTERNS, SensitivityLevel.SECRETS, max_matches_per_category
            )
            if secret_matches:
                sensitivity |= SensitivityLevel.SECRETS
                matches.extend(secret_matches)

        # Research data patterns
        if self.enable_research:
            research_matches = self._scan_patterns(
                content, RESEARCH_PATTERNS, SensitivityLevel.RESEARCH_DATA, max_matches_per_category
            )
            if research_matches:
                sensitivity |= SensitivityLevel.RESEARCH_DATA
                matches.extend(research_matches)

        # Medical patterns
        if self.enable_medical:
            medical_matches = self._scan_patterns(
                content, MEDICAL_PATTERNS, SensitivityLevel.MEDICAL, max_matches_per_category
            )
            if medical_matches:
                sensitivity |= SensitivityLevel.MEDICAL
                matches.extend(medical_matches)

        # Custom patterns
        for name, pattern, category in self.custom_patterns:
            custom_matches = self._scan_patterns(
                content, [(name, pattern)], category, max_matches_per_category
            )
            if custom_matches:
                sensitivity |= category
                matches.extend(custom_matches)

        # NER-based detection (optional)
        if self.enable_ner:
            ner_matches = self._scan_ner(content, max_matches_per_category)
            if ner_matches:
                sensitivity |= SensitivityLevel.PII
                matches.extend(ner_matches)

        return ScanResult(sensitivity=sensitivity, matches=matches, user_tags=user_tags or [])

    def _scan_patterns(
        self,
        content: str,
        patterns: list[tuple[str, re.Pattern[str]]],
        category: SensitivityLevel,
        max_matches: int,
    ) -> list[ScanMatch]:
        """Scan content against a set of patterns."""
        matches: list[ScanMatch] = []
        count = 0

        for name, pattern in patterns:
            for match in pattern.finditer(content):
                if count >= max_matches:
                    break
                matches.append(
                    ScanMatch(
                        pattern_name=name,
                        matched_text=match.group(),
                        start_pos=match.start(),
                        end_pos=match.end(),
                        category=category,
                    )
                )
                count += 1
            if count >= max_matches:
                break

        return matches

    def _scan_ner(self, content: str, max_matches: int) -> list[ScanMatch]:
        """Scan content using NER for entity detection."""
        nlp = self._get_nlp()
        if nlp is None:
            return []

        matches: list[ScanMatch] = []
        doc = nlp(content[:100000])  # Limit for performance

        pii_labels = {"PERSON", "GPE", "ORG", "FAC", "LOC"}  # Named entity labels that may be PII

        for ent in doc.ents:
            if len(matches) >= max_matches:
                break
            if ent.label_ in pii_labels:
                matches.append(
                    ScanMatch(
                        pattern_name=f"ner_{ent.label_.lower()}",
                        matched_text=ent.text,
                        start_pos=ent.start_char,
                        end_pos=ent.end_char,
                        category=SensitivityLevel.PII,
                    )
                )

        return matches

    def scan_dict(
        self,
        data: dict[str, Any],
        user_tags: list[str] | None = None,
        max_matches_per_category: int = 10,
    ) -> ScanResult:
        """Scan a dictionary (e.g., JSON payload) for sensitive data.

        Recursively scans all string values in the dictionary.

        Args:
            data: Dictionary to scan
            user_tags: User-provided sensitivity tags
            max_matches_per_category: Limit matches per category

        Returns:
            Combined ScanResult from all values
        """
        import json

        # Convert to JSON string for scanning
        content = json.dumps(data, default=str)
        return self.scan(
            content, user_tags=user_tags, max_matches_per_category=max_matches_per_category
        )

    def redact(
        self,
        content: str,
        categories: SensitivityLevel | None = None,
    ) -> str:
        """Redact sensitive content.

        Args:
            content: Original content
            categories: Which categories to redact (None = all)

        Returns:
            Content with sensitive data redacted
        """
        result = self.scan(content)
        if not result.is_sensitive:
            return content

        # Sort matches by position (descending) to replace from end to start
        matches = sorted(result.matches, key=lambda m: m.start_pos, reverse=True)

        redacted = content
        for match in matches:
            if categories is None or match.category in categories:
                redacted = (
                    redacted[: match.start_pos]
                    + f"[REDACTED:{match.pattern_name}]"
                    + redacted[match.end_pos :]
                )

        return redacted


# =============================================================================
# Convenience Functions
# =============================================================================


def quick_scan(content: str) -> ScanResult:
    """Quick scan with default settings.

    Args:
        content: Text to scan

    Returns:
        ScanResult
    """
    scanner = ContentScanner()
    return scanner.scan(content)


def is_sensitive(content: str) -> bool:
    """Check if content contains sensitive data.

    Args:
        content: Text to check

    Returns:
        True if any sensitivity detected
    """
    return quick_scan(content).is_sensitive


def redact(content: str) -> str:
    """Redact all sensitive data from content.

    Args:
        content: Original content

    Returns:
        Redacted content
    """
    scanner = ContentScanner()
    return scanner.redact(content)


# =============================================================================
# Scanner Factory for Project-Specific Configuration
# =============================================================================


def create_scanner_for_domain(domain: str) -> ContentScanner:
    """Create a scanner configured for a specific research domain.

    Args:
        domain: Research domain (e.g., "genomics", "chemistry", "medical")

    Returns:
        Configured ContentScanner
    """
    if domain == "genomics":
        return ContentScanner(
            enable_pii=True,
            enable_secrets=True,
            enable_research=True,
            enable_medical=True,
        )
    elif domain == "chemistry":
        # Add SMILES/InChI pattern emphasis
        return ContentScanner(
            enable_pii=True,
            enable_secrets=True,
            enable_research=True,
            enable_medical=False,
        )
    elif domain == "medical":
        return ContentScanner(
            enable_pii=True,
            enable_secrets=True,
            enable_research=True,
            enable_medical=True,
            enable_ner=True,  # Enable NER for medical contexts
        )
    else:
        # Default: all detection enabled
        return ContentScanner()
