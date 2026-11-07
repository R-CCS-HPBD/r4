"""Profile manifest generator (COR-001).

Reads the runtime support matrix and generates per-profile deployment
manifests including:
- Environment variable templates
- Smoke command references
- Pre-deployment checklists
- Rollback procedures
- Service dependency lists
- Readiness gate references

Usage:
    python tools/profile_manifest_generator.py --profile hpc-full
    python tools/profile_manifest_generator.py --all
    python tools/profile_manifest_generator.py --list
    python tools/profile_manifest_generator.py --profile dev-compose --format json
    python tools/profile_manifest_generator.py --profile hpc-full --output deploy/manifests/
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class EnvVar:
    """Environment variable requirement."""

    name: str
    required: bool = True
    description: str = ""
    example: str = ""
    secret: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "required": self.required,
        }
        if self.description:
            d["description"] = self.description
        if self.example:
            d["example"] = self.example
        if self.secret:
            d["secret"] = True
        return d


@dataclass
class ChecklistItem:
    """Pre-deployment checklist item."""

    category: str
    description: str
    verification: str = ""

    def to_dict(self) -> dict[str, str]:
        d = {"category": self.category, "description": self.description}
        if self.verification:
            d["verification"] = self.verification
        return d


@dataclass
class ProfileManifest:
    """Generated deployment manifest for a profile."""

    profile: str
    envelope: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    env_vars: list[EnvVar] = field(default_factory=list)
    smoke_commands: list[str] = field(default_factory=list)
    rollback_procedures: list[str] = field(default_factory=list)
    required_services: list[str] = field(default_factory=list)
    checklist: list[ChecklistItem] = field(default_factory=list)
    readiness_gates: list[str] = field(default_factory=list)
    auth_model: str = ""
    ha_mode: str = "none"
    support_level: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "envelope": self.envelope,
            "support_level": self.support_level,
            "auth_model": self.auth_model,
            "ha_mode": self.ha_mode,
            "required_services": self.required_services,
            "env_vars": [e.to_dict() for e in self.env_vars],
            "smoke_commands": self.smoke_commands,
            "rollback_procedures": self.rollback_procedures,
            "checklist": [c.to_dict() for c in self.checklist],
            "readiness_gates": self.readiness_gates,
            "runtime_entries": [
                {
                    "id": e["id"],
                    "runtime_wrapper": e.get("runtime_wrapper", ""),
                    "support_level": e.get("support_level", ""),
                }
                for e in self.entries
            ],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Deployment Manifest: {self.profile}")
        lines.append("")
        lines.append(f"**Envelope**: {self.envelope}")
        lines.append(f"**Support level**: {self.support_level}")
        lines.append(f"**Auth model**: {self.auth_model}")
        lines.append(f"**HA mode**: {self.ha_mode}")
        lines.append("")

        # Required services
        lines.append("## Required Services")
        lines.append("")
        for svc in self.required_services:
            lines.append(f"- {svc}")
        lines.append("")

        # Runtime entries
        lines.append("## Runtime Entries")
        lines.append("")
        lines.append("| Entry ID | Runtime Wrapper | Support Level |")
        lines.append("| --- | --- | --- |")
        for e in self.entries:
            lines.append(
                f"| {e['id']} | {e.get('runtime_wrapper', '')} | {e.get('support_level', '')} |"
            )
        lines.append("")

        # Environment variables
        lines.append("## Environment Variables")
        lines.append("")
        lines.append("```bash")
        for ev in self.env_vars:
            marker = "# REQUIRED" if ev.required else "# optional"
            secret_note = " (SECRET)" if ev.secret else ""
            desc = f"  # {ev.description}" if ev.description else ""
            if ev.example:
                lines.append(f"{ev.name}={ev.example}{desc}")
            else:
                lines.append(f"# {ev.name}=  {marker}{secret_note}{desc}")
        lines.append("```")
        lines.append("")

        # Pre-deployment checklist
        lines.append("## Pre-Deployment Checklist")
        lines.append("")
        for i, item in enumerate(self.checklist, 1):
            check = f"  Verify: `{item.verification}`" if item.verification else ""
            lines.append(f"{i}. **[{item.category}]** {item.description}{check}")
        lines.append("")

        # Smoke commands
        lines.append("## Smoke Commands")
        lines.append("")
        lines.append("Run these commands after deployment to validate:")
        lines.append("")
        for cmd in self.smoke_commands:
            lines.append(f"```bash\n{cmd}\n```")
        lines.append("")

        # Rollback procedures
        lines.append("## Rollback Procedures")
        lines.append("")
        for i, proc in enumerate(self.rollback_procedures, 1):
            lines.append(f"{i}. {proc}")
        lines.append("")

        # Readiness gates
        if self.readiness_gates:
            lines.append("## Readiness Gates")
            lines.append("")
            for gate in self.readiness_gates:
                lines.append(f"- [{gate}]({gate})")
            lines.append("")

        return "\n".join(lines)

    def to_env_template(self) -> str:
        """Generate a .env template file."""
        lines: list[str] = []
        lines.append(f"# Environment template for profile: {self.profile}")
        lines.append(f"# Envelope: {self.envelope}")
        lines.append("# Generated by tools/profile_manifest_generator.py")
        lines.append("")

        current_category = ""
        for ev in self.env_vars:
            cat = _env_category(ev.name)
            if cat != current_category:
                if current_category:
                    lines.append("")
                lines.append(f"# --- {cat} ---")
                current_category = cat

            desc = f"  # {ev.description}" if ev.description else ""
            if ev.secret:
                lines.append(f"# {ev.name}=  # SECRET — set via secure channel")
            elif ev.example:
                lines.append(f"{ev.name}={ev.example}{desc}")
            elif ev.required:
                lines.append(f"{ev.name}={desc}")
            else:
                lines.append(f"# {ev.name}={desc}")

        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Environment variable catalog
# ---------------------------------------------------------------------------

ENV_CATALOG: dict[str, dict[str, Any]] = {
    # PostgreSQL
    "UMRS_DB_URL": {
        "description": "PostgreSQL connection string",
        "example": "postgresql://umrs:password@localhost:5432/umrs_db",
        "secret": True,
        "category": "PostgreSQL",
    },
    "POSTGRES_USER": {
        "description": "PostgreSQL user",
        "example": "umrs",
        "category": "PostgreSQL",
    },
    "POSTGRES_PASSWORD": {
        "description": "PostgreSQL password",
        "secret": True,
        "category": "PostgreSQL",
    },
    "POSTGRES_DB": {
        "description": "PostgreSQL database name",
        "example": "umrs_db",
        "category": "PostgreSQL",
    },
    # S3 / MinIO
    "UMRS_S3_ENDPOINT": {
        "description": "S3-compatible storage endpoint",
        "example": "http://localhost:9000",
        "category": "S3/MinIO",
    },
    "UMRS_S3_ACCESS_KEY": {
        "description": "S3 access key",
        "secret": True,
        "category": "S3/MinIO",
    },
    "UMRS_S3_SECRET_KEY": {
        "description": "S3 secret key",
        "secret": True,
        "category": "S3/MinIO",
    },
    "S3_PRESIGN_ENDPOINT": {
        "description": "Compute-node-reachable S3 endpoint for presigned URLs",
        "example": "http://localhost:9000",
        "category": "S3/MinIO",
    },
    "MINIO_ROOT_USER": {
        "description": "MinIO root username",
        "secret": True,
        "category": "S3/MinIO",
    },
    "MINIO_ROOT_PASSWORD": {
        "description": "MinIO root password",
        "secret": True,
        "category": "S3/MinIO",
    },
    "MINIO_BIND": {
        "description": "MinIO bind address",
        "example": "127.0.0.1",
        "category": "S3/MinIO",
    },
    # LiteLLM
    "LITELLM_MASTER_KEY": {
        "description": "LiteLLM master API key",
        "secret": True,
        "category": "LiteLLM",
    },
    "LITELLM_PORT": {
        "description": "LiteLLM API port",
        "example": "11434",
        "category": "LiteLLM",
    },
    # vLLM
    "VLLM_MODEL_PATH": {
        "description": "Filesystem path to pre-staged model weights",
        "example": "/models/Qwen/Qwen2.5-7B-Instruct",
        "category": "vLLM",
    },
    "VLLM_MAX_MODEL_LEN": {
        "description": "Max context length",
        "example": "4096",
        "category": "vLLM",
    },
    "VLLM_MAX_NUM_SEQS": {
        "description": "Max concurrent sequences",
        "example": "4",
        "category": "vLLM",
    },
    "VLLM_TENSOR_PARALLEL_SIZE": {
        "description": "GPU tensor parallelism",
        "example": "1",
        "category": "vLLM",
    },
    "VLLM_PORT": {
        "description": "vLLM service port",
        "example": "8000",
        "category": "vLLM",
    },
    # Slurm
    "SLURM_PARTITION": {
        "description": "Slurm partition for job submission",
        "example": "gpu",
        "category": "Slurm",
    },
    "SLURM_TIME": {
        "description": "Slurm job time limit",
        "example": "02:00:00",
        "category": "Slurm",
    },
    "SLURM_GPUS": {
        "description": "Number of GPUs per job",
        "example": "1",
        "category": "Slurm",
    },
    # Gateway
    "GENAI_BASE_URL": {
        "description": "Gateway base URL",
        "example": "http://localhost:8001",
        "category": "Gateway",
    },
    "UMRS_BASE_URL": {
        "description": "UMRS base URL",
        "example": "http://umrs:8000",
        "category": "Gateway",
    },
    "RATE_LIMIT_RPS": {
        "description": "Gateway rate limit (requests per second)",
        "example": "5",
        "category": "Gateway",
    },
    # ZMQ transport
    "UMRS_DATA_BASE_PORT": {
        "description": "ZMQ data plane base port",
        "example": "15556",
        "category": "Transport",
    },
    "UMRS_DATA_PORT_COUNT": {
        "description": "Number of ZMQ data ports",
        "example": "8",
        "category": "Transport",
    },
    "UMRS_TUNNEL_MODE": {
        "description": "Enable SSH tunnel mode (bind 127.0.0.1 only)",
        "example": "true",
        "category": "Transport",
    },
    "UMRS_ZMQ_AUTH_TYPE": {
        "description": "ZMQ authentication mode (none or curve)",
        "example": "curve",
        "category": "Transport",
    },
    "UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY": {
        "description": "ZMQ CURVE server public key (Z85)",
        "example": "",
        "category": "Transport",
    },
    "UMRS_ZMQ_CURVE_SERVER_SECRET_KEY": {
        "description": "ZMQ CURVE server secret key (Z85; prefer *_FILE secret injection)",
        "example": "",
        "category": "Transport",
    },
    "UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY": {
        "description": "ZMQ CURVE client public key (Z85)",
        "example": "",
        "category": "Transport",
    },
    "UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY": {
        "description": "ZMQ CURVE client secret key (Z85; prefer *_FILE secret injection)",
        "example": "",
        "category": "Transport",
    },
    "UMRS_ZMQ_CURVE_CLIENT_KEYS_DIR": {
        "description": "Directory of trusted ZMQ CURVE client public certificates for server-side ZAP auth",
        "example": "/etc/umrs/curve/clients",
        "category": "Transport",
    },
    # EgressGuard
    "EGRESS_GUARD_ENABLED": {
        "description": "Enable EgressGuard proxy",
        "example": "true",
        "category": "EgressGuard",
    },
    "EGRESS_PROXY_PORT": {
        "description": "EgressGuard proxy listen port",
        "example": "8888",
        "category": "EgressGuard",
    },
    "EGRESS_DOMAIN_ALLOWLIST": {
        "description": "Comma-separated allowlisted domains",
        "example": "",
        "category": "EgressGuard",
    },
    # NGINX
    "NGINX_UPLOAD_LIMIT": {
        "description": "Max file upload size",
        "example": "10G",
        "category": "NGINX",
    },
    "NGINX_TIMEOUT": {
        "description": "NGINX proxy timeout (seconds)",
        "example": "600",
        "category": "NGINX",
    },
}


# Profile-specific env var requirements
PROFILE_ENV: dict[str, list[str]] = {
    "hpc-full": [
        "UMRS_DB_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "S3_PRESIGN_ENDPOINT",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_BIND",
        "LITELLM_MASTER_KEY",
        "LITELLM_PORT",
        "VLLM_MODEL_PATH",
        "VLLM_MAX_MODEL_LEN",
        "VLLM_MAX_NUM_SEQS",
        "VLLM_TENSOR_PARALLEL_SIZE",
        "SLURM_PARTITION",
        "SLURM_TIME",
        "SLURM_GPUS",
        "GENAI_BASE_URL",
        "UMRS_BASE_URL",
        "UMRS_DATA_BASE_PORT",
        "UMRS_DATA_PORT_COUNT",
        "UMRS_TUNNEL_MODE",
        "EGRESS_GUARD_ENABLED",
        "EGRESS_PROXY_PORT",
        "NGINX_UPLOAD_LIMIT",
        "NGINX_TIMEOUT",
    ],
    "hpc-minimal": [
        "UMRS_DB_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "VLLM_MODEL_PATH",
        "VLLM_MAX_MODEL_LEN",
        "SLURM_PARTITION",
        "SLURM_TIME",
        "SLURM_GPUS",
        "UMRS_DATA_BASE_PORT",
        "UMRS_DATA_PORT_COUNT",
        "UMRS_TUNNEL_MODE",
    ],
    "k8s-cluster": [
        "UMRS_DB_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
        "LITELLM_PORT",
        "GENAI_BASE_URL",
        "UMRS_BASE_URL",
        "EGRESS_GUARD_ENABLED",
        "NGINX_UPLOAD_LIMIT",
        "NGINX_TIMEOUT",
    ],
    "server": [
        "UMRS_DB_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_BIND",
        "LITELLM_MASTER_KEY",
        "LITELLM_PORT",
        "GENAI_BASE_URL",
        "UMRS_BASE_URL",
        "EGRESS_GUARD_ENABLED",
        "EGRESS_PROXY_PORT",
        "NGINX_UPLOAD_LIMIT",
        "NGINX_TIMEOUT",
    ],
    "development": [
        "UMRS_DB_URL",
        "LITELLM_MASTER_KEY",
    ],
    "ci-testing": [
        "UMRS_DB_URL",
    ],
}

# Required vars (must be set; others are optional with defaults)
PROFILE_REQUIRED: dict[str, set[str]] = {
    "hpc-full": {
        "UMRS_DB_URL",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
        "VLLM_MODEL_PATH",
        "SLURM_PARTITION",
    },
    "hpc-minimal": {"UMRS_DB_URL", "VLLM_MODEL_PATH", "SLURM_PARTITION"},
    "k8s-cluster": {
        "UMRS_DB_URL",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
    },
    "server": {
        "UMRS_DB_URL",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
    },
    "development": {"UMRS_DB_URL"},
    "ci-testing": {"UMRS_DB_URL"},
}


def _env_category(name: str) -> str:
    info = ENV_CATALOG.get(name)
    if info:
        return info.get("category", "Other")
    return "Other"


# ---------------------------------------------------------------------------
# Checklist templates
# ---------------------------------------------------------------------------

COMMON_CHECKLIST: list[ChecklistItem] = [
    ChecklistItem(
        "Environment",
        "Set all required environment variables",
        "python tools/bootstrap_validator.py --profile <PROFILE>",
    ),
    ChecklistItem(
        "Environment",
        "Verify secrets are not in version control",
        "git grep -l 'MASTER_KEY\\|SECRET_KEY\\|PASSWORD' -- '*.env'",
    ),
    ChecklistItem(
        "Database",
        "PostgreSQL is running and accessible",
        "pg_isready -h <host> -p 5432",
    ),
]

PROFILE_CHECKLIST: dict[str, list[ChecklistItem]] = {
    "hpc-full": [
        ChecklistItem(
            "Runtime",
            "Singularity/Apptainer or Podman is installed",
            "which singularity || which podman",
        ),
        ChecklistItem(
            "Runtime",
            "Slurm scheduler is accessible",
            "sinfo -p $SLURM_PARTITION",
        ),
        ChecklistItem(
            "Model",
            "Model weights are pre-staged and readable",
            "ls -la $VLLM_MODEL_PATH",
        ),
        ChecklistItem(
            "Storage",
            "MinIO/S3 endpoint is reachable",
            "curl -s $UMRS_S3_ENDPOINT/minio/health/live",
        ),
        ChecklistItem(
            "Network",
            "Required ports are available (8000, 8001, 11434)",
            "python tools/bootstrap_validator.py --profile hpc-full --check-ports",
        ),
        ChecklistItem(
            "Security",
            "TRANSFORMERS_OFFLINE=1 in inference environment",
            "grep TRANSFORMERS_OFFLINE site.yaml",
        ),
        ChecklistItem(
            "Security",
            "EgressGuard is configured and enabled",
            "echo $EGRESS_GUARD_ENABLED",
        ),
        ChecklistItem(
            "Gateway",
            "NGINX sidecar config is deployed",
            "nginx -t",
        ),
        ChecklistItem(
            "Transport",
            "ZMQ tunnel mode is enabled for remote access",
            "echo $UMRS_TUNNEL_MODE",
        ),
    ],
    "hpc-minimal": [
        ChecklistItem(
            "Runtime",
            "Singularity/Apptainer is installed",
            "which singularity",
        ),
        ChecklistItem(
            "Runtime",
            "Slurm scheduler is accessible",
            "sinfo -p $SLURM_PARTITION",
        ),
        ChecklistItem(
            "Model",
            "Model weights are pre-staged and readable",
            "ls -la $VLLM_MODEL_PATH",
        ),
        ChecklistItem(
            "Network",
            "ZMQ ports are available",
            "python tools/bootstrap_validator.py --profile hpc-minimal --check-ports",
        ),
    ],
    "k8s-cluster": [
        ChecklistItem(
            "Runtime",
            "kubectl is configured and cluster is reachable",
            "kubectl cluster-info",
        ),
        ChecklistItem(
            "Storage",
            "PersistentVolumeClaims are provisioned",
            "kubectl get pvc -n rivault",
        ),
        ChecklistItem(
            "Storage",
            "MinIO/S3 endpoint is reachable from cluster",
            "kubectl run s3check --image=curlimages/curl --rm -it -- curl $UMRS_S3_ENDPOINT/minio/health/live",
        ),
        ChecklistItem(
            "Network",
            "Ingress/LoadBalancer is configured",
            "kubectl get svc -n rivault",
        ),
        ChecklistItem(
            "Security",
            "NetworkPolicies are applied for egress control",
            "kubectl get networkpolicy -n rivault",
        ),
        ChecklistItem(
            "Gateway",
            "NGINX ingress controller is healthy",
            "kubectl get pods -n ingress-nginx",
        ),
    ],
    "server": [
        ChecklistItem(
            "Runtime",
            "Podman or Docker Compose is installed",
            "which podman || which docker",
        ),
        ChecklistItem(
            "Storage",
            "MinIO/S3 endpoint is reachable",
            "curl -s $UMRS_S3_ENDPOINT/minio/health/live",
        ),
        ChecklistItem(
            "Network",
            "Required ports are available",
            "python tools/bootstrap_validator.py --profile dev-compose --check-ports",
        ),
        ChecklistItem(
            "Gateway",
            "NGINX sidecar config is deployed",
            "nginx -t",
        ),
    ],
    "development": [
        ChecklistItem(
            "Runtime",
            "Docker and Docker Compose are installed",
            "docker compose version",
        ),
        ChecklistItem(
            "Network",
            "Default ports are available (8000, 8001, 5432, 9000, 11434)",
            "python tools/bootstrap_validator.py --profile dev-compose --check-ports",
        ),
    ],
    "ci-testing": [
        ChecklistItem(
            "Runtime",
            "Docker and Docker Compose are available in CI",
            "docker compose version",
        ),
        ChecklistItem(
            "Network",
            "CI runner allows required port bindings",
            "",
        ),
    ],
}


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def load_matrix(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Load the runtime support matrix JSON."""
    if path is None:
        path = Path(__file__).parent / "runtime_support_matrix.json"
    with open(path) as f:
        return json.load(f)


def generate_manifest(
    profile: str,
    matrix_data: dict[str, Any] | None = None,
) -> ProfileManifest:
    """Generate a deployment manifest for the given profile.

    Args:
        profile: Profile name (hpc-full, hpc-minimal, k8s-cluster, etc.)
        matrix_data: Pre-loaded matrix data, or None to load default.

    Returns:
        ProfileManifest with all deployment details.

    Raises:
        ValueError: If profile is unknown.
    """
    if matrix_data is None:
        matrix_data = load_matrix()

    # Find entries matching this profile
    entries = [e for e in matrix_data.get("entries", []) if e.get("profile") == profile]
    if not entries:
        known = sorted({e.get("profile", "") for e in matrix_data.get("entries", [])})
        raise ValueError(f"Unknown profile: {profile!r}. Known profiles: {', '.join(known)}")

    # Use first entry as representative for envelope/auth/ha
    rep = entries[0]
    envelope = rep.get("envelope", "")

    # Collect unique smoke commands, rollback notes, services
    smoke_commands: list[str] = []
    rollback_procedures: list[str] = []
    all_services: set[str] = set()
    readiness_gates: list[str] = []

    for e in entries:
        cmd = e.get("smoke_command", "")
        if cmd and cmd not in smoke_commands:
            smoke_commands.append(cmd)
        rb = e.get("rollback", "")
        if rb and rb not in rollback_procedures:
            rollback_procedures.append(rb)
        for svc in e.get("required_services", []):
            all_services.add(svc)
        gate = e.get("readiness_gate", "")
        if gate and gate not in readiness_gates:
            readiness_gates.append(gate)

    # Build env var list
    env_names = PROFILE_ENV.get(profile, [])
    required_names = PROFILE_REQUIRED.get(profile, set())
    env_vars: list[EnvVar] = []
    for name in env_names:
        info = ENV_CATALOG.get(name, {})
        env_vars.append(
            EnvVar(
                name=name,
                required=name in required_names,
                description=info.get("description", ""),
                example=info.get("example", ""),
                secret=info.get("secret", False),
            )
        )

    # Build checklist
    checklist = list(COMMON_CHECKLIST)
    checklist.extend(PROFILE_CHECKLIST.get(profile, []))

    # Determine best support level
    support_levels = [e.get("support_level", "") for e in entries]
    best = "experimental"
    for level in [
        "supported",
        "site_dependent",
        "development",
        "ci",
        "compatibility",
        "experimental",
    ]:
        if level in support_levels:
            best = level
            break

    return ProfileManifest(
        profile=profile,
        envelope=envelope,
        entries=entries,
        env_vars=env_vars,
        smoke_commands=smoke_commands,
        rollback_procedures=rollback_procedures,
        required_services=sorted(all_services),
        checklist=checklist,
        readiness_gates=readiness_gates,
        auth_model=rep.get("auth_model", ""),
        ha_mode=rep.get("ha", "none"),
        support_level=best,
    )


def list_profiles(matrix_data: dict[str, Any] | None = None) -> list[str]:
    """Return sorted list of known profile names."""
    if matrix_data is None:
        matrix_data = load_matrix()
    return sorted(
        {e.get("profile", "") for e in matrix_data.get("entries", []) if e.get("profile")}
    )


def generate_all_manifests(
    matrix_data: dict[str, Any] | None = None,
) -> dict[str, ProfileManifest]:
    """Generate manifests for all profiles."""
    if matrix_data is None:
        matrix_data = load_matrix()
    profiles = list_profiles(matrix_data)
    return {p: generate_manifest(p, matrix_data) for p in profiles}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def write_manifest(
    manifest: ProfileManifest,
    output_dir: str | Path,
    fmt: str = "all",
) -> list[str]:
    """Write manifest files to output directory.

    Returns list of written file paths.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    if fmt in ("all", "json"):
        json_path = out / f"{manifest.profile}.json"
        with open(json_path, "w") as f:
            json.dump(manifest.to_dict(), f, indent=2)
            f.write("\n")
        written.append(str(json_path))

    if fmt in ("all", "markdown", "md"):
        md_path = out / f"{manifest.profile}.md"
        with open(md_path, "w") as f:
            f.write(manifest.to_markdown())
        written.append(str(md_path))

    if fmt in ("all", "env"):
        env_path = out / f"{manifest.profile}.env.template"
        with open(env_path, "w") as f:
            f.write(manifest.to_env_template())
        written.append(str(env_path))

    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate profile-specific deployment manifests (COR-001)",
    )
    parser.add_argument(
        "--profile",
        "-p",
        help="Profile name (e.g., hpc-full, k8s-cluster, development)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate manifests for all profiles",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_profiles",
        help="List available profiles",
    )
    parser.add_argument(
        "--format",
        "-f",
        default="markdown",
        choices=["json", "markdown", "env", "all"],
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output directory for generated files",
    )
    parser.add_argument(
        "--matrix",
        help="Path to runtime support matrix JSON",
    )

    args = parser.parse_args(argv)

    matrix_data = None
    if args.matrix:
        matrix_data = load_matrix(args.matrix)

    if args.list_profiles:
        profiles = list_profiles(matrix_data)
        print("Available profiles:")
        for p in profiles:
            print(f"  - {p}")
        return 0

    if args.all:
        manifests = generate_all_manifests(matrix_data)
        if args.output:
            for _name, m in manifests.items():
                written = write_manifest(m, args.output, args.format)
                for w in written:
                    print(f"  wrote: {w}")
        else:
            for _name, m in sorted(manifests.items()):
                if args.format == "json":
                    print(json.dumps(m.to_dict(), indent=2))
                else:
                    print(m.to_markdown())
                    print("---")
        return 0

    if not args.profile:
        parser.error("--profile or --all or --list required")
        return 1

    try:
        manifest = generate_manifest(args.profile, matrix_data)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.output:
        written = write_manifest(manifest, args.output, args.format)
        for w in written:
            print(f"  wrote: {w}")
    else:
        if args.format == "json":
            print(json.dumps(manifest.to_dict(), indent=2))
        elif args.format == "env":
            print(manifest.to_env_template())
        else:
            print(manifest.to_markdown())

    return 0


if __name__ == "__main__":
    sys.exit(main())
