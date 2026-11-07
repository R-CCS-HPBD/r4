"""
Hatchling MCP Integration (A-701 to A-710)

Enables users to develop, share, and use MCP servers following the Hatch! package
ecosystem. All MCP servers connect to the platform's LLM (Mini-RiVault) instead
of local Ollama.

Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                    User Layer (R4 SDK)                       │
    │  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
    │  │  Hatchling CLI  │  │  Hatch! Package │  │ Hatch-Registry │  │
    │  │  (User's env)   │  │  (MCP Server)   │  │ (Public pkgs)  │  │
    │  └────────┬────────┘  └────────┬────────┘  └───────┬────────┘  │
    │           │                    │                   │           │
    │           └────────────────────┼───────────────────┘           │
    │                                │                               │
    │                    ┌───────────▼───────────┐                   │
    │                    │  Hatchling Adapter    │                   │
    │                    │  (LLM Provider Bridge)│                   │
    │                    └───────────┬───────────┘                   │
    └────────────────────────────────┼───────────────────────────────┘
                                     │
                        ┌────────────▼────────────┐
                        │    Platform Gateway     │
                        │    /gw/llm/* routes     │
                        └────────────▬────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)

# Where a Hatch runtime listens, and the address UMRS is told to dial.
#
# Default (both unset): listen on 127.0.0.1 and advertise 127.0.0.1 — right when
# UMRS shares the host's network (umrs-front). When UMRS runs in a container
# (the stand-alone deployment), its 127.0.0.1 is the container itself, so the
# registered URL is unreachable: registration succeeds and every tool call fails.
# There, set R4_HATCH_ADVERTISE_HOST=host.containers.internal and
# R4_HATCH_BIND_HOST=0.0.0.0 (a loopback-only host port is not reachable from
# podman/Docker on macOS). A non-loopback bind always gets a per-runtime bearer
# token, required on every request and registered with UMRS, so the runtime is
# not an open tool-execution endpoint on the LAN.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})


def _hatch_bind_host() -> str:
    return os.environ.get("R4_HATCH_BIND_HOST", "").strip() or "127.0.0.1"


def _hatch_advertise_host(bind_host: str) -> str:
    explicit = os.environ.get("R4_HATCH_ADVERTISE_HOST", "").strip()
    if explicit:
        return explicit
    return "127.0.0.1" if bind_host in _WILDCARD_HOSTS else bind_host


def _is_loopback_host(host: str) -> bool:
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _runtime_gateway_env(client: Any) -> dict[str, str]:
    """R4_GATEWAY_URL/R4_GATEWAY_TOKEN for a runtime, from the connecting client."""
    cfg = getattr(client, "config", None)
    url = (getattr(cfg, "gateway_url", "") or "").rstrip("/")
    key = getattr(cfg, "api_key", None) or ""
    if not (url and key):
        return {}
    return {"R4_GATEWAY_URL": url, "R4_GATEWAY_TOKEN": key}


def _bearer_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _runtime_env(client: Any, token: str | None) -> dict[str, str]:
    """The hatch runtime's environment: our ``sys.path``, its token, the gateway."""
    env = os.environ.copy()
    python_path_parts = [path for path in sys.path if path]
    if env.get("PYTHONPATH"):
        python_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    # Via the environment, not argv: argv is visible to every local user.
    env.pop("R4_HATCH_RUNTIME_TOKEN", None)
    if token:
        env["R4_HATCH_RUNTIME_TOKEN"] = token
    # The package's own gateway calls (e.g. platform-tools -> /gw/models)
    # must go to the deployment THIS client is connected to. The default
    # packages honour R4_GATEWAY_URL/R4_GATEWAY_TOKEN first; without them
    # they fell back to reading sources.json themselves, and could pick a
    # different source than the active one.
    env.update(_runtime_gateway_env(client))
    return env


def _runtime_command(entry_point: Path, package_name: str, host: str, port: int) -> list[str]:
    runtime_script = Path(__file__).with_name("hatch_runtime.py")
    return [
        sys.executable,
        str(runtime_script),
        "--entry-point",
        str(entry_point),
        "--package-name",
        package_name,
        "--host",
        host,
        "--port",
        str(port),
    ]


def _sandboxed_runtime(command: list[str], package_dir: Path) -> tuple[list[str], str | None]:
    """ADR-101 D-9: under the MCP sandbox, run the runtime through the launcher.

    Host network (UMRS dials the runtime on loopback), the package directory
    readable inside the denied r4 config dir, ``cwd="/"``; the launcher keeps
    ``R4_GATEWAY_URL``/``R4_GATEWAY_TOKEN`` and the runtime's own token. The
    command is unchanged (and ``cwd`` None) when the MCP sandbox is off.
    """
    from r4.companion.mcp_exposure import hatch_runtime_prefix

    prefix = hatch_runtime_prefix(package_dir)
    if not prefix:
        return command, None
    return [*prefix, *command], "/"


def _unsandboxed_alive(state_path: Path) -> bool:
    """A live runtime whose state does not say it was started through the launcher."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0)) if isinstance(state, dict) else 0
    except (OSError, ValueError, TypeError):
        return False
    if pid <= 0 or state.get("sandboxed") is True:
        return False
    return HatchlingBridge._is_process_alive(pid)


def unsandboxed_hatch_runtimes(packages_dir: Path | None = None) -> list[str]:
    """ADR-101 D-9: hatch runtimes still running unconfined (started before the
    MCP sandbox went on; they stay so until disconnected and reconnected)."""
    root = packages_dir or HATCH_PACKAGES_DIR
    try:
        dirs = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError:
        return []
    return sorted(d.name for d in dirs if _unsandboxed_alive(d / HATCH_RUNTIME_STATE_FILE))


# Hatch-Registry URL
HATCH_REGISTRY_URL = os.environ.get(
    "HATCH_REGISTRY_URL",
    "https://github.com/CrackingShells/Hatch-Registry",
)

# Package storage location
HATCH_PACKAGES_DIR = Path(
    os.environ.get(
        "R4_HATCH_PACKAGES_DIR",
        os.path.expanduser("~/.config/r4/hatch_packages"),
    )
)

HATCH_RUNTIME_STATE_FILE = ".r4_hatch_runtime.json"


def _default_hatch_registry_urls() -> list[str]:
    raw = os.environ.get("R4_HATCH_REGISTRIES", "").strip()
    if raw:
        urls = [item.strip() for item in raw.split(",") if item.strip()]
        return urls or [HATCH_REGISTRY_URL]
    return [HATCH_REGISTRY_URL]


def _display_registry_name(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        return Path(parsed.path).name or url
    if parsed.scheme and parsed.netloc:
        return parsed.netloc + parsed.path.rstrip("/")
    return Path(url).expanduser().name or url


def _local_reference_path(value: str) -> Path | None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "file":
        return Path(urllib.parse.unquote(parsed.path)).expanduser()
    if parsed.scheme:
        return None
    return Path(value).expanduser()


def _looks_like_git_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"git", "http", "https", "ssh"} or value.startswith("git@")


def _runtime_tool_names(runtime_state: dict[str, Any]) -> list[str]:
    """Extract non-empty tool names from a runtime state dict."""
    return [
        tool.get("name", "")
        for tool in runtime_state.get("tools", [])
        if isinstance(tool, dict) and tool.get("name")
    ]


def _matches_package(query: str, package: dict[str, Any]) -> bool:
    if not query:
        return True
    needle = query.lower()
    tags = package.get("tags", [])
    if isinstance(tags, list):
        tag_text = " ".join(str(tag) for tag in tags)
    else:
        tag_text = str(tags)
    haystack = " ".join(
        str(package.get(field_name, ""))
        for field_name in ("name", "description", "author", "license")
    )
    haystack = f"{haystack} {tag_text}".lower()
    return needle in haystack


@dataclass(frozen=True)
class HatchRegistrySource:
    """Configured Hatch registry source."""

    url: str
    name: str = ""
    kind: str = "auto"
    priority: int = 100

    @property
    def display_name(self) -> str:
        return self.name or _display_registry_name(self.url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "url": self.url,
            "kind": self.kind,
            "priority": self.priority,
        }


class PackageSource(Enum):
    """Source of a Hatch! package."""

    REGISTRY = "registry"
    LOCAL = "local"
    GIT = "git"


class PackageStatus(Enum):
    """Status of an installed package."""

    INSTALLED = "installed"
    CONNECTED = "connected"
    ERROR = "error"
    NOT_FOUND = "not_found"


@dataclass
class HatchPackageInfo:
    """Information about a Hatch! package."""

    name: str
    version: str
    description: str = ""
    source: PackageSource = PackageSource.LOCAL
    source_url: Optional[str] = None
    install_path: Optional[Path] = None
    status: PackageStatus = PackageStatus.NOT_FOUND
    tools: list[str] = field(default_factory=list)
    author: Optional[str] = None
    license: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": self.source.value,
            "source_url": self.source_url,
            "install_path": str(self.install_path) if self.install_path else None,
            "status": self.status.value,
            "tools": self.tools,
            "author": self.author,
            "license": self.license,
            "dependencies": self.dependencies,
        }


@dataclass
class HatchSecurityPolicy:
    """Security policy for Hatch! packages (A-707)."""

    # Package allowlist (None = allow all, empty = deny all)
    allowed_packages: Optional[list[str]] = None
    # Registry allowlist
    allowed_registries: list[str] = field(default_factory=_default_hatch_registry_urls)
    # Require package signatures
    require_signature: bool = False
    # Admin-approved packages only
    admin_approval_required: bool = False
    # Max package size in MB
    max_package_size_mb: int = 100

    def is_package_allowed(self, package_name: str) -> bool:
        """Check if a package is allowed by policy."""
        if self.allowed_packages is None:
            return True  # Allow all
        return package_name in self.allowed_packages

    def is_registry_allowed(self, registry_url: str) -> bool:
        """Check if a registry is allowed."""
        return any(registry_url.startswith(allowed) for allowed in self.allowed_registries)

    def registry_sources(self) -> list[HatchRegistrySource]:
        """Return configured registries as source descriptors."""
        return [
            HatchRegistrySource(url=url, priority=index)
            for index, url in enumerate(self.allowed_registries)
        ]


class HatchRegistryClient:
    """
    Client for Hatch-Registry API (A-709).

    Fetches package information from the Hatch-Registry service.
    Registry structure: https://github.com/CrackingShells/Hatch-Registry
    """

    def __init__(self, registry_url: str = HATCH_REGISTRY_URL):
        """Initialize registry client."""
        self.registry_url = registry_url.rstrip("/")
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ttl = 300  # 5 minutes

    def _local_registry_root(self) -> Path | None:
        return _local_reference_path(self.registry_url)

    def _with_registry_metadata(self, package: dict[str, Any]) -> dict[str, Any]:
        data = dict(package)
        data.setdefault("registry_url", self.registry_url)
        return data

    async def get_package_info(self, package_name: str) -> Optional[dict[str, Any]]:
        """
        Get package information from registry.

        Args:
            package_name: Package name to look up

        Returns:
            Package info dict or None if not found
        """
        local_root = self._local_registry_root()
        if local_root is not None:
            return self._get_package_info_local(package_name, local_root)

        try:
            import aiohttp
        except ImportError:
            # Fallback to sync HTTP
            return await self._get_package_info_sync(package_name)

        # Check cache
        if package_name in self._cache:
            return self._cache[package_name]

        # Registry structure: packages/{name}/package.json
        # Or for GitHub-based registry: raw URL to package manifest
        manifest_url = self._get_manifest_url(package_name)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(manifest_url, timeout=30) as response:
                    if response.status == 404:
                        return None
                    response.raise_for_status()
                    data = await response.json()
                    data = self._with_registry_metadata(data)
                    self._cache[package_name] = data
                    return data
        except Exception as e:
            logger.warning(f"Failed to fetch package info from registry: {e}")
            return None

    async def _get_package_info_sync(self, package_name: str) -> Optional[dict[str, Any]]:
        """Fallback sync HTTP client."""
        import urllib.error
        import urllib.request

        manifest_url = self._get_manifest_url(package_name)

        try:
            with urllib.request.urlopen(manifest_url, timeout=30) as response:
                import json

                data = json.loads(response.read().decode())
                data = self._with_registry_metadata(data)
                self._cache[package_name] = data
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.warning(f"Failed to fetch package info: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch package info: {e}")
            return None

    def _get_manifest_url(self, package_name: str) -> str:
        """Get manifest URL for package."""
        # GitHub raw URL format for Hatch-Registry
        if "github.com" in self.registry_url:
            # Convert to raw URL
            base = self.registry_url.replace("github.com", "raw.githubusercontent.com")
            return f"{base}/main/packages/{package_name}/hatch.json"
        else:
            # Generic registry API
            return f"{self.registry_url}/api/v1/packages/{package_name}"

    def _get_package_info_local(
        self,
        package_name: str,
        root: Path,
    ) -> Optional[dict[str, Any]]:
        """Load one package manifest from a local/static registry."""
        candidates = [
            root / "packages" / package_name / "hatch.json",
            root / package_name / "hatch.json",
            root / "packages" / package_name / "package.json",
            root / package_name / "package.json",
        ]
        for manifest_path in candidates:
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read Hatch package manifest %s: %s", manifest_path, exc)
                return None
            data.setdefault("name", package_name)
            data = self._with_registry_metadata(data)
            self._cache[package_name] = data
            return data
        return None

    def _load_local_registry_index(self, root: Path) -> list[dict[str, Any]]:
        """Load package metadata from a local registry index or package tree."""
        for name in ("registry.json", "index.json", "packages.json", "hatch-registry.json"):
            index_path = root / name
            if not index_path.is_file():
                continue
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read Hatch registry index %s: %s", index_path, exc)
                return []
            return self._normalize_package_index(payload)

        package_root = root / "packages"
        if not package_root.is_dir():
            package_root = root
        packages = []
        for manifest_path in sorted(package_root.glob("*/hatch.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            data.setdefault("name", manifest_path.parent.name)
            packages.append(data)
        return packages

    @staticmethod
    def _normalize_package_mapping(packages: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize a name→data package mapping into package dicts."""
        normalized = []
        for name, data in packages.items():
            if isinstance(data, dict):
                item = dict(data)
                item.setdefault("name", name)
                normalized.append(item)
            elif isinstance(data, str):
                normalized.append({"name": name, "repository": data})
        return normalized

    def _normalize_package_index(self, payload: Any) -> list[dict[str, Any]]:
        """Normalize registry index payload shapes into package dicts."""
        if isinstance(payload, dict):
            packages = payload.get("packages", payload.get("items", []))
            if isinstance(packages, dict):
                return self._normalize_package_mapping(packages)
        elif isinstance(payload, list):
            packages = payload
        else:
            packages = []

        normalized = []
        for item in packages:
            if isinstance(item, dict):
                normalized.append(dict(item))
            elif isinstance(item, str):
                normalized.append({"name": item})
        return normalized

    def _raw_registry_base(self) -> str:
        """Return a raw-content base URL for static registry indexes."""
        if "github.com" in self.registry_url:
            return self.registry_url.replace("github.com", "raw.githubusercontent.com") + "/main"
        return self.registry_url

    def _registry_index_urls(self) -> list[str]:
        base = self._raw_registry_base().rstrip("/")
        return [
            f"{base}/registry.json",
            f"{base}/index.json",
            f"{base}/packages.json",
            f"{base}/hatch-registry.json",
        ]

    async def _load_remote_registry_index(self) -> list[dict[str, Any]]:
        """Load a static JSON registry index from HTTP if available."""
        try:
            import aiohttp
        except ImportError:
            return self._load_remote_registry_index_sync()

        for index_url in self._registry_index_urls():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(index_url, timeout=30) as response:
                        if response.status == 404:
                            continue
                        if response.status != 200:
                            continue
                        return self._normalize_package_index(await response.json())
            except Exception:
                continue
        return []

    def _load_remote_registry_index_sync(self) -> list[dict[str, Any]]:
        import urllib.error
        import urllib.request

        for index_url in self._registry_index_urls():
            try:
                with urllib.request.urlopen(index_url, timeout=30) as response:
                    payload = json.loads(response.read().decode())
                    return self._normalize_package_index(payload)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
            except Exception:
                continue
        return []

    async def search_packages(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search packages in registry.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of package info dicts
        """
        local_root = self._local_registry_root()
        if local_root is not None:
            return self._search_package_index(
                self._load_local_registry_index(local_root),
                query,
                limit,
            )

        indexed = await self._load_remote_registry_index()
        if indexed:
            return self._search_package_index(indexed, query, limit)

        try:
            import aiohttp
        except ImportError:
            return await self._search_packages_sync(query, limit)

        search_url = f"{self.registry_url}/api/v1/search?q={query}&limit={limit}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, timeout=30) as response:
                    if response.status != 200:
                        return []
                    data = await response.json()
                    return self._search_package_index(
                        self._normalize_package_index(data),
                        query,
                        limit,
                    )
        except Exception as e:
            logger.warning(f"Registry search failed: {e}")
            return []

    def _search_package_index(
        self,
        packages: list[dict[str, Any]],
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        matches = []
        for package in packages:
            if not _matches_package(query, package):
                continue
            matches.append(self._with_registry_metadata(package))
            if len(matches) >= limit:
                break
        return matches

    async def _search_packages_sync(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fallback sync search."""
        import urllib.error
        import urllib.request

        search_url = f"{self.registry_url}/api/v1/search?q={query}&limit={limit}"

        try:
            with urllib.request.urlopen(search_url, timeout=30) as response:
                import json

                data = json.loads(response.read().decode())
                return self._search_package_index(
                    self._normalize_package_index(data),
                    query,
                    limit,
                )
        except Exception as e:
            logger.warning(f"Registry search failed: {e}")
            return []

    async def list_packages(self, limit: int = 100) -> list[dict[str, Any]]:
        """List all available packages."""
        return await self.search_packages("", limit)


class HatchlingBridge:
    """
    Bridge between Hatchling/Hatch! ecosystem and Mini-RiVault LLM (A-701, A-702).

    This bridge allows Hatchling packages to use the platform's LLM
    instead of local Ollama.
    """

    def __init__(
        self,
        client: "Client",
        packages_dir: Optional[Path] = None,
        security_policy: Optional[HatchSecurityPolicy] = None,
    ):
        """
        Initialize HatchlingBridge.

        Args:
            client: R4 client for LLM access
            packages_dir: Directory for installed packages
            security_policy: Security policy for package management
        """
        self.client = client
        self.packages_dir = packages_dir or HATCH_PACKAGES_DIR
        self.security_policy = security_policy or HatchSecurityPolicy()
        self._installed_packages: dict[str, HatchPackageInfo] = {}
        self._connected_packages: set[str] = set()

        # Ensure packages directory exists
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self._load_installed_packages()

    def list_registry_sources(self) -> list[HatchRegistrySource]:
        """List configured Hatch registries in search order."""
        return self.security_policy.registry_sources()

    async def search_available_packages(
        self,
        query: str = "",
        *,
        limit: int = 20,
        registry_url: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search available Hatch packages across configured registries."""
        sources = self._registry_sources(registry_url)
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for source in sources:
            if not self.security_policy.is_registry_allowed(source.url):
                continue
            client = HatchRegistryClient(source.url)
            for package in await client.search_packages(query, limit=limit):
                name = str(package.get("name", ""))
                version = str(package.get("version", ""))
                key = (source.url, name, version)
                if not name or key in seen:
                    continue
                package.setdefault("registry_name", source.display_name)
                package.setdefault("registry_url", source.url)
                results.append(package)
                seen.add(key)
                if len(results) >= limit:
                    return results
        return results

    def _registry_sources(self, registry_url: Optional[str] = None) -> list[HatchRegistrySource]:
        if registry_url:
            return [HatchRegistrySource(url=registry_url, priority=0)]
        return self.list_registry_sources()

    def _runtime_state_path(self, package_name: str) -> Path:
        return self.packages_dir / package_name / HATCH_RUNTIME_STATE_FILE

    def _load_runtime_state(self, package_name: str) -> Optional[dict[str, Any]]:
        path = self._runtime_state_path(package_name)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load runtime state for {package_name}: {e}")
            return None

    def _save_runtime_state(self, package_name: str, state: dict[str, Any]) -> None:
        path = self._runtime_state_path(package_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0600: the state can hold the runtime's bearer token.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.chmod(path, 0o600)

    def _clear_runtime_state(self, package_name: str) -> None:
        path = self._runtime_state_path(package_name)
        if path.exists():
            path.unlink()

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _wait_for_runtime_health(
        self, url: str, timeout_sec: float = 10.0, token: str | None = None
    ) -> None:
        import httpx

        deadline = asyncio.get_running_loop().time() + timeout_sec
        async with httpx.AsyncClient(
            timeout=2.0,
            verify=self.client.config.verify_ssl,
            trust_env=False,
        ) as http_client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await http_client.get(
                        f"{url}/health", headers=_bearer_headers(token)
                    )
                    if response.status_code == 200:
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError(f"Timed out waiting for Hatch bridge health at {url}")

    async def _fetch_runtime_tools(
        self, url: str, token: str | None = None
    ) -> list[dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient(
            timeout=5.0,
            verify=self.client.config.verify_ssl,
            trust_env=False,
        ) as http_client:
            response = await http_client.get(f"{url}/tools", headers=_bearer_headers(token))
            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict):
            tools = payload.get("tools", [])
        elif isinstance(payload, list):
            tools = payload
        else:
            tools = []

        return [tool for tool in tools if isinstance(tool, dict)]

    async def _launch_runtime(
        self,
        package_name: str,
        entry_point: Path,
    ) -> dict[str, Any]:
        host = _hatch_bind_host()
        port = self._find_free_port()
        probe_host = "127.0.0.1" if host in _WILDCARD_HOSTS else host
        probe_url = f"http://{probe_host}:{port}"
        url = f"http://{_hatch_advertise_host(host)}:{port}"
        token = None if _is_loopback_host(host) else secrets.token_urlsafe(32)
        package_dir = self.packages_dir / package_name
        log_path = package_dir / ".r4_hatch_runtime.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = _runtime_env(self.client, token)
        command, cwd = _sandboxed_runtime(
            _runtime_command(entry_point, package_name, host, port), package_dir
        )
        with open(log_path, "ab") as log_file:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
                env=env,
                cwd=cwd,
            )

        try:
            await self._wait_for_runtime_health(probe_url, token=token)
            tools = await self._fetch_runtime_tools(probe_url, token=token)
        except Exception:
            if proc.returncode is None:
                proc.terminate()
            raise

        return {
            "pid": proc.pid,
            "host": host,
            "port": port,
            "url": url,
            "probe_url": probe_url,
            "token": token,
            "server_name": f"hatch-{package_name}",
            "entry_point": str(entry_point),
            "tools": tools,
            "log_path": str(log_path),
            # ADR-101 D-9: lets the sandbox status name runtimes left unconfined.
            "sandboxed": cwd is not None,
        }

    def _load_installed_packages(self) -> None:
        """Load installed packages from disk."""
        if not self.packages_dir.exists():
            return

        for pkg_dir in self.packages_dir.iterdir():
            if not pkg_dir.is_dir() or not (pkg_dir / "hatch.json").exists():
                continue
            try:
                self._load_package_from_dir(pkg_dir)
            except Exception as e:
                logger.warning(f"Failed to load package {pkg_dir.name}: {e}")

    def _load_package_from_dir(self, pkg_dir: Path) -> None:
        """Load one installed package manifest and its runtime state."""
        with open(pkg_dir / "hatch.json") as f:
            manifest = json.load(f)
        self._installed_packages[pkg_dir.name] = HatchPackageInfo(
            name=manifest.get("name", pkg_dir.name),
            version=manifest.get("version", "0.0.0"),
            description=manifest.get("description", ""),
            source=PackageSource(manifest.get("source", "local")),
            source_url=manifest.get("source_url"),
            install_path=pkg_dir,
            status=PackageStatus.INSTALLED,
            tools=manifest.get("tools", []),
            author=manifest.get("author"),
            license=manifest.get("license"),
            dependencies=manifest.get("dependencies", []),
        )
        self._apply_runtime_state(pkg_dir.name)

    def _apply_runtime_state(self, package_name: str) -> None:
        """Mark a loaded package connected when its runtime is still alive."""
        runtime_state = self._load_runtime_state(package_name)
        if runtime_state and self._is_process_alive(int(runtime_state.get("pid", 0))):
            self._connected_packages.add(package_name)
            self._installed_packages[package_name].status = PackageStatus.CONNECTED
            tool_names = _runtime_tool_names(runtime_state)
            if tool_names:
                self._installed_packages[package_name].tools = tool_names
        elif runtime_state:
            self._clear_runtime_state(package_name)

    async def install_package(
        self,
        package_name: str,
        source: PackageSource = PackageSource.REGISTRY,
        source_url: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> HatchPackageInfo:
        """
        Install a Hatch! package (A-704).

        Args:
            package_name: Package name to install
            source: Where to install from
            source_url: URL for git or local source
            on_progress: Progress callback

        Returns:
            Installed package info

        Raises:
            ValueError: If package not allowed by security policy
            RuntimeError: If installation fails
        """
        # Security checks
        if not self.security_policy.is_package_allowed(package_name):
            raise ValueError(f"Package '{package_name}' is not allowed by security policy")

        if on_progress:
            on_progress(f"Installing {package_name}...")

        if source == PackageSource.REGISTRY:
            return await self._install_from_registry(
                package_name,
                on_progress,
                registry_url=source_url,
            )
        elif source == PackageSource.LOCAL:
            if not source_url:
                raise ValueError("source_url required for local install")
            return await self._install_from_local(package_name, Path(source_url), on_progress)
        elif source == PackageSource.GIT:
            if not source_url:
                raise ValueError("source_url required for git install")
            return await self._install_from_git(package_name, source_url, on_progress)
        else:
            raise ValueError(f"Unknown source: {source}")

    async def _install_from_registry(
        self,
        package_name: str,
        on_progress: Optional[Callable[[str], None]] = None,
        registry_url: Optional[str] = None,
    ) -> HatchPackageInfo:
        """Install package from Hatch-Registry (A-709)."""
        if on_progress:
            on_progress(f"Fetching {package_name} from registry...")

        last_denied: str | None = None
        for registry in self._registry_sources(registry_url):
            if not self.security_policy.is_registry_allowed(registry.url):
                last_denied = registry.url
                continue

            registry_client = HatchRegistryClient(registry.url)
            pkg_info = await registry_client.get_package_info(package_name)
            if not pkg_info:
                continue

            if on_progress:
                on_progress(
                    f"Found {package_name} v{pkg_info.get('version', 'unknown')} "
                    f"in {registry.display_name}"
                )

            return await self._install_registry_package(
                package_name,
                pkg_info,
                on_progress,
            )

        if last_denied and registry_url:
            raise ValueError(f"Registry {last_denied} not allowed by security policy")
        raise ValueError(f"Package '{package_name}' not found in configured registries")

    async def _install_from_registry_tree(
        self,
        package_name: str,
        pkg_info: dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Optional[HatchPackageInfo]:
        """Install from a local/static registry that hosts packages in-tree.

        Falls back to <registry_root>/packages/<name> then
        <registry_root>/<name>. Returns None when no candidate exists.
        """
        registry_root = _local_reference_path(str(pkg_info.get("registry_url", "")))
        if registry_root is None:
            return None
        for candidate in (
            registry_root / "packages" / package_name,
            registry_root / package_name,
        ):
            if (candidate / "hatch.json").is_file():
                return await self._install_from_local(package_name, candidate, on_progress)
        return None

    async def _install_registry_package(
        self,
        package_name: str,
        pkg_info: dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> HatchPackageInfo:
        """Install a package from resolved registry metadata."""
        source_ref = pkg_info.get("repository") or pkg_info.get("source_url") or pkg_info.get("url")
        if not isinstance(source_ref, str) or not source_ref.strip():
            installed = await self._install_from_registry_tree(package_name, pkg_info, on_progress)
            if installed is not None:
                return installed
            raise ValueError(
                f"Registry entry for '{package_name}' is missing repository/source_url"
            )
        source_ref = source_ref.strip()

        local_path = _local_reference_path(source_ref)
        if local_path is not None and local_path.exists():
            return await self._install_from_local(package_name, local_path, on_progress)

        if _looks_like_git_url(source_ref):
            return await self._install_from_git(package_name, source_ref, on_progress)

        raise ValueError(
            f"Registry entry for '{package_name}' has unsupported source: {source_ref}"
        )

    async def _install_from_git(
        self,
        package_name: str,
        git_url: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> HatchPackageInfo:
        """Install package from git repository."""
        pkg_dir = self.packages_dir / package_name

        if pkg_dir.exists():
            if on_progress:
                on_progress(f"Updating existing package {package_name}...")
            shutil.rmtree(pkg_dir)

        if on_progress:
            on_progress(f"Cloning {git_url}...")

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                git_url,
                str(pkg_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                shutil.rmtree(pkg_dir, ignore_errors=True)
                raise RuntimeError(f"Git clone failed: {stderr.decode()}")

        except FileNotFoundError:
            raise RuntimeError("git command not found. Please install git.")

        return await self._complete_installation(
            package_name,
            pkg_dir,
            git_url,
            on_progress,
        )

    async def _install_from_local(
        self,
        package_name: str,
        local_path: Path,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> HatchPackageInfo:
        """Install package from local path."""
        if not local_path.exists():
            raise ValueError(f"Local path does not exist: {local_path}")

        pkg_dir = self.packages_dir / package_name

        if pkg_dir.exists():
            if on_progress:
                on_progress(f"Updating existing package {package_name}...")
            shutil.rmtree(pkg_dir)

        if on_progress:
            on_progress(f"Copying from {local_path}...")

        shutil.copytree(local_path, pkg_dir)

        return await self._complete_installation(
            package_name,
            pkg_dir,
            str(local_path),
            on_progress,
        )

    async def _complete_installation(
        self,
        package_name: str,
        pkg_dir: Path,
        source_url: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> HatchPackageInfo:
        """Validate a fetched package and persist it as an installed package."""
        try:
            await self._validate_package_directory(pkg_dir, on_progress)
            return self._finalize_installation(package_name, pkg_dir, source_url)
        except Exception:
            shutil.rmtree(pkg_dir, ignore_errors=True)
            raise

    async def _validate_package_directory(
        self,
        pkg_dir: Path,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Run post-fetch security validation before finalizing installation."""
        if not self.security_policy.require_signature:
            return

        if on_progress:
            on_progress("Verifying package signature...")

        verifier = SignatureVerifier()
        result = await verifier.verify(pkg_dir)
        if not result.is_valid:
            raise RuntimeError(
                f"Package signature verification failed: {result.error or 'invalid signature'}"
            )

    def _finalize_installation(
        self,
        package_name: str,
        pkg_dir: Path,
        source_url: str,
    ) -> HatchPackageInfo:
        """Finalize package installation."""
        # Look for manifest
        manifest_path = pkg_dir / "hatch.json"
        package_json = pkg_dir / "package.json"

        manifest = {"name": package_name, "version": "0.0.0"}

        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest.update(json.load(f))
        elif package_json.exists():
            with open(package_json) as f:
                npm_manifest = json.load(f)
                manifest.update(
                    {
                        "name": npm_manifest.get("name", package_name),
                        "version": npm_manifest.get("version", "0.0.0"),
                        "description": npm_manifest.get("description", ""),
                        "author": npm_manifest.get("author"),
                        "license": npm_manifest.get("license"),
                    }
                )

        # Save manifest with source info
        manifest["source"] = "git" if source_url.startswith("http") else "local"
        manifest["source_url"] = source_url

        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Create package info
        pkg_info = HatchPackageInfo(
            name=manifest.get("name", package_name),
            version=manifest.get("version", "0.0.0"),
            description=manifest.get("description", ""),
            source=PackageSource(manifest.get("source", "local")),
            source_url=source_url,
            install_path=pkg_dir,
            status=PackageStatus.INSTALLED,
            tools=manifest.get("tools", []),
            author=manifest.get("author"),
            license=manifest.get("license"),
            dependencies=manifest.get("dependencies", []),
        )

        self._installed_packages[package_name] = pkg_info
        return pkg_info

    async def connect_package(self, package_name: str) -> bool:
        """
        Connect and enable tools from a Hatch! package (A-705).

        Args:
            package_name: Package to connect

        Returns:
            True if connected successfully
        """
        if package_name not in self._installed_packages:
            raise ValueError(f"Package '{package_name}' is not installed")

        pkg_info = self._installed_packages[package_name]
        runtime_state = self._load_runtime_state(package_name)
        if runtime_state and self._is_process_alive(int(runtime_state.get("pid", 0))):
            await self._mark_connected(package_name, runtime_state)
            return True
        if runtime_state:
            self._clear_runtime_state(package_name)

        # Look for MCP server entry point
        entry_points = [
            pkg_info.install_path / "server.py",
            pkg_info.install_path / "src" / "server.py",
            pkg_info.install_path / "index.js",
            pkg_info.install_path / "dist" / "index.js",
        ]

        for entry in entry_points:
            if entry.exists():
                runtime_state = await self._launch_runtime(package_name, entry)
                self._save_runtime_state(package_name, runtime_state)
                await self._mark_connected(package_name, runtime_state)
                return True

        logger.warning(f"No MCP server entry point found for {package_name}")
        return False

    async def _mark_connected(self, package_name: str, runtime_state: dict[str, Any]) -> None:
        """Register a live runtime with UMRS and mark the package connected."""
        await self._register_with_umrs(
            package_name,
            runtime_state["url"],
            runtime_state.get("tools", []),
            token=runtime_state.get("token"),
        )
        self._connected_packages.add(package_name)
        pkg_info = self._installed_packages[package_name]
        pkg_info.status = PackageStatus.CONNECTED
        tool_names = _runtime_tool_names(runtime_state)
        if tool_names:
            pkg_info.tools = tool_names

    async def _register_with_umrs(
        self,
        package_name: str,
        server_url: str,
        tools: list[dict[str, Any]],
        token: str | None = None,
    ) -> None:
        """Register package MCP server with UMRS via the client.

        With a runtime token, UMRS stores it and presents it as
        ``Authorization: Bearer`` on every proxied tool call.
        """
        self.client.mcp.register_server(
            name=f"hatch-{package_name}",
            url=server_url,
            tools=tools,
            metadata={"source": "hatch", "package": package_name},
            description=f"Hatch! package: {package_name}",
            auth_type="api_key" if token else "none",
            auth_config={"token": token} if token else None,
        )

    async def disconnect_package(self, package_name: str) -> bool:
        """Disconnect a package."""
        runtime_state = self._load_runtime_state(package_name)
        if package_name not in self._connected_packages and not runtime_state:
            return False

        self._connected_packages.discard(package_name)

        if runtime_state:
            server_name = runtime_state.get("server_name", f"hatch-{package_name}")
            try:
                self.client.mcp.unregister_server(server_name)
            except Exception:
                logger.debug("Failed to unregister Hatch package %s from Gateway", package_name)

            pid = int(runtime_state.get("pid", 0))
            if self._is_process_alive(pid):
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(0.5)
                if self._is_process_alive(pid):
                    os.kill(pid, signal.SIGKILL)

            self._clear_runtime_state(package_name)

        if package_name in self._installed_packages:
            self._installed_packages[package_name].status = PackageStatus.INSTALLED

        return True

    def list_packages(
        self,
        include_available: bool = False,
    ) -> list[HatchPackageInfo]:
        """
        List installed Hatch! packages (A-706).

        Args:
            include_available: Also list available packages from registry

        Returns:
            List of package info
        """
        packages = list(self._installed_packages.values())

        if include_available:
            # Note: This would require querying the Hatch-Registry
            # For now, just return installed packages
            pass

        return packages

    def uninstall_package(self, package_name: str) -> bool:
        """Uninstall a package."""
        if package_name not in self._installed_packages:
            return False

        pkg_info = self._installed_packages[package_name]

        # Disconnect first if connected
        if package_name in self._connected_packages:
            asyncio.run(self.disconnect_package(package_name))

        # Remove from disk
        if pkg_info.install_path and pkg_info.install_path.exists():
            shutil.rmtree(pkg_info.install_path)

        del self._installed_packages[package_name]
        return True

    async def share_package(
        self,
        package_name: str,
        project_id: str,
    ) -> bool:
        """
        Share a Hatch! package within a project (A-710).

        Args:
            package_name: Package to share
            project_id: Target project

        Returns:
            True if shared successfully
        """
        if package_name not in self._installed_packages:
            raise ValueError(f"Package '{package_name}' is not installed")

        pkg_info = self._installed_packages[package_name]
        if not pkg_info.install_path or not pkg_info.install_path.exists():
            raise ValueError(f"Installed package path missing for '{package_name}'")

        logger.info("Sharing package %s with project %s", package_name, project_id)

        metadata = self._build_share_metadata(pkg_info, project_id)
        archive_name = self._build_share_archive_name(pkg_info)

        with tempfile.TemporaryDirectory(prefix="r4-hatch-share-") as temp_dir:
            archive_path = Path(temp_dir) / archive_name
            self._create_share_archive(pkg_info, project_id, archive_path)
            upload_result = await asyncio.to_thread(
                self.client.data.upload,
                str(archive_path),
                project_id=project_id,
                metadata=metadata,
            )

        if upload_result.status.lower() != "success":
            detail = upload_result.error or upload_result.status
            raise RuntimeError(f"Failed to share package '{package_name}': {detail}")

        logger.info(
            "Shared package %s with project %s as %s",
            package_name,
            project_id,
            upload_result.file_id,
        )

        return True

    def _build_share_archive_name(self, pkg_info: HatchPackageInfo) -> str:
        """Build a stable archive name for a shared package."""
        safe_name = pkg_info.name.replace("/", "-")
        safe_version = pkg_info.version.replace("/", "-")
        return f"{safe_name}-{safe_version}.zip"

    def _build_share_metadata(
        self,
        pkg_info: HatchPackageInfo,
        project_id: str,
    ) -> dict[str, Any]:
        """Build metadata for project-scoped shared package uploads."""
        return {
            "artifact_type": "hatch_package",
            "package_name": pkg_info.name,
            "package_version": pkg_info.version,
            "package_description": pkg_info.description,
            "package_source": pkg_info.source.value,
            "package_source_url": pkg_info.source_url or "",
            "package_tools": list(pkg_info.tools),
            "project_id": project_id,
        }

    def _create_share_archive(
        self,
        pkg_info: HatchPackageInfo,
        project_id: str,
        archive_path: Path,
    ) -> None:
        """Create a portable archive for project-scoped package sharing."""
        install_path = pkg_info.install_path
        if install_path is None:
            raise ValueError(f"Installed package path missing for '{pkg_info.name}'")

        share_metadata = {
            "shared_via": "r4.mcp.hatch.share",
            "project_id": project_id,
            "package": pkg_info.to_dict(),
        }
        root_dir = Path(pkg_info.name)
        excluded_names = {
            HATCH_RUNTIME_STATE_FILE,
            ".r4_hatch_runtime.log",
        }
        excluded_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            "node_modules",
        }

        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                str(root_dir / ".r4_share_metadata.json"),
                json.dumps(share_metadata, indent=2),
            )
            for path in install_path.rglob("*"):
                relative_path = path.relative_to(install_path)
                if relative_path.name in excluded_names:
                    continue
                if any(part in excluded_dirs for part in relative_path.parts):
                    continue
                if path.is_dir():
                    continue
                archive.write(path, str(root_dir / relative_path))


def create_package_template(
    package_name: str,
    output_dir: Optional[Path] = None,
    description: str = "",
) -> Path:
    """
    Create a new Hatch! MCP server package from template (A-703).

    Args:
        package_name: Package name
        output_dir: Output directory (default: current directory)
        description: Package description

    Returns:
        Path to created package
    """
    output_dir = output_dir or Path.cwd()
    pkg_dir = output_dir / package_name

    if pkg_dir.exists():
        raise ValueError(f"Directory already exists: {pkg_dir}")

    pkg_dir.mkdir(parents=True)

    # Create hatch.json manifest
    manifest = {
        "name": package_name,
        "version": "0.1.0",
        "description": description or f"Hatch! MCP server: {package_name}",
        "author": os.environ.get("USER", "unknown"),
        "license": "MIT",
        "tools": [],
        "dependencies": [],
    }

    with open(pkg_dir / "hatch.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Create Python MCP server template
    server_code = '''"""
{package_name} - Hatch! MCP Server

This MCP server provides tools for [description].
"""

from typing import Any

# MCP server implementation
# See: https://github.com/CrackingShells/Hatchling

class {class_name}Server:
    """MCP server for {package_name}."""

    def __init__(self) -> None:
        """Initialize the server."""
        self.tools: dict[str, callable] = {{
            "hello_world": self.hello_world,
        }}

    def hello_world(self, name: str = "World") -> dict[str, Any]:
        """
        A simple hello world tool.

        Args:
            name: Name to greet

        Returns:
            Greeting message
        """
        return {{"message": f"Hello, {{name}} from {package_name}!"}}

    def list_tools(self) -> list[dict[str, Any]]:
        """List available tools."""
        return [
            {{
                "name": "hello_world",
                "description": "A simple hello world tool",
                "parameters": {{
                    "type": "object",
                    "properties": {{
                        "name": {{
                            "type": "string",
                            "description": "Name to greet",
                            "default": "World",
                        }},
                    }},
                }},
            }},
        ]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Invoke a tool by name."""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {{tool_name}}")
        return self.tools[tool_name](**args)


# Entry point
def create_server():
    """Create and return an MCP server instance."""
    return {class_name}Server()


if __name__ == "__main__":
    server = create_server()
    print(f"Available tools: {{server.list_tools()}}")
    print(server.invoke("hello_world", {{"name": "Hatch"}}))
'''.format(
        package_name=package_name,
        class_name="".join(word.capitalize() for word in package_name.replace("-", "_").split("_")),
    )

    with open(pkg_dir / "server.py", "w") as f:
        f.write(server_code)

    # Create README
    readme = f"""# {package_name}

{description or "A Hatch! MCP server package."}

## Installation

```bash
r4 mcp hatch install ./{package_name}
```

## Usage

```bash
# Connect the package
r4 mcp hatch connect {package_name}

# List available tools
r4 mcp list --server hatch-{package_name}
```

## Tools

- `hello_world`: A simple hello world tool

## Development

```bash
# Test locally
python server.py
```

## License

MIT
"""

    with open(pkg_dir / "README.md", "w") as f:
        f.write(readme)

    # Create .gitignore
    gitignore = """__pycache__/
*.py[cod]
*$py.class
.env
.venv/
*.egg-info/
dist/
build/
"""

    with open(pkg_dir / ".gitignore", "w") as f:
        f.write(gitignore)

    return pkg_dir


# Verification function for package signatures (A-708)
@dataclass
class SignatureInfo:
    """Signature verification result."""

    is_valid: bool
    signer_id: Optional[str] = None
    timestamp: Optional[str] = None
    algorithm: Optional[str] = None
    error: Optional[str] = None


class SignatureVerifier:
    """
    Package signature verifier (A-708).

    Supports multiple signature formats:
    - GPG (.sig, .asc)
    - Minisign (.minisig)
    - ECDSA (.p256sig)
    """

    # Trusted public keys (admin-configured)
    TRUSTED_KEYS_DIR = Path(
        os.environ.get(
            "R4_TRUSTED_KEYS_DIR",
            os.path.expanduser("~/.config/r4/trusted_keys"),
        )
    )

    # Hatch-Registry official key ID
    REGISTRY_KEY_ID = os.environ.get(
        "HATCH_REGISTRY_KEY_ID",
        "hatch-registry@crackingshells.github.io",
    )

    def __init__(self, trusted_keys_dir: Optional[Path] = None):
        """Initialize verifier."""
        self.trusted_keys_dir = trusted_keys_dir or self.TRUSTED_KEYS_DIR
        self.trusted_keys_dir.mkdir(parents=True, exist_ok=True)

    async def verify(
        self,
        package_path: Path,
        signature_path: Optional[Path] = None,
    ) -> SignatureInfo:
        """
        Verify package signature (A-708).

        Args:
            package_path: Path to package directory
            signature_path: Path to signature file (auto-detected if None)

        Returns:
            SignatureInfo with verification result
        """
        # Find signature file
        sig_path = self._find_signature_file(package_path, signature_path)
        if not sig_path:
            return SignatureInfo(
                is_valid=False,
                error="No signature file found",
            )

        # Determine signature type and verify
        if sig_path.suffix in (".sig", ".asc"):
            return await self._verify_gpg(package_path, sig_path)
        elif sig_path.suffix == ".minisig":
            return await self._verify_minisign(package_path, sig_path)
        elif sig_path.suffix == ".p256sig":
            return await self._verify_ecdsa(package_path, sig_path)
        else:
            return SignatureInfo(
                is_valid=False,
                error=f"Unknown signature format: {sig_path.suffix}",
            )

    def _find_signature_file(
        self,
        package_path: Path,
        signature_path: Optional[Path],
    ) -> Optional[Path]:
        """Find signature file for package."""
        if signature_path and signature_path.exists():
            return signature_path

        # Check common locations
        candidates = [
            package_path / "signature.sig",
            package_path / "hatch.json.sig",
            package_path / "signature.asc",
            package_path / "signature.minisig",
            package_path / "hatch.json.minisig",
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None

    async def _verify_gpg(
        self,
        package_path: Path,
        sig_path: Path,
    ) -> SignatureInfo:
        """Verify GPG signature."""
        # Determine what was signed (manifest or package tarball)
        signed_file = package_path / "hatch.json"
        if not signed_file.exists():
            return SignatureInfo(
                is_valid=False,
                error="hatch.json not found for signature verification",
            )

        try:
            # Import trusted keys
            keyring = self.trusted_keys_dir / "gpg-keyring"

            proc = await asyncio.create_subprocess_exec(
                "gpg",
                "--homedir",
                str(keyring),
                "--verify",
                str(sig_path),
                str(signed_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                # Parse GPG output for signer info
                output = stderr.decode()
                signer_id = None
                for line in output.split("\n"):
                    if "Good signature from" in line:
                        # Extract key ID
                        start = line.find('"') + 1
                        end = line.rfind('"')
                        if start < end:
                            signer_id = line[start:end]
                        break

                return SignatureInfo(
                    is_valid=True,
                    signer_id=signer_id,
                    algorithm="GPG",
                )
            else:
                return SignatureInfo(
                    is_valid=False,
                    error=f"GPG verification failed: {stderr.decode()}",
                )

        except FileNotFoundError:
            return SignatureInfo(
                is_valid=False,
                error="gpg command not found. Install GnuPG for signature verification.",
            )
        except Exception as e:
            return SignatureInfo(
                is_valid=False,
                error=f"GPG verification error: {e}",
            )

    async def _verify_minisign(
        self,
        package_path: Path,
        sig_path: Path,
    ) -> SignatureInfo:
        """Verify minisign signature."""
        signed_file = package_path / "hatch.json"
        if not signed_file.exists():
            return SignatureInfo(
                is_valid=False,
                error="hatch.json not found for signature verification",
            )

        # Find public key
        pubkey_files = list(self.trusted_keys_dir.glob("*.pub"))
        if not pubkey_files:
            return SignatureInfo(
                is_valid=False,
                error="No trusted public keys found",
            )

        try:
            for pubkey in pubkey_files:
                proc = await asyncio.create_subprocess_exec(
                    "minisign",
                    "-Vm",
                    str(signed_file),
                    "-x",
                    str(sig_path),
                    "-p",
                    str(pubkey),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0:
                    return SignatureInfo(
                        is_valid=True,
                        signer_id=pubkey.stem,
                        algorithm="Ed25519 (minisign)",
                    )

            return SignatureInfo(
                is_valid=False,
                error="No matching trusted key for signature",
            )

        except FileNotFoundError:
            return SignatureInfo(
                is_valid=False,
                error="minisign command not found. Install minisign for signature verification.",
            )
        except Exception as e:
            return SignatureInfo(
                is_valid=False,
                error=f"Minisign verification error: {e}",
            )

    async def _verify_ecdsa(
        self,
        package_path: Path,
        sig_path: Path,
    ) -> SignatureInfo:
        """Verify ECDSA P-256 signature using Python cryptography."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            signed_file = package_path / "hatch.json"
            if not signed_file.exists():
                return SignatureInfo(
                    is_valid=False,
                    error="hatch.json not found for signature verification",
                )

            # Load signature
            with open(sig_path, "rb") as f:
                signature = f.read()

            # Load file content
            with open(signed_file, "rb") as f:
                data = f.read()

            # Try each trusted public key
            pubkey_files = list(self.trusted_keys_dir.glob("*.pem"))
            for pubkey_path in pubkey_files:
                try:
                    with open(pubkey_path, "rb") as f:
                        public_key = serialization.load_pem_public_key(f.read())

                    if isinstance(public_key, ec.EllipticCurvePublicKey):
                        public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
                        return SignatureInfo(
                            is_valid=True,
                            signer_id=pubkey_path.stem,
                            algorithm="ECDSA P-256",
                        )
                except Exception:
                    continue

            return SignatureInfo(
                is_valid=False,
                error="No matching trusted key for ECDSA signature",
            )

        except ImportError:
            return SignatureInfo(
                is_valid=False,
                error="cryptography package not installed for ECDSA verification",
            )
        except Exception as e:
            return SignatureInfo(
                is_valid=False,
                error=f"ECDSA verification error: {e}",
            )

    def add_trusted_key(
        self,
        key_path: Path,
        key_id: str,
    ) -> bool:
        """Add a trusted public key."""
        if not key_path.exists():
            return False

        dest = self.trusted_keys_dir / f"{key_id}{key_path.suffix}"
        shutil.copy(key_path, dest)
        logger.info(f"Added trusted key: {key_id}")
        return True


async def verify_package_signature(
    package_path: Path,
    signature_path: Optional[Path] = None,
) -> bool:
    """
    Verify package signature (A-708).

    Args:
        package_path: Path to package
        signature_path: Path to signature file

    Returns:
        True if signature is valid
    """
    verifier = SignatureVerifier()
    result = await verifier.verify(package_path, signature_path)

    if not result.is_valid:
        logger.warning(f"Signature verification failed: {result.error}")

    return result.is_valid
