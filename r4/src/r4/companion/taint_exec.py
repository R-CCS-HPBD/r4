"""What an exec call fetched, and where it put it (ADR-039 taint producer side).

The taint registry (:mod:`r4.companion.taint`) classifies a tool result by
the tool's name, except for the calls that run local code: their result is
``web`` content when the call reads the network. This module decides that,
and follows downloaded data to disk so a later read of it is web content too:

- :func:`exec_fetches_network`: the program text of a ``shell_exec`` /
  ``python_exec`` call, or the local script a call runs (``python fetch.py``,
  ``bash get.sh``, ``make``, ``run_code`` with ``path=fetch.py``), reads the
  network. The scan is bounded (the first and last 64 KB of a program, the
  first 256 KB of a script) and every pattern is linear.
- :func:`write_targets`: the paths a network-reading call wrote (redirects,
  ``tee``, ``-o`` / ``-O`` / ``-P`` / ``-OutFile`` / ``--local-dir`` targets,
  the ``git clone`` directory, the URL basename ``wget`` / ``curl -O`` save
  under, path-like string literals of python code).
- :func:`written_fetch_scripts`: a ``file_write`` / ``file_edit`` /
  ``apply_patch`` whose new text reads the network names a script whose
  later output is web content.
- :func:`read_paths` / :func:`searched_dirs`: the paths a later call reads,
  matched by the registry against what it recorded.

Deliberately broad (ADR-039): a false positive costs one extra approval
prompt when the agent reuses that output; a false negative lets fetched text
steer a later call unasked. Paths resolve against the call's working
directory and every ``cd`` / ``-C`` / ``chdir`` target it names; a recorded
file also matches by its basename.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

from .agent_workspace import WorkspaceEscape, resolve_cwd

#: The argument holding the program of each exec tool (permission_rules).
EXEC_PROGRAM_ARGS: dict[str, str] = {"shell_exec": "command", "python_exec": "code"}
#: Tools that run a local script named by these arguments, with no program text.
SCRIPT_RUN_ARGS: dict[str, tuple[str, ...]] = {
    "run_code": ("path", "source_path", "executable"),
}
#: Tools whose result shows the content of the paths their arguments name.
READER_TOOLS = frozenset(
    {"file_read", "grep_search", "shell_exec", "python_exec", "run_code", "test_code"}
    | {"compile_code", "lab_file_read"}
)
#: Writers: tool -> (path argument, new-text argument).
WRITER_ARGS: dict[str, tuple[str, str]] = {
    "file_write": ("path", "content"),
    "file_edit": ("path", "new_text"),
}

_SCAN_EDGE_CHARS = 64 * 1024
_SCRIPT_READ_CAP = 256 * 1024
_MAX_TOKENS = 4096
_MAX_SCRIPTS = 32
_MIN_BASENAME_LEN = 5

_NETWORK_FETCH_RE = re.compile(
    r"(?:https?|ftps?|s3|gs|hf|ssh|rsync)://"
    r"|\b(?:curl|wget|wget2|aria2c|httpie|lynx|w3m|links|elinks|scp|sftp|ftp|tftp"
    r"|nc|ncat|netcat|socat|rclone|gsutil|svn|hg|axel|yt-dlp|youtube-dl)\b"
    # Bounded gap: an unbounded lazy gap was quadratic on 'git git git ...'.
    r"|\bgit\b[^|;&\n]{0,200}?\s(?:clone|fetch|pull|ls-remote|submodule|archive\s+--remote)\b"
    r"|\bpip\d*(?:\.\d+)?\s+(?:download|install)\b|\buv\s+(?:pip|add|sync|run\s+--with)\b"
    r"|\b(?:conda|mamba|micromamba|npm|pnpm|yarn|cargo|go)\s+(?:install|get|add|fetch)\b"
    r"|\b(?:huggingface-cli|hf)\s+download\b|\baws\s+s3\b|\bgh\s+(?:api|repo\s+clone|release)\b"
    r"|\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer|iwr|irm)\b"
    r"|\bNet\.WebClient\b|\bDownload(?:String|File|Data)\b"
    r"|\b(?:urllib\d?|urllib3|requests|httpx|aiohttp|pycurl|httplib2|ftplib|paramiko"
    r"|boto3|botocore|huggingface_hub|websockets?|socket|fsspec|gdown|wget)\b"
    r"|\bhttp\.client\b|\burl(?:open|retrieve)\b|\bload_dataset\b|\bfrom_pretrained\b"
    # Generic verbs: download.file (R), download() (Julia), nltk.download,
    # hf_hub_download, Entrez.efetch, fetch_openml; not DB-cursor fetchone/all/many.
    r"|\b\w*download\w*|\b(?!fetch(?:one|all|many)\b)\w*fetch\w*"
    r"|\b(?:Entrez|pooch|astroquery|kaggle|zenodo_get|gget|tfds|tensorflow_datasets"
    r"|openml|selenium|playwright|mechanize|scrapy|webbrowser|fasterq-dump|fastq-dump)\b"
    r"|\b(?:docker|podman|singularity|apptainer|enroot|dvc|datalad|git-annex|oras|skopeo"
    r"|ollama)\s+(?:pull|get|fetch|clone|import|copy|install)\b"
    r"|\bHTTP\.(?:get|post|request|open)\b|\burl\s*\(",
    re.IGNORECASE,
)
#: A reader handed a variable: the URL may be in it (``pd.read_csv(u)``).
#: Searched in the text as written (flattening drops the quotes it looks for).
_VARIABLE_READER_RE = re.compile(
    r"\bread_(?:csv|json|html|xml|parquet|excel|table|fwf|feather|pickle|sas|stata|orc|hdf)"
    r"\s*\((?!\s*[rRbBuU]?[\"'])"
)
#: Code that decodes and then runs what it decoded cannot be classified.
_DECODE_RE = re.compile(
    r"\b(?:b64decode|b32decode|b16decode|a85decode|b85decode|decodebytes|unhexlify|fromhex"
    r"|decompress|rot13)\b|\bcodecs\.decode\b|\bbase64\s+(?:-d|-D|--decode)\b",
    re.IGNORECASE,
)
_DYNAMIC_EXEC_RE = re.compile(
    r"\b(?:exec|eval|__import__|import_module|getattr)\s*\(|(?<!re\.)\bcompile\s*\("
    r"|\beval\b|\bsource\b"
    r"|\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|\bsh\s+-c\b",
    re.IGNORECASE,
)
#: In-token obfuscation (``cu"rl"``, ``c\url``, ``'req' + 'uests'``).
_TOKEN_SPLIT_RE = re.compile(r"[\"'`\\^]|\s*\+\s*")

_WORD = r"[^\s;|&<>()\[\]{}'\"`,]+"
_TOKEN_RE = re.compile(r"[^\s;|&<>()\[\]{}'\"`,=]+")
_REDIRECT_RE = re.compile(r"(?:^|[^<>&\d-])(?:\d|&)?>>?\|?\s*(" + _WORD + ")")
_TEE_RE = re.compile(r"\btee\b(?:\s+-\S+)*\s+(" + _WORD + ")")
_OUTPUT_FLAG_RE = re.compile(
    r"(?:^|\s)(?:-o|-O|-P|-d|-C|--output|--output-document|--output-dir|--directory-prefix"
    r"|--dir|--dest|--destination|--local-dir|--target-dir|--cache-dir|--out|-OutFile"
    r"|-Destination)(?:\s*=\s*|\s+)['\"]?(" + _WORD + ")",
    re.IGNORECASE,
)
_COPY_DEST_RE = re.compile(r"\b(?:cp|mv|ln|install|rsync)\b[^;|&\n]{0,400}?\s(" + _WORD + r")\s*$")
#: Simple commands of a shell line (``;``, ``|``, ``&``, newline).
_SEGMENT_RE = re.compile(r"[;|&\n]+")
_CLONE_RE = re.compile(r"\b(?:git\s+clone|gh\s+repo\s+clone)\b([^;|&\n]{0,600})")
#: The repository argument of a clone (a URL, ``host:path``, ``owner/repo``).
_REPO_RE = re.compile(r"[/:]")
_URL_RE = re.compile(
    r"(?:https?|ftps?|s3|gs|hf)://[^\s'\"`<>|;()]+|\b[\w-]+(?:\.[\w-]+)+/[^\s'\"`<>|;()]*"
)
_CD_RE = re.compile(r"(?:\b(?:cd|pushd)\s+|(?:^|\s)-C\s+)['\"]?(" + _WORD + ")")
_CHDIR_RE = re.compile(r"\bchdir\s*\(\s*[rRbBuU]?['\"]([^'\"\n]+)['\"]")
_PY_LITERAL_RE = re.compile(r"(['\"])([^'\"\s]{1,260})\1")
_PY_DIR_KWARG_RE = re.compile(
    r"\b(?:local_dir|cache_dir|output_dir|out_dir|outdir|save_dir|data_dir|target_dir|root"
    r"|dest|destination|out|filename|path)\s*=\s*[rRbBuU]?['\"]([^'\"\n]+)['\"]"
)
_PATH_LIKE_RE = re.compile(r"/|\.\w{1,8}$")
_SCRIPT_RE = re.compile(r"\.(?:py|sh|bash|zsh|ksh|R|r|jl|pl|rb|js|mjs|cjs|ts|ps1|lua|php)$")
_MAKE_RE = re.compile(r"\b(?:make|gmake)\b")
_PATCH_PATH_RE = re.compile(
    r"^(?:\*\*\* (?:Add File|Update File|Move to):|\+\+\+ (?:b/)?)\s*(\S+)", re.MULTILINE
)
_RECURSIVE_SEARCH_RE = re.compile(
    r"\bgrep\s(?:[^|;&\n]{0,80}?\s)?-\w*[rR]|--recursive\b|\b(?:rg|ag|ack|git\s+grep)\b"
)
_NOT_PATHS = frozenset({"-", "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "&1", "&2"})


def bounded(text: str) -> str:
    """The first and last 64 KB of a long program (each scan stays linear)."""
    if len(text) <= 2 * _SCAN_EDGE_CHARS:
        return text
    return text[:_SCAN_EDGE_CHARS] + "\n" + text[-_SCAN_EDGE_CHARS:]


def text_fetches_network(text: str) -> bool:
    """True when program text reads the network, as written or with in-token
    obfuscation removed, or decodes and runs what it decoded."""
    text = bounded(text)
    flattened = _TOKEN_SPLIT_RE.sub("", text)
    if _NETWORK_FETCH_RE.search(text) or _NETWORK_FETCH_RE.search(flattened):
        return True
    if _VARIABLE_READER_RE.search(text):
        return True
    return bool(_DECODE_RE.search(flattened) and _DYNAMIC_EXEC_RE.search(flattened))


def program_of(tool_name: str, arguments: Any) -> str:
    """The program text of an exec call, or '' (another tool, no program)."""
    key = EXEC_PROGRAM_ARGS.get(tool_name)
    program = arguments.get(key) if key and isinstance(arguments, dict) else None
    return bounded(program) if isinstance(program, str) else ""


def exec_fetches_network(tool_name: str, arguments: Any) -> bool:
    """True when an exec call reads the network: its program text does, or a
    local script it runs does (read bounded, regular files only)."""
    program = program_of(tool_name, arguments)
    if program.strip() and text_fetches_network(program):
        return True
    return any(_script_fetches(path) for path in _scripts_run(tool_name, arguments, program))


def write_targets(tool_name: str, arguments: Any) -> set[str]:
    """Resolved paths an exec call may have written its (fetched) data to."""
    program = program_of(tool_name, arguments)
    if not program:
        return set()
    if tool_name == "python_exec":
        tokens = _python_targets(program)
    else:
        tokens = _shell_targets(program)
    return _resolve_all(tokens, _dirs(tool_name, arguments, program), record=True)


def written_fetch_scripts(tool_name: str, arguments: Any) -> set[str]:
    """Paths a writer gave new text that reads the network (a fetch script)."""
    if not isinstance(arguments, dict):
        return set()
    if tool_name == "apply_patch":
        text = arguments.get("patch_text")
        if not isinstance(text, str) or not text_fetches_network(text):
            return set()
        paths = _PATCH_PATH_RE.findall(bounded(text))
        base = _base_dir(arguments.get("workdir"))
        return _resolve_all(paths, [base], record=True)
    path_key, text_key = WRITER_ARGS.get(tool_name, ("", ""))
    path, text = arguments.get(path_key), arguments.get(text_key)
    if not isinstance(path, str) or not isinstance(text, str) or not text_fetches_network(text):
        return set()
    return _resolve_all([path], [_base_dir(None)], record=True)


def read_paths(tool_name: str, arguments: Any) -> set[str]:
    """Resolved paths a reader call names (every path-like token of its strings)."""
    if tool_name not in READER_TOOLS:
        return set()
    tokens = [token for text in _strings(arguments) for token in _TOKEN_RE.findall(bounded(text))]
    return _resolve_all(tokens[:_MAX_TOKENS], _dirs(tool_name, arguments, _all_text(arguments)))


def searched_dirs(tool_name: str, arguments: Any) -> set[str]:
    """Directories a recursive search reads everything beneath (grep_search,
    ``grep -r`` / ``rg`` in a shell): its working directory and named paths."""
    program = program_of(tool_name, arguments)
    if tool_name != "grep_search" and not _RECURSIVE_SEARCH_RE.search(program):
        return set()
    dirs = _dirs(tool_name, arguments, program)
    named = read_paths(tool_name, arguments) if tool_name != "grep_search" else set()
    path = arguments.get("path") if isinstance(arguments, dict) else None
    if isinstance(path, str):
        named |= _resolve_all([path], dirs)
    return named | set(dirs)


def names_recorded(paths: Iterable[str], recorded: Iterable[str]) -> bool:
    """True when a read path is a recorded path, lies under one, or shares a
    recorded file's basename."""
    recorded_set = set(recorded)
    if not recorded_set:
        return False
    basenames = {os.path.basename(p) for p in recorded_set if _file_like(os.path.basename(p))}
    for path in paths:
        if path in recorded_set or os.path.basename(path) in basenames:
            return True
        if any(parent in recorded_set for parent in _parents(path)):
            return True
    return False


def holds_recorded(dirs: Iterable[str], recorded: Iterable[str]) -> bool:
    """True when a recorded path lies beneath one of ``dirs``."""
    prefixes = tuple(d.rstrip("/") + "/" for d in dirs)
    return bool(prefixes) and any(p.startswith(prefixes) for p in recorded)


# -- helpers ------------------------------------------------------------------


def _shell_targets(command: str) -> list[str]:
    """Write targets named in a shell command."""
    found: list[str] = []
    for part in _SEGMENT_RE.split(command):
        found += _REDIRECT_RE.findall(part) + _TEE_RE.findall(part)
        found += _OUTPUT_FLAG_RE.findall(part) + _COPY_DEST_RE.findall(part)
    return found + _clone_targets(command) + _url_basenames(command)


def _python_targets(code: str) -> list[str]:
    """Write targets of python code: path-like literals, directory keyword
    arguments, and a subprocess argument list's output flags."""
    literals = [value for _q, value in _PY_LITERAL_RE.findall(code) if _PATH_LIKE_RE.search(value)]
    as_argv = _TOKEN_SPLIT_RE.sub("", code).replace(",", " ")
    flags = _OUTPUT_FLAG_RE.findall(as_argv)
    return literals + _PY_DIR_KWARG_RE.findall(code) + flags + _url_basenames(code)


def _clone_targets(command: str) -> list[str]:
    """The directory each ``git clone`` writes: its explicit target, else the
    repository's basename."""
    targets: list[str] = []
    for tail in _CLONE_RE.findall(command):
        words = tail.split()
        at = next((i for i, w in enumerate(words) if _REPO_RE.search(w)), None)
        if at is None:
            continue
        after = words[at + 1 : at + 2]
        explicit = after and not after[0].startswith("-")
        targets.append(after[0] if explicit else _url_basename(words[at], strip_git=True))
    return targets


def _url_basenames(text: str) -> list[str]:
    """The file name a URL downloads under by default (wget, curl -O)."""
    return [_url_basename(url, strip_git=True) for url in _URL_RE.findall(text)]


def _url_basename(url: str, *, strip_git: bool = False) -> str:
    path = re.split(r"[?#]", url, maxsplit=1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name[:-4] if strip_git and name.endswith(".git") else name


def _scripts_run(tool_name: str, arguments: Any, program: str) -> list[str]:
    """Local script files an exec call runs (by name, or ``make``'s Makefile)."""
    names = [t for t in _TOKEN_RE.findall(program) if _SCRIPT_RE.search(t)]
    if _MAKE_RE.search(program):
        names += ["Makefile", "makefile", "GNUmakefile"]
    for key in SCRIPT_RUN_ARGS.get(tool_name, ()):
        value = arguments.get(key) if isinstance(arguments, dict) else None
        if isinstance(value, str) and value.strip():
            names.append(value)
    return sorted(_resolve_all(names[:_MAX_SCRIPTS], _dirs(tool_name, arguments, program)))


def _script_fetches(path: str) -> bool:
    """True when the regular file at ``path`` (first 256 KB) reads the network."""
    try:
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            return text_fetches_network(handle.read(_SCRIPT_READ_CAP))
    except OSError:
        return False


def _base_dir(cwd: Any) -> str:
    """The directory a call's relative paths start from (the plan agent's
    workspace when one is set, ADR-092 slice 5)."""
    try:
        resolved = resolve_cwd(cwd if isinstance(cwd, str) and cwd else None)
    except WorkspaceEscape:
        resolved = None
    return os.path.abspath(os.path.expanduser(resolved or cwd or os.getcwd()))


def _dirs(tool_name: str, arguments: Any, program: str) -> list[str]:
    """The call's working directory, then every directory it changes into."""
    shell = isinstance(arguments, dict) and tool_name == "shell_exec"
    cwd = arguments.get("cwd") if shell else None
    base = _base_dir(cwd)
    moved = _CD_RE.findall(program) + _CHDIR_RE.findall(program)
    return [base, *(_resolve(d, base) for d in moved[:64])]


def _resolve(token: str, base: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(token)))


def _resolve_all(tokens: Iterable[str], dirs: list[str], *, record: bool = False) -> set[str]:
    """Each kept token resolved against each directory. A recorded target is
    never the working directory, one of its ancestors, or the home directory
    (that would taint the whole project)."""
    resolved = {_resolve(t, d) for t in tokens if _keep(t) for d in dirs}
    if not record:
        return resolved
    shielded = {os.path.expanduser("~"), os.sep, *dirs}
    return {p for p in resolved if not any(s == p or s.startswith(p + os.sep) for s in shielded)}


def _keep(token: str) -> bool:
    token = token.strip()
    if not token or token in _NOT_PATHS or "://" in token:
        return False
    return not (token[0] in "-$&" or token.isdigit())


def _file_like(name: str) -> bool:
    return len(name) >= _MIN_BASENAME_LEN and bool(re.search(r"\.\w{1,8}$", name))


def _parents(path: str) -> list[str]:
    parents: list[str] = []
    head = os.path.dirname(path)
    while head and head != os.sep and len(parents) < 64:
        parents.append(head)
        head = os.path.dirname(head)
    return parents


def _strings(value: Any, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v, depth + 1)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v, depth + 1)]
    return []


def _all_text(arguments: Any) -> str:
    return bounded("\n".join(_strings(arguments)))


__all__ = [
    "EXEC_PROGRAM_ARGS",
    "READER_TOOLS",
    "SCRIPT_RUN_ARGS",
    "exec_fetches_network",
    "holds_recorded",
    "names_recorded",
    "program_of",
    "read_paths",
    "searched_dirs",
    "text_fetches_network",
    "write_targets",
    "written_fetch_scripts",
]
