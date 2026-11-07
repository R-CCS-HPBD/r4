"""Provider abstraction layer for r4.

Defines the :class:`Provider` ABC that unifies access to any LLM backend —
LiteLLM, Ollama, or any OpenAI-compatible endpoint. Concrete
implementations live alongside this module:

- :class:`LiteLLMProvider` — delegates to the platform gateway / LiteLLM.
- :class:`OllamaProvider` — local Ollama instance.
- :class:`OpenAICompatibleProvider` — generic OpenAI-compatible endpoint.

The :class:`ProviderRegistry` keeps track of configured providers and is
used by the smart router (Phase 3.1) to score and select providers per
request.

This module has no dependency on any r4 sub-client beyond
:mod:`r4.chat_types` and :mod:`r4.errors` to avoid import cycles.

Use :func:`build_registry` (from :mod:`.factory`) to create a populated
:class:`ProviderRegistry` from the user's configured sources.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional


class ProviderStatus(Enum):
    """Health status of a provider."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class ModelCapabilities:
    """Capabilities detected or declared for a model."""

    supports_tool_calling: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_structured_output: bool = False
    max_context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None


@dataclass
class ProviderHealth:
    """Result of a provider health check."""

    status: ProviderStatus = ProviderStatus.UNKNOWN
    latency_ms: float = 0.0
    error: Optional[str] = None
    models_available: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProviderStats:
    """Rolling statistics for a provider (used by the smart router)."""

    total_requests: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 9999.0
    last_error: Optional[str] = None
    last_success_time: float = 0.0

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests


class Provider(ABC):
    """Abstract base class for LLM providers.

    Every provider must implement ``health()``, ``chat()``, ``stream()``,
    ``cost_per_1k()``, and ``model_capabilities()``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short human-readable name (e.g. ``'ollama'``, ``'mini-rivault'``)."""

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """Whether this provider runs on the user's machine (no egress)."""

    @property
    def dial_url(self) -> Optional[str]:
        """The base URL this provider sends its requests to, or ``None`` when
        it picks its own endpoint (an SDK that reads cloud config, such as
        Bedrock's). The data-class gate classifies this URL and reads
        ``None`` as public (2026-10-16)."""
        return None

    @abstractmethod
    def health(self) -> ProviderHealth:
        """Check connectivity and return health + available models."""

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Synchronous chat completion (returns raw API dict)."""

    @abstractmethod
    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Streaming chat completion (yields text chunks)."""

    @abstractmethod
    def cost_per_1k(self, model: Optional[str] = None) -> float:
        """Estimated cost in USD per 1,000 tokens (0.0 for local)."""

    @abstractmethod
    def model_capabilities(self, model: Optional[str] = None) -> ModelCapabilities:
        """Return known capabilities for the given (or default) model."""

    def score(self, strategy: str = "balanced", stats: Optional[ProviderStats] = None) -> float:
        """Score this provider (lower = better). Used by the smart router.

        Args:
            strategy: ``'latency'``, ``'cost'``, or ``'balanced'``.
            stats: Rolling stats; if ``None``, uses cost + defaults.

        Returns:
            A float score; ``float('inf')`` means unavailable.
        """
        health = self.health()
        if health.status == ProviderStatus.UNAVAILABLE:
            return float("inf")

        s = stats or ProviderStats()
        latency_score = (
            s.avg_latency_ms if s.avg_latency_ms < 9999 else health.latency_ms
        ) / 1000.0
        cost_score = self.cost_per_1k() * 100
        error_penalty = s.error_rate * 500

        if strategy == "latency":
            return latency_score + error_penalty
        if strategy == "cost":
            return cost_score + error_penalty
        # balanced (default)
        return (latency_score * 0.5) + (cost_score * 0.5) + error_penalty


class ProviderRegistry:
    """Registry of configured :class:`Provider` instances."""

    def __init__(self) -> None:
        self._providers: Dict[str, Provider] = {}
        self._stats: Dict[str, ProviderStats] = {}

    def register(self, provider: Provider, name: str | None = None) -> None:
        """Register a provider, optionally under an explicit key.

        When *name* is given (e.g. a source name from ``sources.json``), the
        provider is stored under that key so ``get(source_name)`` finds it.
        Without *name*, the provider's own ``name`` property is used.
        """
        key = name or provider.name
        self._providers[key] = provider
        self._stats.setdefault(key, ProviderStats())

    def get(self, name: str) -> Optional[Provider]:
        return self._providers.get(name)

    def all(self) -> List[Provider]:
        return list(self._providers.values())

    def stats_for(self, name: str) -> ProviderStats:
        return self._stats.setdefault(name, ProviderStats())

    def record_success(self, name: str, latency_ms: float) -> None:
        s = self.stats_for(name)
        s.total_requests += 1
        s.last_success_time = time.time()
        # Exponential moving average for latency
        alpha = 0.3
        if s.avg_latency_ms >= 9999:
            s.avg_latency_ms = latency_ms
        else:
            s.avg_latency_ms = alpha * latency_ms + (1 - alpha) * s.avg_latency_ms

    def record_error(self, name: str, error: str) -> None:
        s = self.stats_for(name)
        s.total_requests += 1
        s.total_errors += 1
        s.last_error = error

    def best(self, strategy: str = "balanced") -> Optional[Provider]:
        """Return the lowest-scoring (best) available provider."""
        scored = [
            (p, p.score(strategy, self._stats.get(key))) for key, p in self._providers.items()
        ]
        scored.sort(key=lambda x: x[1])
        return scored[0][0] if scored and scored[0][1] < float("inf") else None

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._providers.values())

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._providers
