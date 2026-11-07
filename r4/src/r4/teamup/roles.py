"""
Agent Role Definitions (A-802): the HPC optimisation preset.

Defines the roles for multi-agent collaboration following VibeCodeHPC patterns.

Roles:
    PM  - Project Manager: Coordination and oversight
    SE  - System Engineer: Monitoring and performance analysis
    PG  - Programmer: Code optimization and implementation
    CD  - Code Doctor: Code review and quality assurance (optional)

ADR-092 slice 4 (MD-8): ``AgentRole`` is FROZEN to these four, the HPC
preset (the ``hpc`` crew). No crew re-skins them any more: every other crew
is a plan template whose agents are named by their prompts (``r4.teams.crews``).
A new team shape is a template, never a new member here.

Persisted role keys (teamup ``session.json``, ChangeLog agents, team events)
are read through :class:`AgentKey`, which accepts every spelling a snapshot
may hold, so old sessions keep loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRole(Enum):
    """The HPC preset's four roles (frozen, ADR-092 slice 4)."""

    PM = "pm"  # Project Manager
    SE = "se"  # System Engineer (Monitor)
    PG = "pg"  # Programmer
    CD = "cd"  # Code Doctor (optional)


class AgentKey(str):
    """A team agent's key as persisted: the compatibility read (ADR-092 slice 4).

    Normalised to the lower-case value a snapshot writes (``"pm"``). Reads
    the value, the member name (``"PM"``), the enum's ``str()``
    (``"AgentRole.PM"``), an ``AgentRole`` itself, and stray whitespace. It
    is a ``str``, so it compares and hashes as the stored key does, and
    ``.value`` reads like ``AgentRole.value``. ``.role`` is the HPC preset
    role it names, or None for a key outside the preset, which a reader
    skips with a warning instead of failing the whole load.
    """

    def __new__(cls, raw: Any) -> AgentKey:
        text = raw.value if isinstance(raw, AgentRole) else str(raw if raw is not None else "")
        text = text.strip()
        if text.startswith(f"{AgentRole.__name__}."):
            text = text[len(AgentRole.__name__) + 1 :]
        return super().__new__(cls, text.lower())

    @property
    def value(self) -> str:
        return str(self)

    @property
    def role(self) -> AgentRole | None:
        return _ROLE_BY_VALUE.get(str(self))


_ROLE_BY_VALUE = {role.value: role for role in AgentRole}


@dataclass
class AgentRoleSpec:
    """Specification for an agent role."""

    role: AgentRole
    name: str
    description: str
    system_prompt: str
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    can_execute: bool = False  # Can execute code on HPC?
    can_submit_jobs: bool = False  # Can submit Slurm/HPC jobs?
    priority: int = 0  # Higher = higher priority in resource allocation

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "role": self.role.value,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "can_execute": self.can_execute,
            "can_submit_jobs": self.can_submit_jobs,
            "priority": self.priority,
        }


# ============================================================================
# Default Role Specifications
# ============================================================================

PM_SPEC = AgentRoleSpec(
    role=AgentRole.PM,
    name="Project Manager",
    description="Coordinates multi-agent optimization workflow and tracks progress",
    system_prompt="""You are the Project Manager (PM) for an HPC code optimization team.

Your responsibilities:
1. **Coordinate** the optimization workflow across all agents
2. **Track** progress toward performance goals
3. **Allocate** compute budget across optimization attempts
4. **Report** status and SOTA (State-of-the-Art) updates
5. **Decide** when to stop optimization (goal reached or budget exhausted)

Communication:
- Read ChangeLog.md and sota_*.txt files for updates from other agents
- Write decisions and directives to your ChangeLog section
- Track overall project SOTA in sota_project.txt

Budget Management:
- Monitor remaining compute budget
- Approve or reject expensive optimization attempts
- Prioritize promising optimization directions

Do NOT:
- Write or modify code directly
- Submit jobs to HPC
- Make technical implementation decisions (delegate to PG)
""",
    capabilities=[
        "coordination",
        "planning",
        "budget_allocation",
        "progress_tracking",
        "decision_making",
    ],
    tools=[
        "read_changelog",
        "write_changelog",
        "read_sota",
        "update_budget",
        "generate_report",
    ],
    can_execute=False,
    can_submit_jobs=False,
    priority=10,
)

SE_SPEC = AgentRoleSpec(
    role=AgentRole.SE,
    name="System Engineer",
    description="Monitors performance, analyzes results, and identifies bottlenecks",
    system_prompt="""You are the System Engineer (SE) / Monitor for an HPC code optimization team.

Your responsibilities:
1. **Monitor** job execution and resource utilization
2. **Analyze** performance results from benchmark runs
3. **Identify** bottlenecks (memory, compute, communication)
4. **Compare** results against theoretical peak performance
5. **Update** SOTA records when new bests are achieved

Analysis Focus:
- FLOPS, bandwidth, cache hit rates
- MPI communication patterns
- OpenMP thread efficiency
- GPU utilization and memory bandwidth

Communication:
- Read job outputs and log files
- Update sota_hardware.txt with performance records
- Report findings to PM and PG via ChangeLog.md

Do NOT:
- Write optimization code (delegate to PG)
- Make project-level decisions (delegate to PM)
""",
    capabilities=[
        "performance_analysis",
        "bottleneck_detection",
        "benchmarking",
        "monitoring",
        "sota_tracking",
    ],
    tools=[
        "read_job_logs",
        "parse_benchmark",
        "analyze_perf",
        "update_sota",
        "read_changelog",
        "write_changelog",
    ],
    can_execute=False,
    can_submit_jobs=False,
    priority=5,
)

PG_SPEC = AgentRoleSpec(
    role=AgentRole.PG,
    name="Programmer",
    description="Implements code optimizations and evolutionary improvements",
    system_prompt="""You are the Programmer (PG) for an HPC code optimization team.

Your responsibilities:
1. **Implement** code optimizations following PM directives
2. **Apply** optimization techniques (OpenMP, MPI, SIMD, GPU)
3. **Evolve** code by exploring optimization variants
4. **Submit** jobs to HPC for benchmarking
5. **Track** local SOTA for your optimization branch

Optimization Techniques:
- Loop optimization (unrolling, tiling, blocking)
- Parallelization (OpenMP, MPI)
- Vectorization (AVX, NEON, SVE)
- GPU offloading (CUDA, OpenACC)
- Memory optimization (cache blocking, prefetching)

Evolutionary Workflow:
1. Start from current best version
2. Create variant with specific optimization
3. Submit benchmark job
4. Record results in sota_local.txt
5. If improvement, report to SE/PM

Communication:
- Read PM directives from ChangeLog.md
- Write implementations and results to ChangeLog.md
- Maintain sota_local.txt for your optimization branch

Code Organization:
- Each optimization variant in separate directory
- Clear naming: technique_parameter (e.g., tile_64, unroll_4)
""",
    capabilities=[
        "code_optimization",
        "parallelization",
        "vectorization",
        "gpu_offloading",
        "benchmarking",
        "job_submission",
    ],
    tools=[
        "write_code",
        "read_code",
        "submit_job",
        "read_job_status",
        "update_sota_local",
        "read_changelog",
        "write_changelog",
    ],
    can_execute=True,
    can_submit_jobs=True,
    priority=3,
)

CD_SPEC = AgentRoleSpec(
    role=AgentRole.CD,
    name="Code Doctor",
    description="Reviews code quality, correctness, and maintainability",
    system_prompt="""You are the Code Doctor (CD) for an HPC code optimization team.

Your responsibilities:
1. **Review** code changes for correctness
2. **Validate** optimization doesn't break functionality
3. **Check** code quality and maintainability
4. **Verify** numerical accuracy is preserved
5. **Suggest** improvements for code clarity

Review Focus:
- Correctness: Does the optimization preserve semantics?
- Race conditions: Are parallel accesses safe?
- Memory safety: No buffer overflows or leaks?
- Numerical stability: Is precision maintained?
- Portability: Will it work on target architecture?

Communication:
- Review code when PG requests
- Report issues to PG via ChangeLog.md
- Approve optimizations before benchmark submission

Do NOT:
- Implement optimizations (delegate to PG)
- Submit jobs to HPC
""",
    capabilities=[
        "code_review",
        "correctness_validation",
        "race_condition_detection",
        "numerical_analysis",
    ],
    tools=[
        "read_code",
        "static_analysis",
        "review_diff",
        "read_changelog",
        "write_changelog",
    ],
    can_execute=False,
    can_submit_jobs=False,
    priority=1,
)

# Role registry
ROLE_SPECS: dict[AgentRole, AgentRoleSpec] = {
    AgentRole.PM: PM_SPEC,
    AgentRole.SE: SE_SPEC,
    AgentRole.PG: PG_SPEC,
    AgentRole.CD: CD_SPEC,
}


def get_role_spec(role: AgentRole | str) -> AgentRoleSpec:
    """Get role specification by role (any spelling :class:`AgentKey` reads)."""
    resolved = AgentKey(role).role
    if resolved is None:
        raise ValueError(f"{role!r} is not a role of the HPC preset (pm, se, pg, cd)")
    return ROLE_SPECS[resolved]


def get_default_roles() -> list[AgentRole]:
    """Get default roles for a teamup session."""
    return [AgentRole.PM, AgentRole.SE, AgentRole.PG]
