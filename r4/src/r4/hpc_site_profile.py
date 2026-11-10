"""The gate's site profile (``~/.umrs/gate/site.json``), as r4 installs it.

ADR-112 wave B2, plan §5 B2.0 item 2. The profile comes from the site's
operator: which partitions a signed step may ask for, the scheduler's
``PATH``, the venue paths, the image archive directory and the caps. r4
fetches it (``GET /umrs/sites/me/hpc/site-profile``), checks it with the
gate's own validator, shows it to the person with a diff against the copy r4
last installed, and writes it over the person's own SSH session. The gate
then checks every intent against the file it holds, and an intent names the
sha256 of the profile it was proposed against.

r4 keeps a copy of the bytes it installed (``~/.config/r4/hpc/profiles/
<site>.json``, its own directory: a ``sites/<site>.site.json`` copy would be
the site record of a site named ``<site>.site``): the diff is against that
copy, so showing it costs no extra SSH round trip (a site login may ask for a
one-time password each time), and ``r4 workflow trigger --site`` reads ``slurm.queue_seconds`` from
it to bound an intent's ``max_run_seconds``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from r4_contracts import hpc_gate

from .hpc_signing import HpcSigningError, site_record_path

#: Each flattened profile line shown is cut to this many characters.
_SHOWN_CAP = 160


@dataclass(frozen=True)
class SiteProfile:
    """A checked profile: the object, its exact bytes and their sha256."""

    profile: dict[str, Any]
    data: bytes
    sha256: str


def _refuse(detail: str) -> HpcSigningError:
    return HpcSigningError(f"refused: the site profile {detail}")


def _decode(answer: Any) -> tuple[bytes, str]:
    data_field = answer.get("bytes_b64") if isinstance(answer, dict) else None
    if not isinstance(data_field, str):
        raise _refuse("answer carries no bytes_b64.")
    try:
        data = base64.b64decode(data_field, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _refuse("bytes are not base64.") from exc
    return data, hashlib.sha256(data).hexdigest()


def _checked_object(data: bytes) -> dict[str, Any]:
    """The gate's own checks: strict canonical JSON, then its schema."""
    try:
        obj: dict[str, Any] = hpc_gate.parse_strict(data)
        hpc_gate.validate_site_profile(obj)
        canonical = hpc_gate.site_profile_bytes(obj)
    except (ValueError, hpc_gate.GateError) as exc:
        raise _refuse(f"is not valid: {exc}") from exc
    if canonical != data:
        raise _refuse("bytes are not the canonical encoding of the profile.")
    return obj


def parse_site_profile(answer: Any, *, venue: str, domain: str) -> SiteProfile:
    """Check a ``GET site-profile`` answer: its bytes, their digest, the
    gate's validator, and that it names this site and signing domain."""
    data, digest = _decode(answer)
    if answer.get("sha256") != digest:
        raise _refuse("sha256 does not match its bytes.")
    obj = _checked_object(data)
    if obj.get("site") != venue or obj.get("domain") != domain:
        raise _refuse(
            f"names site {obj.get('site')!r} and domain {obj.get('domain')!r}, "
            f"not {venue!r} and {domain!r}."
        )
    return SiteProfile(profile=obj, data=data, sha256=digest)


# ── showing it ──────────────────────────────────────────────────────────────


def _flatten(value: Any, prefix: str, out: dict[str, str]) -> None:
    if isinstance(value, dict) and value:
        for key in sorted(value):
            _flatten(value[key], f"{prefix}.{key}" if prefix else str(key), out)
        return
    out[prefix] = json.dumps(value, sort_keys=True, ensure_ascii=False)


def flatten(profile: Optional[dict[str, Any]]) -> dict[str, str]:
    """``{"partitions.gpu.max_cpus": "64", ...}``: one line per leaf."""
    out: dict[str, str] = {}
    if profile:
        _flatten(profile, "", out)
    return out


def _shown(text: str) -> str:
    clean = "".join(c if c.isprintable() else "?" for c in text)
    return clean if len(clean) <= _SHOWN_CAP else clean[: _SHOWN_CAP - 1] + "…"


def _change(key: str, old: Optional[str], new: Optional[str]) -> Optional[str]:
    if old == new:
        return None
    if old is None:
        return f"  + {_shown(key)} = {_shown(str(new))}"
    if new is None:
        return f"  - {_shown(key)} = {_shown(old)}"
    return f"  ~ {_shown(key)}: {_shown(old)} -> {_shown(new)}"


def diff_lines(installed: Optional[dict[str, Any]], proposed: dict[str, Any]) -> list[str]:
    """What changes against the profile r4 installed last (``+``/``-``/``~``)."""
    old, new = flatten(installed), flatten(proposed)
    changes = (_change(key, old.get(key), new.get(key)) for key in sorted(set(old) | set(new)))
    return [line for line in changes if line]


def _venue_note(profile: dict[str, Any]) -> list[str]:
    venue = profile.get("venue")
    if isinstance(venue, dict) and any(not value for value in venue.values()):
        return ["  (an empty venue path means the default, /var/tmp/podman_<your login>)"]
    return []


def _owner_note(profile: dict[str, Any]) -> list[str]:
    slurm = profile.get("slurm")
    uids = (slurm.get("owner_uids") if isinstance(slurm, dict) else None) or []
    if not uids:
        return []
    return [
        f"  note: scheduler directories owned by uid(s) {_shown(json.dumps(uids))} "
        "(not only root) are trusted to hold sbatch and sacctmgr."
    ]


def render(profile: SiteProfile, installed: Optional[dict[str, Any]]) -> str:
    """The whole profile, one line per value, then the diff."""
    site = _shown(str(profile.profile.get("site")))
    lines = [f"Site profile for {site} (sha256 {profile.sha256}):"]
    lines += [f"  {_shown(k)} = {_shown(v)}" for k, v in flatten(profile.profile).items()]
    lines += _venue_note(profile.profile) + _owner_note(profile.profile)
    if installed is None:
        lines.append("Nothing installed by this r4 before: all of it is new.")
        return "\n".join(lines)
    changes = diff_lines(installed, profile.profile)
    lines.append("Changes against what this r4 installed:" if changes else "No changes.")
    return "\n".join(lines + changes)


# ── the local copy ──────────────────────────────────────────────────────────


def _copy_path(site: str) -> Optional[Path]:
    record = site_record_path(site)
    return record.parent.parent / "profiles" / record.name if record else None


def installed_profile(site: str) -> Optional[dict[str, Any]]:
    """The profile r4 last installed at ``site``, or None."""
    path = _copy_path(site)
    try:
        data = path.read_bytes() if path else None
        return hpc_gate.parse_strict(data) if data else None
    except (OSError, ValueError):
        return None


def queue_seconds(site: str) -> Optional[int]:
    """``slurm.queue_seconds`` of the installed profile (None when unknown)."""
    profile = installed_profile(site) or {}
    slurm = profile.get("slurm")
    value = slurm.get("queue_seconds") if isinstance(slurm, dict) else None
    return value if type(value) is int else None


def remember(site: str, profile: SiteProfile) -> None:
    """Keep the installed bytes (0600) next to the site record."""
    path = _copy_path(site)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(profile.data)
    os.chmod(path, 0o600)
