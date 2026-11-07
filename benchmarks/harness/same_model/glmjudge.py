"""glm-5.3 (rikyu) judge helper via the r4 CLI, plus deterministic key-fact matching.

Maintainer decision 2026-10-03: grading uses glm-5.3@rikyu, never the Anthropic API.

2026-10-20: judge calls die as "__ERROR__ ... read operation timed out" whenever
rikyu is loaded, because r4's per-request read timeout defaults to 300 s while a
temperature-0 judge completion can reason longer than that. glm() now carries
R4_TIMEOUT=900 into the r4 subprocess (env still wins) and a 900 s leash to match.
"""

import json
import os
import re
import subprocess

import paths as P  # code dir on sys.path

R4 = str(P.REPO / ".venv/bin/r4")


def glm(
    system: str, user: str, *, max_tokens: int = 4000, retries: int = 3, timeout: int = 900
) -> str:
    env = dict(os.environ)
    env.setdefault("R4_TIMEOUT", "900")  # r4's per-request read timeout; env wins
    last = ""
    for _ in range(retries):
        try:
            p = subprocess.run(
                [
                    R4,
                    "chat",
                    "-m",
                    "glm-5.3",
                    "--temperature",
                    "0",
                    "--max-tokens",
                    str(max_tokens),
                    "--system",
                    system,
                    user,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            out = p.stdout.strip()
            if out:
                return out
            last = p.stderr[-300:]
        except subprocess.TimeoutExpired:
            last = "timeout"
    return f"__ERROR__ {last}"


def parse_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


_NUM = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:\.\d+)?")


def numbers(s: str) -> set[str]:
    out = set()
    for n in _NUM.findall(s or ""):
        n = n.replace(",", "")
        if n.strip("-").strip("0") == "" and len(n) < 2:
            continue
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def det_containment(key_facts: list[str], text: str) -> float:
    """Share of key facts whose numbers (if any) all appear in text; facts without numbers are skipped."""
    tnums = numbers(text)
    scored = []
    for f in key_facts:
        fn = numbers(f)
        if not fn:
            continue
        scored.append(all(n in tnums for n in fn))
    return round(sum(scored) / len(scored), 3) if scored else float("nan")
