"""Provider factory — builds Provider instances from Source configs.

Central integration point that maps each r4 Source to the appropriate
Provider implementation, ensuring local/public/HPC endpoints are
treated equally through the Provider abstraction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from . import Provider, ProviderRegistry

if TYPE_CHECKING:
    from ..source import Source

logger = logging.getLogger(__name__)


def provider_from_source(source: "Source") -> Optional[Provider]:
    """Create a Provider instance from a Source configuration.

    Returns ``None`` when the source cannot be mapped to a known provider
    (e.g. missing URL or unsupported provider string).
    """
    if source.kind == "platform":
        from .litellm import LiteLLMProvider

        return LiteLLMProvider(
            gateway_url=source.url,
            api_key=source.api_key,
        )

    # Public sources
    provider_type = (source.provider or "").lower()

    if provider_type == "bedrock":
        from .bedrock import BedrockProvider

        return BedrockProvider(
            region=source.aws_region,
            profile_name=source.aws_profile,
            api_key=source.api_key,
        )

    if provider_type == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(base_url=source.url)

    # Generic OpenAI-compatible (including provider=None, "anthropic", etc.)
    if source.url:
        from .openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            provider_name=provider_type or source.name,
            base_url=source.url,
            api_key=source.api_key,
        )

    return None


def _detect_local_ollama() -> Optional[Provider]:
    """Auto-detect a locally running Ollama instance."""
    import urllib.request

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
    except Exception:
        return None

    from .ollama import OllamaProvider

    return OllamaProvider(base_url="http://localhost:11434")


def build_registry(config=None) -> ProviderRegistry:
    """Build a ProviderRegistry from all configured sources.

    Steps:
    1. Iterate registered sources, create a Provider for each.
    2. Auto-detect local Ollama if no explicit ollama source exists.
    3. Return populated registry.
    """
    registry = ProviderRegistry()
    seen_names: set[str] = set()
    has_ollama = False

    try:
        from ..source import get_source_store

        for source in get_source_store().list():
            provider = provider_from_source(source)
            if provider is None:
                continue
            # Use source name as registry key to avoid collisions
            # between multiple sources of the same provider type.
            key = source.name
            if key in seen_names:
                continue
            seen_names.add(key)
            registry.register(provider, name=key)
            if (source.provider or "").lower() == "ollama":
                has_ollama = True
    except Exception:
        logger.debug("Source store unavailable; skipping source-based providers")

    # Auto-detect local Ollama if not explicitly configured
    if not has_ollama:
        ollama = _detect_local_ollama()
        if ollama is not None:
            registry.register(ollama)

    return registry
