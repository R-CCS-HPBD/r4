"""SPI version and compatibility window (ADR-020).

Pre-1.0 semver: ``0.MINOR.PATCH`` — MINOR bumps may break the SPI, PATCH
must not. Compatibility rule for 0.x: same MAJOR and same MINOR.
"""

from __future__ import annotations

SPI_VERSION = "0.1"


def _parse(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(f"invalid SPI version: {version!r}")
    major = int(parts[0])
    minor = int(parts[1])
    patch = int(parts[2]) if len(parts) == 3 else 0
    return major, minor, patch


def is_compatible(spi_version: str) -> bool:
    """True if an adapter built against ``spi_version`` runs on this SPI."""
    adapter_major, adapter_minor, _ = _parse(spi_version)
    spi_major, spi_minor, _ = _parse(SPI_VERSION)
    if adapter_major != spi_major:
        return False
    if adapter_major == 0:
        return adapter_minor == spi_minor
    return adapter_minor <= spi_minor
