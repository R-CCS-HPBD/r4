"""SPI error types (ADR-020)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    """One problem found while validating a TeamSpec against an engine."""

    field: str
    message: str
    severity: str = "error"  # error | warning


class TeamEngineError(Exception):
    """Base class for all Team Engine SPI errors."""


class SpecNotExpressible(TeamEngineError):
    """compile() could not lower the TeamSpec into native form."""


class IncompatibleCheckpoint(TeamEngineError):
    """Checkpoint blob belongs to a different engine or version."""


class BudgetExhausted(TeamEngineError):
    """A BudgetMeter ceiling was hit; the run must stop."""


class EngineCrashed(TeamEngineError):
    """The engine-side execution died unexpectedly."""


class PluginTrustError(TeamEngineError):
    """Plugin failed trust policy (disabled, tier too low, unsigned)."""


class IncompatibleSpiVersion(TeamEngineError):
    """Adapter targets an SPI version outside the supported window."""


class UnknownEngineError(TeamEngineError):
    """No installed adapter provides the requested engine name."""
