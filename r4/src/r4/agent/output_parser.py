"""Composable output parsing framework for HPC experiment results.

Provides a chainable set of parsers that extract structured data from
raw HPC experiment logs, stdout, and output files. Parsers can be
composed to handle multi-format outputs.

Usage::

    from r4.agent.output_parser import RegexParser, JSONParser, CompositeParser

    # Single parser
    parser = RegexParser(pattern=r"loss: (?P<loss>\\d+\\.\\d+)")
    result = parser.parse(raw_output, {"loss": float})

    # Composite parser — later parsers overwrite earlier keys
    composite = CompositeParser([
        RegexParser(pattern=r"GPU (?P<gpu_id>\\d+): (?P<util>\\d+)%"),
        JSONParser(),  # Extracts JSON from log lines
    ])
    result = composite.parse(output, {"gpu_id": int, "util": int, "loss": float})
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)


class OutputParser(ABC):
    """Abstract base class for experiment output parsers."""

    @abstractmethod
    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        """Parse raw output into a structured dict.

        Args:
            raw_output: Raw text output from the experiment.
            schema: Expected keys and their target types for coercion.

        Returns:
            Dict of extracted values. Keys not found in the output
            are simply omitted (no KeyError).
        """


class RegexParser(OutputParser):
    """Extract named groups from output using a regex pattern.

    If multiple matches are found, the **last** match wins (typical
    for iterative training logs where final values matter most).

    Args:
        pattern: Regex with named groups (``(?P<name>...)``).
        flags: Regex compilation flags (default: 0).
    """

    def __init__(self, pattern: str, flags: int = 0) -> None:
        self._pattern = re.compile(pattern, flags)

    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for match in self._pattern.finditer(raw_output):
            for key, value in match.groupdict().items():
                if value is not None:
                    result[key] = value

        # Type coercion based on schema
        for key, type_fn in schema.items():
            if key in result and result[key] is not None:
                try:
                    result[key] = type_fn(result[key])
                except (ValueError, TypeError):
                    logger.debug("Failed to coerce %s=%r to %s", key, result[key], type_fn)

        return result


class JSONParser(OutputParser):
    """Parse JSON or JSONL from experiment output.

    For JSONL, the **last** line's values win (matches training log
    convention). For a single JSON object, extracts all top-level keys.

    Args:
        key: Optional key to extract from a nested JSON object.
             If provided, parses ``data[key]`` instead of ``data``.
    """

    def __init__(self, key: Optional[str] = None) -> None:
        self._key = key

    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        raw_output = raw_output.strip()
        if not raw_output:
            return {}

        # Try JSONL first (one JSON object per line)
        lines = [line.strip() for line in raw_output.split("\n") if line.strip()]
        result: Dict[str, Any] = {}

        for line in lines:
            try:
                data = json.loads(line)
                if self._key and isinstance(data, dict):
                    data = data.get(self._key, {})
                if isinstance(data, dict):
                    result.update(data)
            except json.JSONDecodeError:
                continue

        # If no JSONL lines parsed, try as single JSON object
        if not result:
            try:
                data = json.loads(raw_output)
                if self._key and isinstance(data, dict):
                    data = data.get(self._key, {})
                if isinstance(data, dict):
                    result = dict(data)
            except json.JSONDecodeError:
                return {}

        # Type coercion
        for key, type_fn in schema.items():
            if key in result and result[key] is not None:
                try:
                    result[key] = type_fn(result[key])
                except (ValueError, TypeError):
                    logger.debug("Failed to coerce %s=%r to %s", key, result[key], type_fn)

        return result


class CSVParser(OutputParser):
    """Parse CSV output from experiment logs.

    Returns the **last** row as a dict (final iteration values).

    Args:
        delimiter: Column delimiter (default: comma).
        has_header: Whether the first row is a header (default: True).
    """

    def __init__(self, delimiter: str = ",", has_header: bool = True) -> None:
        self._delimiter = delimiter
        self._has_header = has_header

    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        raw_output = raw_output.strip()
        if not raw_output:
            return {}

        lines = [line.strip() for line in raw_output.split("\n") if line.strip()]
        if not lines:
            return {}

        if not self._has_header or len(lines) < 2:
            return {}

        headers = lines[0].split(self._delimiter)
        last_row = lines[-1].split(self._delimiter)

        result: Dict[str, Any] = {}
        for i, header in enumerate(headers):
            if i < len(last_row):
                result[header.strip()] = last_row[i].strip()

        # Type coercion
        for key, type_fn in schema.items():
            if key in result and result[key] is not None:
                try:
                    result[key] = type_fn(result[key])
                except (ValueError, TypeError):
                    logger.debug("Failed to coerce %s=%r to %s", key, result[key], type_fn)

        return result


class LLMParser(OutputParser):
    """Use LLM to extract structured data from experiment output.

    Falls back gracefully if no client is available or if the LLM
    call fails.

    Args:
        client: R4 client for LLM calls (optional).
        model: Model name for LLM calls.
        max_output_chars: Maximum characters of output to send to LLM.
    """

    def __init__(
        self,
        client: Any = None,
        model: str = "rivault-gpt-oss-static",
        max_output_chars: int = 8000,
    ) -> None:
        self._client = client
        self._model = model
        self._max_output_chars = max_output_chars

    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        if not self._client:
            return {}

        truncated = raw_output[: self._max_output_chars]
        schema_desc = ", ".join(f"{k}: {v.__name__}" for k, v in schema.items())

        prompt = (
            f"Extract the following fields from this experiment output:\n"
            f"Fields: {schema_desc}\n\n"
            f"Output:\n{truncated}\n\n"
            f"Respond with a JSON object containing only the extracted fields."
        )

        try:
            response = self._client.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            data = json.loads(content)
            if isinstance(data, dict):
                # Type coercion
                for key, type_fn in schema.items():
                    if key in data and data[key] is not None:
                        try:
                            data[key] = type_fn(data[key])
                        except (ValueError, TypeError):
                            pass
                return data
        except Exception:
            logger.debug("LLM parser failed, returning empty result")

        return {}


class CompositeParser(OutputParser):
    """Chain multiple parsers — later parsers overwrite earlier keys.

    Each parser is tried independently; exceptions in individual parsers
    are caught and logged, allowing partial results from earlier parsers
    to survive.

    Args:
        parsers: Ordered list of parsers to apply.
    """

    def __init__(self, parsers: Sequence[OutputParser]) -> None:
        self._parsers = list(parsers)

    def parse(self, raw_output: str, schema: Dict[str, type]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for parser in self._parsers:
            try:
                partial = parser.parse(raw_output, schema)
                result.update(partial)
            except Exception:
                logger.debug(
                    "Parser %s failed, continuing with partial results",
                    type(parser).__name__,
                )
        return result
