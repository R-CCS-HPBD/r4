"""Smart provider router for the R4 platform.

Selects the best provider for each request based on a scoring function
that weighs latency, cost, error rate, and model capabilities.

Inspired by latency/cost-aware provider routing patterns.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Optional

from ..providers import (
    Provider,
    ProviderRegistry,
)

logger = logging.getLogger(__name__)


class RoutingStrategy:
    """Strategy constants for the smart router."""

    LATENCY = "latency"
    COST = "cost"
    BALANCED = "balanced"
    LOCAL_FIRST = "local_first"


class SmartRouter:
    """Routes requests to the best available provider.

    The router checks provider health, scores candidates, and dispatches
    to the winner. On failure it falls back to the next-best provider.

    Args:
        registry: Configured :class:`ProviderRegistry`.
        strategy: Default routing strategy.
        max_retries: How many providers to try before giving up.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        strategy: str = RoutingStrategy.BALANCED,
        max_retries: int = 2,
    ):
        self._registry = registry
        self._strategy = strategy
        self._max_retries = max_retries

    def _rank(self, strategy: Optional[str] = None) -> List[Provider]:
        """Rank providers by score (lower is better)."""
        strat = strategy or self._strategy
        candidates = []

        for provider in self._registry:
            stats = self._registry.stats_for(provider.name)
            score = provider.score(strat, stats)

            # Local-first bias
            if strat == RoutingStrategy.LOCAL_FIRST and provider.is_local:
                score *= 0.1  # heavy preference

            candidates.append((provider, score))

        candidates.sort(key=lambda x: x[1])
        return [p for p, _ in candidates if _ < float("inf")]

    def select(
        self,
        *,
        strategy: Optional[str] = None,
        require_tool_calling: bool = False,
        require_vision: bool = False,
        require_streaming: bool = False,
    ) -> Optional[Provider]:
        """Select the best provider matching the given requirements."""
        ranked = self._rank(strategy)
        for provider in ranked:
            caps = provider.model_capabilities()
            if require_tool_calling and not caps.supports_tool_calling:
                continue
            if require_vision and not caps.supports_vision:
                continue
            if require_streaming and not caps.supports_streaming:
                continue
            return provider
        return None

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        strategy: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Route a chat request to the best provider, with fallback."""
        ranked = self._rank(strategy)
        last_error: Optional[Exception] = None

        for provider in ranked[: self._max_retries + 1]:
            t0 = time.time()
            try:
                result = provider.chat(messages, model=model, **kwargs)
                latency = (time.time() - t0) * 1000
                self._registry.record_success(provider.name, latency)
                logger.info("Routed to %s (%.0fms)", provider.name, latency)
                return result
            except Exception as exc:
                latency = (time.time() - t0) * 1000
                self._registry.record_error(provider.name, str(exc))
                logger.warning(
                    "Provider %s failed (%.0fms): %s",
                    provider.name,
                    latency,
                    exc,
                )
                last_error = exc

        if last_error:
            raise last_error
        raise RuntimeError("No providers available")

    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        strategy: Optional[str] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Route a streaming chat request with fallback."""
        ranked = self._rank(strategy)
        last_error: Optional[Exception] = None

        for provider in ranked[: self._max_retries + 1]:
            t0 = time.time()
            try:
                chunks = provider.stream(messages, model=model, **kwargs)
                # We can't easily measure full latency for streams,
                # so record first-chunk latency.
                first = True
                for chunk in chunks:
                    if first:
                        latency = (time.time() - t0) * 1000
                        self._registry.record_success(provider.name, latency)
                        first = True
                    yield chunk
                return
            except Exception as exc:
                latency = (time.time() - t0) * 1000
                self._registry.record_error(provider.name, str(exc))
                logger.warning(
                    "Provider %s stream failed (%.0fms): %s",
                    provider.name,
                    latency,
                    exc,
                )
                last_error = exc

        if last_error:
            raise last_error
        raise RuntimeError("No providers available")
