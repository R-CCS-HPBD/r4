"""Changes in the person's HPC home, over the person's own SSH session.

ADR-112 D-8: ``r4 hpc add`` installs the gate as the person, with their own
``ssh``; r4 never holds a credential for the site and never talks to a
scheduler. Each remote program here is a fixed constant: the command line
sent to ``ssh`` carries no text that came from the person, the server or the
site. Everything variable (the gate files, the ``authorized_keys`` line,
the comments to remove) travels on stdin.

The constants are single lines (``;``-joined) so that a login shell other
than ``sh`` (``tcsh``) still passes them through intact.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass
from typing import Any

from .hpc_signing import (
    GATE_DIR,
    GATE_FILE,
    GATE_PYTHON,
    GRANT_EPOCH_FILE,
    GRANT_EPOCH_FRESH_MEMBER,
    GRANT_EPOCH_LEGACY_MEMBER,
    GRANT_EPOCH_MEMBER,
    SITE_PROFILE_FILE,
    GateInstall,
    HpcSigningError,
    check_home,
    check_host,
    check_login,
    check_site,
)

#: Prints the login and the home directory, one per line.
PROBE_SCRIPT = 'printf "%s\\n%s\\n" "$(id -un)" "$HOME"'

#: Name of the stdin tar member that holds the ``authorized_keys`` line.
LINE_MEMBER = "authorized_keys_line"
GATE_MEMBERS = (GATE_FILE, "gate.json", "allowed_signers")

_NEW_PROFILE = f'"$g/{SITE_PROFILE_FILE}.new"'


def _epoch_script(*, advance: bool) -> str:
    """Fixed remote Python: initialize once, or advance under the gate's lock."""
    lines = [
        "import fcntl, json, os, sys",
        "e = json.loads(sys.stdin.read())['epoch']",
        f"assert type(e) is int and e {'>' if advance else '>='} 0",
        f'd = os.path.expanduser("~/{GATE_DIR}/state/grants")',
        "os.makedirs(d, 0o700, exist_ok=True)",
        "os.chmod(d, 0o700)",
        'l = os.open(os.path.join(d, "epoch.lock"), os.O_WRONLY | os.O_CREAT, 0o600)',
        "fcntl.flock(l, fcntl.LOCK_EX)",
        'p = os.path.join(d, "epoch")',
        "o = json.load(open(p)).get('epoch', 0) if os.path.exists(p) else 0",
    ]
    if advance:
        lines.append("n = max(e, o + 1)")
    else:
        lines += ["os.path.exists(p) and sys.exit(0)", "n = e"]
    lines += [
        't = p + ".new"',
        'f = os.fdopen(os.open(t, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")',
        "f.write(json.dumps({'epoch': n}, separators=(',', ':')))",
        "f.flush()",
        "os.fsync(f.fileno())",
        "f.close()",
        "os.replace(t, p)",
        "os.close(l)",
        "print('written')",
    ]
    return "; ".join(lines)


#: ``EPOCH_PY`` never lowers or reuses an epoch. ``EPOCH_INIT_PY`` bootstraps
#: an absent one only, either with a fresh install time or legacy zero.
EPOCH_PY = _epoch_script(advance=True)
EPOCH_INIT_PY = _epoch_script(advance=False)

#: Zero only for a gate that still has a counter or cancellation record. An
#: empty directory (or lock files alone) is fresh state, not a legacy upgrade.
LEGACY_GRANT_STATE_PY = "; ".join(
    [
        "import os, re, sys",
        f'd = os.path.expanduser("~/{GATE_DIR}/state/grants")',
        "ok = os.path.isdir(d) and any("
        "re.fullmatch(r'[0-9a-f]{32}\\.(json|cancelled)', n) "
        "and os.path.isfile(os.path.join(d, n)) for n in os.listdir(d))",
        "sys.exit(0 if ok else 1)",
    ]
)

#: Reads a tar on stdin: gate files and any epoch metadata. Re-enrolment uses
#: ``EPOCH_PY``; an active install initializes only an absent epoch, with its
#: install time for fresh state and zero for a pre-existing legacy state.
INSTALL_SCRIPT = "; ".join(
    [
        "set -eu",
        "umask 077",
        f'g="$HOME/{GATE_DIR}"',
        't="$HOME/.umrs/.install-$$"',
        'a="$HOME/.ssh/authorized_keys"',
        'mkdir -p "$g/state/nonces" "$g/log" "$HOME/.ssh"',
        'chmod 700 "$HOME/.umrs" "$g" "$g/state" "$g/state/nonces" "$g/log" "$HOME/.ssh"',
        'rm -rf "$t"',
        'mkdir "$t"',
        'tar -xf - -C "$t"',
        f"for f in {' '.join(GATE_MEMBERS)}; do "
        'cp "$t/$f" "$g/$f.new"; chmod 600 "$g/$f.new"; mv -f "$g/$f.new" "$g/$f"; done',
        f'if [ -f "$t/{SITE_PROFILE_FILE}" ]; then cp "$t/{SITE_PROFILE_FILE}" {_NEW_PROFILE}; '
        f'chmod 600 {_NEW_PROFILE}; mv -f {_NEW_PROFILE} "$g/{SITE_PROFILE_FILE}"; fi',
        f'if [ -f "$t/{GRANT_EPOCH_MEMBER}" ]; then '
        f'{GATE_PYTHON} -I -c {shlex.quote(EPOCH_PY)} < "$t/{GRANT_EPOCH_MEMBER}"; '
        f'elif [ ! -f "$g/{GRANT_EPOCH_FILE}" ] && '
        f'[ -f "$t/{GRANT_EPOCH_FRESH_MEMBER}" ] && '
        f'[ -f "$t/{GRANT_EPOCH_LEGACY_MEMBER}" ]; then '
        f'if [ -d "$g/state/grants" ] && '
        f"{GATE_PYTHON} -I -c {shlex.quote(LEGACY_GRANT_STATE_PY)}; then "
        f'{GATE_PYTHON} -I -c {shlex.quote(EPOCH_INIT_PY)} < "$t/{GRANT_EPOCH_LEGACY_MEMBER}"; '
        "else "
        f'{GATE_PYTHON} -I -c {shlex.quote(EPOCH_INIT_PY)} < "$t/{GRANT_EPOCH_FRESH_MEMBER}"; '
        "fi; fi",
        'touch "$a"',
        'chmod 600 "$a"',
        'if [ -s "$a" ] && [ -n "$(tail -c 1 "$a")" ]; then echo >> "$a"; fi',
        f'grep -qxF -f "$t/{LINE_MEMBER}" "$a" || cat "$t/{LINE_MEMBER}" >> "$a"',
        'rm -rf "$t"',
        "echo installed",
    ]
)

#: Reads the site profile's exact bytes on stdin and replaces
#: ``~/.umrs/gate/site.json`` with them (0600, atomically). Fails (exit 1)
#: when the gate is not installed.
PROFILE_SCRIPT = "; ".join(
    [
        "set -eu",
        "umask 077",
        f'g="$HOME/{GATE_DIR}"',
        'test -d "$g"',
        f"cat > {_NEW_PROFILE}",
        f"chmod 600 {_NEW_PROFILE}",
        f'mv -f {_NEW_PROFILE} "$g/{SITE_PROFILE_FILE}"',
        "echo installed",
    ]
)

#: Prints the gate's hash-chained log (nothing when there is none yet).
LOG_SCRIPT = f'cat "$HOME/{GATE_DIR}/log/gate.jsonl" 2>/dev/null || true'

#: Reads ``{"drop": [comment, ...], "add": line | ""}`` on stdin. Removes every
#: ``authorized_keys`` line whose last field is one of the comments, appends
#: ``add`` if given, replaces the file atomically (0600) and prints
#: ``{"removed": n}``.
EDIT_LINES_PY = "; ".join(
    [
        "import json, os, sys",
        "r = json.load(sys.stdin)",
        'p = os.path.expanduser("~/.ssh/authorized_keys")',
        "L = open(p).read().splitlines() if os.path.exists(p) else []",
        'K = [l for l in L if l.split()[-1:] not in [[c] for c in r["drop"]]]',
        "n = len(L) - len(K)",
        'K += [r["add"]] if r["add"] else []',
        't = p + ".umrs-new"',
        'f = os.fdopen(os.open(t, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")',
        'f.write("".join(l + "\\n" for l in K))',
        "f.flush()",
        "os.fsync(f.fileno())",
        "f.close()",
        "os.chmod(t, 0o600)",
        "os.replace(t, p)",
        'print(json.dumps({"removed": n}))',
    ]
)


def _run(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    return subprocess.run(argv, check=False, **kwargs)


def ssh_argv(host: str, program: list[str], script: str) -> list[str]:
    """``ssh <host> <program...> '<fixed script>'``. The script is quoted once
    for the remote login shell, which joins and re-parses the arguments."""
    return ["ssh", check_host(host), *program, shlex.quote(script)]


def _ssh(host: str, program: list[str], script: str, stdin: bytes, timeout: int = 300) -> bytes:
    """Run a fixed program as the person; stderr (and prompts) stay theirs."""
    argv = ssh_argv(host, program, script)
    try:
        proc = _run(argv, input=stdin, stdout=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HpcSigningError(f"ssh {host} did not run: {exc}") from exc
    if proc.returncode != 0:
        raise HpcSigningError(f"ssh {host} failed (exit {proc.returncode}); nothing was verified.")
    return bytes(proc.stdout or b"")


@dataclass(frozen=True)
class RemoteHome:
    login: str
    home: str


def probe(host: str) -> RemoteHome:
    """The login and home directory the site gives this person."""
    out = _ssh(host, ["sh", "-c"], PROBE_SCRIPT, b"").decode("utf-8", "replace")
    lines = out.splitlines()
    if len(lines) != 2:
        raise HpcSigningError(f"ssh {host}: could not read the login and home directory.")
    return RemoteHome(login=check_login(lines[0]), home=check_home(lines[1]))


def tar_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def install_payload(plan: GateInstall) -> bytes:
    files = dict(plan.files)
    files[LINE_MEMBER] = (plan.line + "\n").encode("ascii")
    return tar_bytes(files)


def install(host: str, plan: GateInstall) -> None:
    out = _ssh(host, ["sh", "-c"], INSTALL_SCRIPT, install_payload(plan))
    if out.strip().splitlines()[-1:] != [b"installed"]:
        raise HpcSigningError(f"ssh {host}: the install did not complete.")


def install_profile(host: str, data: bytes) -> None:
    """Write ``site.json`` (the checked profile bytes) over the person's SSH."""
    out = _ssh(host, ["sh", "-c"], PROFILE_SCRIPT, bytes(data))
    if out.strip().splitlines()[-1:] != [b"installed"]:
        raise HpcSigningError(f"ssh {host}: the site profile was not installed.")


def read_gate_log(host: str) -> bytes:
    """The gate's log, read as the person (r4 runs no scheduler command)."""
    return _ssh(host, ["sh", "-c"], LOG_SCRIPT, b"")


DIRECT_CANCEL_TIMEOUT_S = 90
DIRECT_CANCEL_ANSWER_MAX_BYTES = 64 * 1024


def direct_grant_cancel(host: str, venue: str, envelope: dict[str, str]) -> dict[str, Any]:
    """Submit one signed ``grant-cancel`` over the person's own SSH session.

    The gate takes the request only on stdin. Its absolute installed path and
    checked venue are shell-quoted; the SSH call and JSON answer are bounded.
    """
    where = probe(host)
    script_path = check_home(where.home) + "/" + os.path.join(GATE_DIR, GATE_FILE)
    script = (
        f"SSH_ORIGINAL_COMMAND=grant-cancel {GATE_PYTHON} -I {shlex.quote(script_path)} "
        f"--site {shlex.quote(check_site(venue))}"
    )
    payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    out = _ssh(host, ["sh", "-c"], script, payload, timeout=DIRECT_CANCEL_TIMEOUT_S)
    if len(out) > DIRECT_CANCEL_ANSWER_MAX_BYTES:
        raise HpcSigningError(f"ssh {host}: the gate cancel answer is too large.")
    try:
        answer = json.loads(out)
    except ValueError as exc:
        raise HpcSigningError(f"ssh {host}: the gate cancel answer is not JSON.") from exc
    if not isinstance(answer, dict):
        raise HpcSigningError(f"ssh {host}: the gate cancel answer is not an object.")
    return answer


def write_epoch(host: str, data: bytes) -> None:
    """Raise the gate's grant epoch (``epoch_bytes``) over the person's SSH."""
    out = _ssh(host, [GATE_PYTHON, "-I", "-c"], EPOCH_PY, bytes(data))
    if out.strip().splitlines()[-1:] != [b"written"]:
        raise HpcSigningError(f"ssh {host}: the grant epoch was not written.")


def edit_lines(host: str, *, drop: list[str], add: str = "") -> int:
    """Remove the lines with these comments (and append ``add``); the count removed."""
    payload = json.dumps({"drop": list(drop), "add": add}).encode("utf-8")
    out = _ssh(host, [GATE_PYTHON, "-I", "-c"], EDIT_LINES_PY, payload)
    try:
        removed = json.loads(out.decode("utf-8").strip().splitlines()[-1])["removed"]
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise HpcSigningError(f"ssh {host}: authorized_keys edit gave no answer.") from exc
    return int(removed)


# ── the same, by hand ────────────────────────────────────────────────────────

_MKDIRS = (
    f"mkdir -p ~/{GATE_DIR}/state/nonces ~/{GATE_DIR}/log && "
    f"chmod 700 ~/.umrs ~/{GATE_DIR} ~/{GATE_DIR}/state ~/{GATE_DIR}/state/nonces "
    f"~/{GATE_DIR}/log"
)


def _file_block(name: str, data: bytes, note: str = "") -> list[str]:
    return [
        f"==> ~/{GATE_DIR}/{name} (mode 0600{note}) <==",
        data.decode("utf-8").rstrip("\n"),
        f"==> end of {name} <==",
        "",
    ]


def manual_profile_command(data: bytes) -> str:
    """One command that writes ``site.json`` byte for byte (the gate hashes
    the exact bytes; an editor's trailing newline would change them)."""
    path = f"~/{GATE_DIR}/{SITE_PROFILE_FILE}"
    return "\n".join(
        [
            f"==> write {path} with exactly these bytes (mode 0600): run <==",
            f"printf '%s' {shlex.quote(data.decode('utf-8'))} > {path} && chmod 600 {path}",
        ]
    )


def _manual_epoch_write(data: bytes, script: str) -> str:
    payload = shlex.quote(data.decode("ascii"))
    return f"printf '%s' {payload} | {GATE_PYTHON} -I -c {shlex.quote(script)}"


def manual_epoch_command(data: bytes) -> str:
    """Advance the epoch by hand, never lowering or reusing its old value."""
    path = f"~/{GATE_DIR}/{GRANT_EPOCH_FILE}"
    return "\n".join(
        [
            f"==> advance {path} (never lower or reuse it): run <==",
            _manual_epoch_write(data, EPOCH_PY),
        ]
    )


def manual_epoch_bootstrap_command(fresh: bytes, legacy: bytes) -> str:
    """Initialize only an absent epoch: fresh time, or zero for an upgrade."""
    path = f"~/{GATE_DIR}/{GRANT_EPOCH_FILE}"
    directory = f"~/{GATE_DIR}/state/grants"
    legacy_write = _manual_epoch_write(legacy, EPOCH_INIT_PY)
    fresh_write = _manual_epoch_write(fresh, EPOCH_INIT_PY)
    return "\n".join(
        [
            f"==> initialize {path} only if absent (fresh time, or legacy zero): run <==",
            f"if [ ! -f {path} ]; then if [ -d {directory} ] && "
            f"{GATE_PYTHON} -I -c {shlex.quote(LEGACY_GRANT_STATE_PY)}; then "
            f"{legacy_write}; else {fresh_write}; fi; fi",
        ]
    )


def manual_install_text(plan: GateInstall, site: str) -> str:
    """``r4 hpc add --manual``: the three files and the line, to install by hand."""
    lines = ["Install these in your HPC home at the site, then verify.", "", _MKDIRS, ""]
    lines += _file_block("gate.json", plan.files["gate.json"])
    lines += _file_block("allowed_signers", plan.files["allowed_signers"])
    if SITE_PROFILE_FILE in plan.files:
        lines += [manual_profile_command(plan.files[SITE_PROFILE_FILE]), ""]
    if GRANT_EPOCH_MEMBER in plan.files:
        lines += [manual_epoch_command(plan.files[GRANT_EPOCH_MEMBER]), ""]
    elif GRANT_EPOCH_FRESH_MEMBER in plan.files:
        lines += [
            manual_epoch_bootstrap_command(
                plan.files[GRANT_EPOCH_FRESH_MEMBER], plan.files[GRANT_EPOCH_LEGACY_MEMBER]
            ),
            "",
        ]
    note = f"; sha256 {plan.gate_sha256}"
    lines += _file_block(GATE_FILE, plan.files[GATE_FILE], note)
    lines += [
        "==> append this one line to ~/.ssh/authorized_keys (file mode 0600, ~/.ssh mode 0700) <==",
        plan.line,
        "",
        f"Then run: r4 hpc verify {site} --login <your login at the site>",
    ]
    return "\n".join(lines)


def manual_rotate_text(old_comment: str, line: str, site: str) -> str:
    """``r4 hpc rotate --manual``: which line to replace, and with what."""
    return "\n".join(
        [
            f"In ~/.ssh/authorized_keys at the site, delete the line ending in ' {old_comment}'",
            "and append this one line:",
            line,
            "",
            f"Then run: r4 hpc verify {site} --login <your login at the site>",
        ]
    )
