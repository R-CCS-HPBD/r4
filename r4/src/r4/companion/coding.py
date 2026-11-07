"""Polyglot coding helpers for the companion.

The companion uses this module for local, interactive coding workflows. It
detects lightweight project context and chooses conservative compile, run, and
test commands for common research languages without creating a separate job or
workflow system.
"""

from __future__ import annotations

import atexit
import json
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r4.lsp import LSPManager

from .agent_workspace import current_workspace, resolve_cwd
from .agent_workspace import resolve as resolve_workspace_path
from .sandbox import SandboxError
from .tool_python import tool_python

LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
    "fortran": (".f", ".for", ".f90", ".f95", ".f03", ".f08"),
    "r": (".r",),
    "python": (".py",),
}

PRIMARY_SOURCE_EXTENSIONS = {
    suffix
    for suffixes in LANGUAGE_EXTENSIONS.values()
    for suffix in suffixes
    if suffix not in {".h", ".hh", ".hpp", ".hxx"}
}

BUILD_MARKERS = (
    "Makefile",
    "makefile",
    "CMakeLists.txt",
    "pyproject.toml",
    "setup.py",
    "pytest.ini",
    "tox.ini",
    "DESCRIPTION",
)

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

LSP_PRESET_LANGUAGE_MAP: dict[str, dict[str, str]] = {
    "python": {"preset": "python", "name": "pylsp", "command": "pylsp"},
    "c": {"preset": "c", "name": "clangd", "command": "clangd"},
    "cpp": {"preset": "c", "name": "clangd", "command": "clangd"},
}

_LSP_MANAGERS: dict[str, LSPManager] = {}


@dataclass(frozen=True)
class CodeCommand:
    """A command selected for a local coding operation."""

    argv: list[str]
    cwd: Path
    label: str
    timeout: int

    @property
    def shell_text(self) -> str:
        return shlex.join(self.argv)


def detect_code_context(path: str = ".", max_files: int = 80) -> dict[str, Any]:
    """Return a compact, JSON-serializable coding context snapshot."""
    subject = _resolve_existing_path(path)
    root = _find_project_root(subject)
    source_files = _collect_source_files(root, max_files=max_files)
    language_counts = _language_counts(source_files)
    build_systems = _detect_build_systems(root)
    make_targets = _make_targets(root)
    context = {
        "path": str(subject),
        "root": str(root),
        "languages": language_counts,
        "primary_language": _dominant_language(language_counts),
        "build_systems": build_systems,
        "make_targets": make_targets,
        "tools": _toolchain_status(),
        "language_servers": _detect_language_servers_for_languages(root, list(language_counts)),
        "source_files": [str(source.relative_to(root)) for source in source_files],
        "suggested_commands": _suggested_commands(
            root, language_counts, build_systems, make_targets
        ),
    }
    return context


def detect_code_context_text(path: str = ".") -> str:
    """Return code context as formatted JSON for tool and UI display."""
    return json.dumps(detect_code_context(path), indent=2, default=str)


def detect_language_servers(path: str = ".", *, language: str | None = None) -> dict[str, Any]:
    """Return language-server availability and runtime state for the workspace."""
    subject = _resolve_existing_path(path)
    root = _find_project_root(subject)
    if language:
        detected_languages = [_normalize_language(language)]
    else:
        detected_languages = list(_language_counts(_collect_source_files(root, max_files=120)))
    return _detect_language_servers_for_languages(root, detected_languages)


def detect_language_servers_text(path: str = ".", *, language: str | None = None) -> str:
    """Return language-server availability as formatted JSON."""
    return json.dumps(detect_language_servers(path, language=language), indent=2, default=str)


def start_language_servers(path: str = ".", *, language: str | None = None) -> str:
    """Start available language servers for the current workspace."""
    subject = _resolve_existing_path(path)
    root = _find_project_root(subject)
    report = detect_language_servers(str(root), language=language)
    servers = report["servers"]
    if not report["supported_languages"]:
        detected = ", ".join(report["detected_languages"]) or "none"
        return f"No supported language-server presets for: {detected}"

    manager = _lsp_manager_for_root(root, report["preset_languages"])
    started: list[str] = []
    already_running: list[str] = []
    unavailable: list[str] = []
    for server in servers:
        name = str(server["name"])
        if bool(server["running"]):
            already_running.append(name)
            continue
        if not bool(server["available"]):
            unavailable.append(name)
            continue
        if manager.start(name):
            started.append(name)

    refreshed = _detect_language_servers_for_languages(root, report["detected_languages"])
    lines = ["Language servers", f"Root: {root}"]
    if started:
        lines.append(f"Started: {', '.join(sorted(dict.fromkeys(started)))}")
    if already_running:
        lines.append(f"Already running: {', '.join(sorted(dict.fromkeys(already_running)))}")
    if unavailable:
        lines.append(f"Unavailable: {', '.join(sorted(dict.fromkeys(unavailable)))}")
    active = [server["name"] for server in refreshed["servers"] if server["running"]]
    if active:
        lines.append(f"Active: {', '.join(sorted(dict.fromkeys(active)))}")
    return "\n".join(lines)


def compile_code(
    path: str = ".",
    *,
    language: str | None = None,
    source_path: str | None = None,
    target: str | None = None,
    output_path: str | None = None,
    timeout: int = 120,
) -> str:
    """Compile or syntax-check code using the best local command detected."""
    subject = _resolve_existing_path(source_path or path)
    root = _find_project_root(subject)
    selected_language = _normalize_language(language) or _language_for_subject(subject)
    source = _source_file(subject, root, selected_language)
    steps = _compile_steps(
        root=root,
        source=source,
        language=selected_language,
        target=target,
        output_path=output_path,
        timeout=timeout,
    )
    return _execute_steps("compile", steps)


def run_code(
    path: str = ".",
    *,
    language: str | None = None,
    source_path: str | None = None,
    executable: str | None = None,
    args: str | None = None,
    timeout: int = 60,
) -> str:
    """Run code using a detected interpreter, executable, or build target."""
    subject = _resolve_existing_path(source_path or executable or path)
    root = _find_project_root(subject)
    selected_language = _normalize_language(language) or _language_for_subject(subject)
    source = _source_file(subject, root, selected_language) if not executable else None
    steps = _run_steps(
        root=root,
        source=source,
        language=selected_language,
        executable=executable,
        args=args,
        timeout=timeout,
    )
    return _execute_steps("run", steps)


def test_code(
    path: str = ".",
    *,
    language: str | None = None,
    target: str | None = None,
    timeout: int = 180,
) -> str:
    """Run the most likely local test command for a codebase."""
    subject = _resolve_existing_path(path)
    root = _find_project_root(subject)
    selected_language = _normalize_language(language) or _dominant_language(
        _language_counts(_collect_source_files(root, max_files=120))
    )
    steps = _test_steps(root=root, language=selected_language, target=target, timeout=timeout)
    return _execute_steps("test", steps)


def code_plan(path: str = ".") -> str:
    """Return a concise local coding workbench plan for slash commands."""
    context = detect_code_context(path)
    lines = [
        "Code workbench",
        f"Root: {context['root']}",
        f"Primary language: {context.get('primary_language') or 'unknown'}",
    ]
    if context["languages"]:
        languages = ", ".join(f"{name}={count}" for name, count in context["languages"].items())
        lines.append(f"Languages: {languages}")
    if context["build_systems"]:
        lines.append(f"Build systems: {', '.join(context['build_systems'])}")
    if context["make_targets"]:
        lines.append(f"Make targets: {', '.join(context['make_targets'][:10])}")
    lsp = context["language_servers"]
    if lsp["servers"]:
        labels = []
        for server in lsp["servers"]:
            state = "running" if server["running"] else "available"
            labels.append(f"{server['name']} ({'/'.join(server['languages'])}, {state})")
        lines.append(f"Language servers: {', '.join(labels)}")
    elif lsp["supported_languages"]:
        lines.append("Language servers: supported, but no server binaries detected")
    lines.append("Suggested commands:")
    for command in context["suggested_commands"]:
        lines.append(f"  {command}")
    return "\n".join(lines)


@atexit.register
def _cleanup_lsp_managers() -> None:
    for manager in _LSP_MANAGERS.values():
        manager.stop_all()


def unconfined_language_servers() -> list[str]:
    """Language servers still running that were started with the sandbox off (ADR-101)."""
    return [
        f"language server {name} (root {root})"
        for root, manager in sorted(_LSP_MANAGERS.items())
        for name in manager.unconfined()
    ]


def _resolve_existing_path(path: str) -> Path:
    """``path`` resolved like a tool read: a relative path joins the agent's
    workspace root when one is set (ADR-092 slice 5), so the step's cwd lies in
    the workspace that :func:`_run_command` holds it to."""
    subject = resolve_workspace_path(path or ".", write=False)
    if not subject.exists():
        raise FileNotFoundError(f"path not found: {path}")
    return subject.resolve()


def _is_project_root(candidate: Path) -> bool:
    if any((candidate / marker).exists() for marker in BUILD_MARKERS):
        return True
    return (candidate / ".git").exists()


def _find_project_root(subject: Path) -> Path:
    """The nearest directory with a build marker or ``.git``, else the subject's.

    With an agent workspace set the search stops at its root: a marker above
    it would put the step's cwd outside the workspace, where
    :func:`_run_command` refuses it (2026-10-30, review OFF-1).
    """
    current = subject if subject.is_dir() else subject.parent
    workspace = current_workspace()
    stop = workspace.root if workspace is not None else None
    for candidate in (current, *current.parents):
        if _is_project_root(candidate):
            return candidate
        if candidate == stop:
            break
    return current


def _collect_source_files(root: Path, *, max_files: int) -> list[Path]:
    matches: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in IGNORED_DIRS and not directory.startswith(".")
        ]
        current_path = Path(current)
        for filename in sorted(files):
            suffix = Path(filename).suffix.lower()
            if suffix in PRIMARY_SOURCE_EXTENSIONS:
                matches.append(current_path / filename)
                if len(matches) >= max_files:
                    return matches
    return matches


def _language_counts(source_files: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in source_files:
        language = _language_from_extension(source.suffix)
        if language:
            counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _dominant_language(counts: dict[str, int]) -> str | None:
    return next(iter(counts), None)


def _detect_build_systems(root: Path) -> list[str]:
    systems: list[str] = []
    if (root / "Makefile").exists() or (root / "makefile").exists():
        systems.append("make")
    if (root / "CMakeLists.txt").exists():
        systems.append("cmake")
    if (
        (root / "pyproject.toml").exists()
        or (root / "setup.py").exists()
        or (root / "pytest.ini").exists()
    ):
        systems.append("python")
    if (root / "DESCRIPTION").exists() or (root / "tests" / "testthat").exists():
        systems.append("r")
    return systems


def _make_targets(root: Path) -> list[str]:
    makefile = root / "Makefile"
    if not makefile.exists():
        makefile = root / "makefile"
    if not makefile.exists():
        return []

    targets: list[str] = []
    try:
        lines = makefile.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line or line.startswith(("\t", " ", "#", ".")):
            continue
        head, separator, _tail = line.partition(":")
        if not separator or "=" in head:
            continue
        for target in head.split():
            if target and not target.startswith("%") and target not in targets:
                targets.append(target)
    return targets[:40]


def _toolchain_status() -> dict[str, bool]:
    tools = ("make", "cmake", "ctest", "gcc", "g++", "gfortran", "Rscript", "pytest")
    return {tool: shutil.which(tool) is not None for tool in tools}


def _detect_language_servers_for_languages(
    root: Path, languages: list[str | None]
) -> dict[str, Any]:
    detected_languages = [language for language in languages if language]
    supported_languages = [
        language for language in detected_languages if language in LSP_PRESET_LANGUAGE_MAP
    ]
    unsupported_languages = [
        language for language in detected_languages if language not in LSP_PRESET_LANGUAGE_MAP
    ]
    preset_languages = sorted(
        {LSP_PRESET_LANGUAGE_MAP[language]["preset"] for language in supported_languages}
    )
    manager = _lsp_manager_for_root(root, preset_languages) if preset_languages else None
    statuses = {status.name: status for status in manager.status()} if manager else {}

    servers_by_name: dict[str, dict[str, Any]] = {}
    for language in supported_languages:
        preset = LSP_PRESET_LANGUAGE_MAP[language]
        name = preset["name"]
        status = statuses.get(name)
        entry = servers_by_name.setdefault(
            name,
            {
                "name": name,
                "languages": [],
                "command": preset["command"],
                "available": shutil.which(preset["command"]) is not None,
                "running": bool(status.running) if status else False,
                "pid": status.pid if status and status.running else None,
            },
        )
        entry["languages"].append(language)

    servers = [
        {
            **server,
            "languages": sorted(server["languages"]),
        }
        for server in servers_by_name.values()
    ]
    servers.sort(key=lambda server: server["name"])
    return {
        "root": str(root),
        "detected_languages": sorted(dict.fromkeys(detected_languages)),
        "supported_languages": sorted(dict.fromkeys(supported_languages)),
        "unsupported_languages": sorted(dict.fromkeys(unsupported_languages)),
        "preset_languages": preset_languages,
        "servers": servers,
        "semantic_ready": any(server["running"] for server in servers),
    }


def _lsp_manager_for_root(root: Path, preset_languages: list[str]) -> LSPManager:
    key = str(root)
    manager = _LSP_MANAGERS.get(key)
    if manager is None:
        manager = LSPManager()
        _LSP_MANAGERS[key] = manager
    if preset_languages:
        manager.load_presets(preset_languages)
    return manager


def _suggested_commands(
    root: Path,
    language_counts: dict[str, int],
    build_systems: list[str],
    make_targets: list[str],
) -> list[str]:
    commands = _build_system_commands(build_systems, make_targets)
    if language_counts.get("python"):
        commands.append(f"{shlex.quote(tool_python())} -m pytest")
    if language_counts.get("r"):
        commands.append("Rscript -e \"testthat::test_dir('tests/testthat')\"")
    if not commands:
        commands.extend(_fallback_commands(root, language_counts))
    return commands


def _build_system_commands(build_systems: list[str], make_targets: list[str]) -> list[str]:
    commands: list[str] = []
    if "make" in build_systems:
        commands.append("make")
        commands.extend(f"make {t}" for t in ("test", "check", "run") if t in make_targets)
    if "cmake" in build_systems:
        commands.append("cmake -S . -B build")
        commands.append("cmake --build build")
        commands.append("ctest --test-dir build")
    return commands


def _fallback_commands(root: Path, language_counts: dict[str, int]) -> list[str]:
    primary = _dominant_language(language_counts)
    source = _source_file(root, root, primary) if primary else None
    return [_fallback_compile_command(root, source, primary)] if source else []


def _normalize_language(language: str | None) -> str | None:
    if not language:
        return None
    normalized = language.strip().lower()
    aliases = {
        "c++": "cpp",
        "cxx": "cpp",
        "fortran90": "fortran",
        "f90": "fortran",
        "py": "python",
    }
    return aliases.get(normalized, normalized)


def _language_from_extension(suffix: str) -> str | None:
    normalized = suffix.lower()
    if normalized == ".r":
        return "r"
    for language, suffixes in LANGUAGE_EXTENSIONS.items():
        if normalized in suffixes:
            return language
    return None


def _language_for_subject(subject: Path) -> str | None:
    if subject.is_file():
        return _language_from_extension(subject.suffix)
    source_files = _collect_source_files(subject, max_files=120)
    return _dominant_language(_language_counts(source_files))


def _source_file(subject: Path, root: Path, language: str | None) -> Path | None:
    if subject.is_file():
        return subject
    source_files = _collect_source_files(root, max_files=120)
    if language:
        for source in source_files:
            if _language_from_extension(source.suffix) == language:
                return source
    return source_files[0] if source_files else None


def _compile_steps(
    *,
    root: Path,
    source: Path | None,
    language: str | None,
    target: str | None,
    output_path: str | None,
    timeout: int,
) -> list[CodeCommand]:
    _make_targets(root)  # validate/cache targets
    build_steps = _build_system_compile_steps(root, target, timeout)
    if build_steps is not None:
        return build_steps
    if source is None:
        raise ValueError("no source file or build system detected")
    language = language or _language_from_extension(source.suffix)
    return _source_compile_steps(root, source, language, output_path, timeout)


def _build_system_compile_steps(
    root: Path, target: str | None, timeout: int
) -> list[CodeCommand] | None:
    """make / cmake build steps, or None when the root has neither."""
    if (root / "Makefile").exists() or (root / "makefile").exists():
        argv = ["make"] + ([target] if target else [])
        return [CodeCommand(argv=argv, cwd=root, label="make build", timeout=timeout)]
    if (root / "CMakeLists.txt").exists():
        return _cmake_steps(root, target, timeout)
    return None


def _cmake_steps(root: Path, target: str | None, timeout: int) -> list[CodeCommand]:
    steps: list[CodeCommand] = []
    if not (root / "build").exists():
        steps.append(
            CodeCommand(["cmake", "-S", ".", "-B", "build"], root, "cmake configure", timeout)
        )
    build_argv = ["cmake", "--build", "build"]
    if target:
        build_argv.extend(["--target", target])
    steps.append(CodeCommand(build_argv, root, "cmake build", timeout))
    return steps


def _source_compile_steps(
    root: Path, source: Path, language: str | None, output_path: str | None, timeout: int
) -> list[CodeCommand]:
    """Syntax-check or compile one source file."""
    if language == "python":
        argv = [tool_python(), "-m", "py_compile", str(source)]
        return [CodeCommand(argv, root, "python syntax", timeout)]
    if language == "r":
        expression = f"parse(file={json.dumps(str(source))})"
        return [CodeCommand(["Rscript", "-e", expression], root, "r parse", timeout)]
    compiler = {"c": "gcc", "cpp": "g++", "fortran": "gfortran"}.get(language or "")
    if compiler is None:
        raise ValueError(f"unsupported language for compile: {language or 'unknown'}")
    if shutil.which(compiler) is None:
        raise FileNotFoundError(f"compiler not found: {compiler}")
    output = _default_executable_path(root, source, output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return [
        CodeCommand(
            [compiler, str(source), "-o", str(output)], root, f"{language} compile", timeout
        )
    ]


def _run_steps(
    *,
    root: Path,
    source: Path | None,
    language: str | None,
    executable: str | None,
    args: str | None,
    timeout: int,
) -> list[CodeCommand]:
    split_args = shlex.split(args or "")
    if executable:
        argv = [str(Path(executable).expanduser()), *split_args]
        return [CodeCommand(argv, root, "run executable", timeout)]
    if source is None:
        return _make_run_steps(root, split_args, timeout)
    language = language or _language_from_extension(source.suffix)
    return _source_run_steps(root, source, language, split_args, timeout)


def _make_run_steps(root: Path, split_args: list[str], timeout: int) -> list[CodeCommand]:
    if "run" in _make_targets(root):
        return [CodeCommand(["make", "run", *split_args], root, "make run", timeout)]
    raise ValueError("no runnable source file or executable detected")


def _source_run_steps(
    root: Path, source: Path, language: str | None, split_args: list[str], timeout: int
) -> list[CodeCommand]:
    if language == "python":
        argv = [tool_python(), str(source), *split_args]
        return [CodeCommand(argv, root, "python run", timeout)]
    if language == "r":
        return [CodeCommand(["Rscript", str(source), *split_args], root, "r run", timeout)]
    if language in {"c", "cpp", "fortran"}:
        return _compiled_run_steps(root, source, language, split_args, timeout)
    raise ValueError(f"unsupported language for run: {language or 'unknown'}")


def _compiled_run_steps(
    root: Path, source: Path, language: str, split_args: list[str], timeout: int
) -> list[CodeCommand]:
    executable_path = _default_executable_path(root, source, None)
    steps: list[CodeCommand] = []
    if not executable_path.exists():
        steps.extend(
            _compile_steps(
                root=root,
                source=source,
                language=language,
                target=None,
                output_path=None,
                timeout=timeout,
            )
        )
    steps.append(
        CodeCommand([str(executable_path), *split_args], root, "run compiled binary", timeout)
    )
    return steps


def _test_steps(
    *,
    root: Path,
    language: str | None,
    target: str | None,
    timeout: int,
) -> list[CodeCommand]:
    build_steps = _build_system_test_steps(root, target, timeout)
    if build_steps:
        return build_steps
    if language == "python" or (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        return [CodeCommand([tool_python(), "-m", "pytest"], root, "pytest", timeout)]
    if language == "r" or (root / "tests" / "testthat").exists():
        expression = (
            "if (requireNamespace('testthat', quietly=TRUE)) "
            "testthat::test_dir('tests/testthat') else quit(status=2)"
        )
        return [CodeCommand(["Rscript", "-e", expression], root, "r testthat", timeout)]
    raise ValueError("no local test command detected")


def _build_system_test_steps(root: Path, target: str | None, timeout: int) -> list[CodeCommand]:
    """make test/check, else ctest on an existing build dir, else nothing."""
    if (root / "Makefile").exists() or (root / "makefile").exists():
        selected_target = target or _first_present(_make_targets(root), ("test", "check"))
        if selected_target:
            return [CodeCommand(["make", selected_target], root, "make test", timeout)]
    if (root / "build").exists() and shutil.which("ctest"):
        return [
            CodeCommand(
                ["ctest", "--test-dir", "build", "--output-on-failure"], root, "ctest", timeout
            )
        ]
    return []


def _first_present(values: list[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in values:
            return candidate
    return None


def _default_executable_path(root: Path, source: Path, output_path: str | None) -> Path:
    if output_path:
        output = Path(output_path).expanduser()
        return output if output.is_absolute() else (root / output)
    return root / ".r4-companion" / "bin" / source.stem


def _fallback_compile_command(root: Path, source: Path, language: str | None) -> str:
    if language == "python":
        return (
            f"{shlex.quote(tool_python())} -m py_compile "
            f"{shlex.quote(str(source.relative_to(root)))}"
        )
    if language == "r":
        return "Rscript -e " + shlex.quote(
            "parse(file=" + json.dumps(str(source.relative_to(root))) + ")"
        )
    compiler = {"c": "gcc", "cpp": "g++", "fortran": "gfortran"}.get(language or "", "cc")
    output = Path(".r4-companion") / "bin" / source.stem
    return f"{compiler} {shlex.quote(str(source.relative_to(root)))} -o {shlex.quote(str(output))}"


def _execute_steps(operation: str, steps: list[CodeCommand]) -> str:
    if not steps:
        return f"No {operation} command selected."
    sections = [f"Code {operation}"]
    for step in steps:
        sections.append(f"\n[{step.label}]")
        sections.append(f"cwd: {step.cwd}")
        sections.append(f"$ {step.shell_text}")
        result_text, returncode = _run_command(step)
        sections.append(result_text)
        if returncode != 0:
            sections.append(f"Stopped after {step.label} failed.")
            break
    return "\n".join(part for part in sections if part)


def _run_command(command: CodeCommand) -> tuple[str, int]:
    """Run one step like ``shell_exec`` runs a command (ADR-101 D-4).

    The scrubbed tool env, its own process group killed whole on timeout, the
    cwd held to the agent's workspace (ADR-092 slice 5), and the local tool
    sandbox when it is on. :class:`SandboxError` propagates: the tool result
    is that error, and nothing runs unconfined.
    """
    from .tools import _run_captured, _tool_spawn_env

    try:
        cwd = resolve_cwd(str(command.cwd)) or str(command.cwd)
        outcome = _run_captured(
            command.argv, shell=False, cwd=cwd, timeout=command.timeout, env=_tool_spawn_env()
        )
    except SandboxError:
        raise
    except FileNotFoundError as exc:
        return f"Error: executable not found: {exc.filename}", 127
    except Exception as exc:
        return f"Error: {exc}", 1

    output = _combine_output(outcome.stdout, outcome.stderr)
    if outcome.timed_out:
        return f"{output}\nError: timed out after {command.timeout}s".strip(), 124
    returncode = outcome.returncode if outcome.returncode is not None else 1
    if returncode != 0:
        output = f"{output}\n[exit code: {returncode}]".strip()
    return output or "(no output)", returncode


def _combine_output(stdout: str, stderr: str) -> str:
    output = stdout or ""
    if stderr:
        output += ("\n[stderr]\n" if output else "[stderr]\n") + stderr
    if len(output) > 12000:
        output = output[:6000] + "\n...[truncated]...\n" + output[-3000:]
    return output
