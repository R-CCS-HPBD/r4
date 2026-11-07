"""
Teamup Hardware Profiles (A-805)

HPC hardware target definitions for optimization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Built-in hardware profiles directory
PROFILES_DIR = Path(__file__).parent / "profiles"


class ComputeType(Enum):
    """Type of compute resource."""

    CPU = "cpu"
    GPU = "gpu"
    ACCELERATOR = "accelerator"
    HYBRID = "hybrid"


class MemoryType(Enum):
    """Type of memory."""

    DDR4 = "ddr4"
    DDR5 = "ddr5"
    HBM2 = "hbm2"
    HBM2E = "hbm2e"
    HBM3 = "hbm3"


class Interconnect(Enum):
    """Network interconnect type."""

    ETHERNET = "ethernet"
    INFINIBAND_HDR = "infiniband_hdr"
    INFINIBAND_NDR = "infiniband_ndr"
    TOFU_D = "tofu_d"  # Fugaku
    NVLINK = "nvlink"


@dataclass
class ComputeCapability:
    """Compute capability of a device."""

    device_type: ComputeType
    name: str
    peak_flops: float  # TFLOPS (FP64)
    peak_flops_fp32: float  # TFLOPS (FP32)
    peak_flops_fp16: float  # TFLOPS (FP16/BF16)
    cores: int
    vector_width: int = 512  # bits
    simd_lanes: int = 16
    clock_mhz: int = 2000
    arch: str = "generic"
    compiler_flags: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "device_type": self.device_type.value,
            "name": self.name,
            "peak_flops": self.peak_flops,
            "peak_flops_fp32": self.peak_flops_fp32,
            "peak_flops_fp16": self.peak_flops_fp16,
            "cores": self.cores,
            "vector_width": self.vector_width,
            "simd_lanes": self.simd_lanes,
            "clock_mhz": self.clock_mhz,
            "arch": self.arch,
            "compiler_flags": self.compiler_flags,
            "libraries": self.libraries,
        }


@dataclass
class MemorySubsystem:
    """Memory subsystem characteristics."""

    type: MemoryType
    capacity_gb: float
    bandwidth_gbps: float
    latency_ns: float = 100.0
    channels: int = 8
    ecc: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "capacity_gb": self.capacity_gb,
            "bandwidth_gbps": self.bandwidth_gbps,
            "latency_ns": self.latency_ns,
            "channels": self.channels,
            "ecc": self.ecc,
        }


@dataclass
class NetworkSubsystem:
    """Network interconnect characteristics."""

    type: Interconnect
    bandwidth_gbps: float
    latency_us: float
    topology: str = "fat-tree"
    mpi_impl: str = "openmpi"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "bandwidth_gbps": self.bandwidth_gbps,
            "latency_us": self.latency_us,
            "topology": self.topology,
            "mpi_impl": self.mpi_impl,
        }


@dataclass
class HardwareTarget:
    """
    Complete hardware target profile (A-805).

    Defines the characteristics of an HPC system for optimization targeting.
    """

    id: str
    name: str
    description: str
    compute: ComputeCapability
    memory: MemorySubsystem
    network: Optional[NetworkSubsystem] = None

    # Node configuration
    nodes_min: int = 1
    nodes_max: int = 1000
    gpus_per_node: int = 0
    cpus_per_node: int = 48

    # Software environment
    os: str = "linux"
    compiler: str = "gcc"
    compiler_version: str = "11.0"
    mpi: str = "openmpi"
    mpi_version: str = "4.1"
    container_runtime: str = "singularity"

    # Optimization hints
    preferred_parallelism: str = "mpi+openmp"
    recommended_block_size: int = 64
    cache_line_bytes: int = 64
    numa_domains: int = 2
    vectorization_hints: list[str] = field(default_factory=list)
    optimization_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "compute": self.compute.to_dict(),
            "memory": self.memory.to_dict(),
            "network": self.network.to_dict() if self.network else None,
            "nodes_min": self.nodes_min,
            "nodes_max": self.nodes_max,
            "gpus_per_node": self.gpus_per_node,
            "cpus_per_node": self.cpus_per_node,
            "os": self.os,
            "compiler": self.compiler,
            "compiler_version": self.compiler_version,
            "mpi": self.mpi,
            "mpi_version": self.mpi_version,
            "container_runtime": self.container_runtime,
            "preferred_parallelism": self.preferred_parallelism,
            "recommended_block_size": self.recommended_block_size,
            "cache_line_bytes": self.cache_line_bytes,
            "numa_domains": self.numa_domains,
            "vectorization_hints": self.vectorization_hints,
            "optimization_notes": self.optimization_notes,
        }

    def get_optimization_context(self) -> str:
        """
        Get optimization context for LLM.

        Returns description suitable for including in agent prompts.
        """
        return f"""## Target Hardware: {self.name}

### Compute
- **Type**: {self.compute.device_type.value}
- **Architecture**: {self.compute.arch}
- **Peak FP64**: {self.compute.peak_flops:.2f} TFLOPS
- **Peak FP32**: {self.compute.peak_flops_fp32:.2f} TFLOPS
- **Cores**: {self.compute.cores}
- **Vector Width**: {self.compute.vector_width} bits
- **SIMD Lanes**: {self.compute.simd_lanes}

### Memory
- **Type**: {self.memory.type.value}
- **Capacity**: {self.memory.capacity_gb:.1f} GB
- **Bandwidth**: {self.memory.bandwidth_gbps:.1f} GB/s

### Network
{self._format_network()}

### Configuration
- Nodes: {self.nodes_min} - {self.nodes_max}
- CPUs per node: {self.cpus_per_node}
- GPUs per node: {self.gpus_per_node}
- Preferred parallelism: {self.preferred_parallelism}

### Compiler
- {self.compiler} {self.compiler_version}
- Flags: {", ".join(self.compute.compiler_flags) or "default"}
- Libraries: {", ".join(self.compute.libraries) or "standard"}

### Optimization Notes
{self.optimization_notes or "No specific notes."}
"""

    def _format_network(self) -> str:
        """Format network info."""
        if not self.network:
            return "- No network (single node)"
        return f"""- **Type**: {self.network.type.value}
- **Bandwidth**: {self.network.bandwidth_gbps:.1f} Gbps
- **Latency**: {self.network.latency_us:.2f} µs
- **Topology**: {self.network.topology}
- **MPI**: {self.network.mpi_impl}"""


# Pre-defined hardware profiles

FUGAKU_PROFILE = HardwareTarget(
    id="fugaku",
    name="Fugaku (A64FX)",
    description="RIKEN Fugaku supercomputer with A64FX ARM-based processors",
    compute=ComputeCapability(
        device_type=ComputeType.CPU,
        name="A64FX",
        peak_flops=3.0,  # TFLOPS FP64 per node
        peak_flops_fp32=6.0,
        peak_flops_fp16=12.0,
        cores=48,
        vector_width=512,  # SVE
        simd_lanes=8,
        clock_mhz=2200,
        arch="aarch64+sve",
        compiler_flags=["-Kfast", "-Nfjomplib", "-Kzfill", "-Kprefetch_infer"],
        libraries=["FUJITSU_SSL", "FFT_fujitsu"],
    ),
    memory=MemorySubsystem(
        type=MemoryType.HBM2,
        capacity_gb=32.0,
        bandwidth_gbps=1024.0,  # 1 TB/s
        latency_ns=90.0,
        channels=4,
        ecc=True,
    ),
    network=NetworkSubsystem(
        type=Interconnect.TOFU_D,
        bandwidth_gbps=40.8,  # 6D mesh/torus
        latency_us=0.5,
        topology="6D_mesh_torus",
        mpi_impl="fujitsu_mpi",
    ),
    nodes_max=158976,
    gpus_per_node=0,
    cpus_per_node=48,
    os="linux",
    compiler="fcc",
    compiler_version="4.x",
    mpi="fujitsu_mpi",
    container_runtime="singularity",
    preferred_parallelism="mpi+openmp",
    recommended_block_size=256,  # Cache-optimized
    cache_line_bytes=256,
    numa_domains=4,  # CMGs
    vectorization_hints=[
        "Use SVE intrinsics for critical loops",
        "Align data to 256-byte boundaries",
        "Use sector cache for streaming access",
        "Consider CMG-aware thread placement",
    ],
    optimization_notes="""
Fugaku uses ARM A64FX with 512-bit SVE vector units.
Key optimizations:
- Use -Kfast for aggressive Fujitsu compiler optimizations
- Leverage HBM2 bandwidth (1 TB/s per node)
- Use Tofu-D 6D mesh for collective operations
- Cache Memory Groups (CMGs) require NUMA-aware allocation
- Sector cache mode for streaming workloads
""",
)

NVIDIA_A100_PROFILE = HardwareTarget(
    id="nvidia_a100",
    name="NVIDIA A100 GPU",
    description="NVIDIA A100 80GB GPU cluster",
    compute=ComputeCapability(
        device_type=ComputeType.GPU,
        name="A100-80GB",
        peak_flops=9.7,  # TFLOPS FP64
        peak_flops_fp32=19.5,
        peak_flops_fp16=312.0,  # Tensor Cores
        cores=6912,  # CUDA cores
        vector_width=32,  # Warp size
        simd_lanes=32,
        clock_mhz=1410,
        arch="sm_80",
        compiler_flags=["-arch=sm_80", "-O3", "-use_fast_math"],
        libraries=["cublas", "cudnn", "nccl", "cutlass"],
    ),
    memory=MemorySubsystem(
        type=MemoryType.HBM2E,
        capacity_gb=80.0,
        bandwidth_gbps=2039.0,  # 2 TB/s
        latency_ns=50.0,
        channels=5,
        ecc=True,
    ),
    network=NetworkSubsystem(
        type=Interconnect.NVLINK,
        bandwidth_gbps=600.0,  # NVLink 3.0
        latency_us=1.0,
        topology="nvswitch",
        mpi_impl="openmpi",
    ),
    nodes_max=1000,
    gpus_per_node=8,
    cpus_per_node=128,
    os="linux",
    compiler="nvcc",
    compiler_version="12.0",
    mpi="openmpi",
    mpi_version="4.1",
    container_runtime="singularity",
    preferred_parallelism="cuda+mpi",
    recommended_block_size=256,
    cache_line_bytes=128,
    numa_domains=2,
    vectorization_hints=[
        "Use cuBLAS/cuDNN for dense operations",
        "Use Tensor Cores for FP16/TF32 operations",
        "Maximize GPU occupancy",
        "Use CUDA Graphs for kernel launch overhead",
    ],
    optimization_notes="""
NVIDIA A100 with 80GB HBM2e memory.
Key optimizations:
- Use Tensor Cores for mixed-precision (FP16, TF32, INT8)
- NVLink for fast GPU-to-GPU communication
- CUDA streams for overlapping compute and data transfer
- Target 70%+ occupancy for memory-bound kernels
- Use cuBLAS/cuDNN/CUTLASS for optimized implementations
""",
)

GENERIC_CPU_PROFILE = HardwareTarget(
    id="generic",
    name="Generic x86-64 CPU",
    description="Generic x86-64 CPU cluster with AVX-512",
    compute=ComputeCapability(
        device_type=ComputeType.CPU,
        name="Generic x86-64",
        peak_flops=0.5,  # TFLOPS FP64
        peak_flops_fp32=1.0,
        peak_flops_fp16=2.0,
        cores=64,
        vector_width=512,  # AVX-512
        simd_lanes=8,
        clock_mhz=2500,
        arch="x86_64+avx512",
        compiler_flags=["-O3", "-march=native", "-ffast-math", "-fopenmp"],
        libraries=["openblas", "fftw3"],
    ),
    memory=MemorySubsystem(
        type=MemoryType.DDR4,
        capacity_gb=256.0,
        bandwidth_gbps=200.0,
        latency_ns=100.0,
        channels=8,
        ecc=True,
    ),
    network=NetworkSubsystem(
        type=Interconnect.INFINIBAND_HDR,
        bandwidth_gbps=200.0,
        latency_us=1.0,
        topology="fat-tree",
        mpi_impl="openmpi",
    ),
    nodes_max=100,
    gpus_per_node=0,
    cpus_per_node=64,
    os="linux",
    compiler="gcc",
    compiler_version="11.0",
    mpi="openmpi",
    mpi_version="4.1",
    container_runtime="singularity",
    preferred_parallelism="mpi+openmp",
    recommended_block_size=64,
    cache_line_bytes=64,
    numa_domains=2,
    vectorization_hints=[
        "Use AVX-512 for vectorized loops",
        "Align data to 64-byte boundaries",
        "Use OpenMP for thread parallelism",
        "Consider NUMA-aware memory allocation",
    ],
    optimization_notes="""
Generic x86-64 cluster with AVX-512 support.
Key optimizations:
- Use -march=native for auto-vectorization
- OpenMP for shared-memory parallelism
- MPI for distributed memory
- OpenBLAS/MKL for dense linear algebra
""",
)

# Registry of built-in profiles
HARDWARE_PROFILES: dict[str, HardwareTarget] = {
    "fugaku": FUGAKU_PROFILE,
    "nvidia_a100": NVIDIA_A100_PROFILE,
    "generic": GENERIC_CPU_PROFILE,
}


def get_hardware_profile(name: str) -> Optional[HardwareTarget]:
    """
    Get a hardware profile by name.

    Args:
        name: Profile name (fugaku, nvidia_a100, generic, or custom path)

    Returns:
        Hardware profile or None if not found
    """
    # Check built-in profiles
    if name.lower() in HARDWARE_PROFILES:
        return HARDWARE_PROFILES[name.lower()]

    # Check for custom profile file
    profile_path = Path(name)
    if profile_path.exists() and profile_path.suffix == ".json":
        return load_profile_from_file(profile_path)

    return None


def load_profile_from_file(path: Path) -> Optional[HardwareTarget]:
    """Load a hardware profile from a JSON file."""
    try:
        with open(path) as f:
            data = json.load(f)
        return _deserialize_profile(data)
    except Exception as e:
        import logging

        logging.warning(f"Failed to load hardware profile from {path}: {e}")
        return None


def _deserialize_profile(data: dict[str, Any]) -> HardwareTarget:
    """Deserialize a hardware profile from dictionary."""
    compute_data = data["compute"]
    compute = ComputeCapability(
        device_type=ComputeType(compute_data["device_type"]),
        name=compute_data["name"],
        peak_flops=compute_data["peak_flops"],
        peak_flops_fp32=compute_data["peak_flops_fp32"],
        peak_flops_fp16=compute_data["peak_flops_fp16"],
        cores=compute_data["cores"],
        vector_width=compute_data.get("vector_width", 512),
        simd_lanes=compute_data.get("simd_lanes", 16),
        clock_mhz=compute_data.get("clock_mhz", 2000),
        arch=compute_data.get("arch", "generic"),
        compiler_flags=compute_data.get("compiler_flags", []),
        libraries=compute_data.get("libraries", []),
    )

    memory_data = data["memory"]
    memory = MemorySubsystem(
        type=MemoryType(memory_data["type"]),
        capacity_gb=memory_data["capacity_gb"],
        bandwidth_gbps=memory_data["bandwidth_gbps"],
        latency_ns=memory_data.get("latency_ns", 100.0),
        channels=memory_data.get("channels", 8),
        ecc=memory_data.get("ecc", True),
    )

    network = None
    if data.get("network"):
        net_data = data["network"]
        network = NetworkSubsystem(
            type=Interconnect(net_data["type"]),
            bandwidth_gbps=net_data["bandwidth_gbps"],
            latency_us=net_data["latency_us"],
            topology=net_data.get("topology", "fat-tree"),
            mpi_impl=net_data.get("mpi_impl", "openmpi"),
        )

    return HardwareTarget(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        compute=compute,
        memory=memory,
        network=network,
        nodes_min=data.get("nodes_min", 1),
        nodes_max=data.get("nodes_max", 1000),
        gpus_per_node=data.get("gpus_per_node", 0),
        cpus_per_node=data.get("cpus_per_node", 48),
        os=data.get("os", "linux"),
        compiler=data.get("compiler", "gcc"),
        compiler_version=data.get("compiler_version", "11.0"),
        mpi=data.get("mpi", "openmpi"),
        mpi_version=data.get("mpi_version", "4.1"),
        container_runtime=data.get("container_runtime", "singularity"),
        preferred_parallelism=data.get("preferred_parallelism", "mpi+openmp"),
        recommended_block_size=data.get("recommended_block_size", 64),
        cache_line_bytes=data.get("cache_line_bytes", 64),
        numa_domains=data.get("numa_domains", 2),
        vectorization_hints=data.get("vectorization_hints", []),
        optimization_notes=data.get("optimization_notes", ""),
    )


def list_hardware_profiles() -> list[dict[str, str]]:
    """
    List available hardware profiles.

    Returns:
        List of profile info dicts with id, name, description
    """
    profiles = []
    for profile_id, profile in HARDWARE_PROFILES.items():
        profiles.append(
            {
                "id": profile_id,
                "name": profile.name,
                "description": profile.description,
            }
        )
    return profiles
