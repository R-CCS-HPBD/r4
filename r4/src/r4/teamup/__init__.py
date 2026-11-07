"""
Multi-Agent Teamup Module (A-801 to A-815)

Enables multi-agent collaboration for complex HPC optimization tasks.
Integrates with VibeCodeHPC for multi-agent workflows.

ADR-092 slice 4: this package is the HPC preset (the ``hpc`` crew) only.
Every other crew is a plan template (``r4.teams.crews``).

ref: https://github.com/Katagiri-Hoshino-Lab/VibeCodeHPC

Architecture:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         Teamup Session                                  │
    │                                                                         │
    │  ┌─────────┐    file-based    ┌─────────┐    file-based    ┌─────────┐ │
    │  │   PM    │◄────────────────►│   SE    │◄────────────────►│   PG    │ │
    │  │ Manager │   communication  │ Monitor │   communication  │ Coder   │ │
    │  └────┬────┘                  └────┬────┘                  └────┬────┘ │
    │       │                            │                            │      │
    │       │         ┌──────────────────┴──────────────────┐         │      │
    │       │         │        Agent-shared/                │         │      │
    │       │         │  • CLAUDE.md (rules)                │         │      │
    │       │         │  • directory_pane_map.md            │         │      │
    │       │         │  • sota_local.txt                   │         │      │
    │       └─────────┤  • ChangeLog.md                     ├─────────┘      │
    │                 └─────────────────────────────────────┘                 │
    │                                    │                                    │
    │                    ┌───────────────▼───────────────┐                    │
    │                    │       Mini-RiVault LLM        │                    │
    │                    │    (All agents use same LLM)  │                    │
    │                    └───────────────────────────────┘                    │
    └─────────────────────────────────────────────────────────────────────────┘
"""

from .comms import AgentCommunication, ChangeLogEntry
from .hardware import (
    FUGAKU_PROFILE,
    GENERIC_CPU_PROFILE,
    HARDWARE_PROFILES,
    NVIDIA_A100_PROFILE,
    HardwareTarget,
    get_hardware_profile,
    list_hardware_profiles,
    load_profile_from_file,
)
from .roles import CD_SPEC, PG_SPEC, PM_SPEC, SE_SPEC, AgentKey, AgentRole, AgentRoleSpec
from .session import TeamupSession, TeamupSessionConfig
from .sota import SOTAEntry, SOTALevel, SOTATracker

# Alias for CLI convenience
TeamupConfig = TeamupSessionConfig

__all__ = [
    # Session management
    "TeamupSession",
    "TeamupSessionConfig",
    "TeamupConfig",  # Alias for TeamupSessionConfig
    # Agent roles
    "AgentRole",
    "AgentKey",
    "AgentRoleSpec",
    "PM_SPEC",
    "SE_SPEC",
    "PG_SPEC",
    "CD_SPEC",
    # Communication
    "AgentCommunication",
    "ChangeLogEntry",
    # Hardware
    "HardwareTarget",
    "FUGAKU_PROFILE",
    "NVIDIA_A100_PROFILE",
    "GENERIC_CPU_PROFILE",
    "HARDWARE_PROFILES",
    "get_hardware_profile",
    "load_profile_from_file",
    "list_hardware_profiles",
    # SOTA tracking
    "SOTATracker",
    "SOTALevel",
    "SOTAEntry",
]
