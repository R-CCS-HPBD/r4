#!/usr/bin/env python3
"""Real R4 companion on glm-5.3 (rikyu) over the gold questions, arms A / B / C / P (C + context pack).

Maintainer decision 2026-10-03: measure with glm-5.3@rikyu only, no Anthropic API.
Each case runs benchmarks.harness.adapters.r4_companion inside bwrap:
  - / read-only; ~/.config/r4 read-only (credentials are NOT copied);
  - writable overlays for the companion's state dirs point into a per-case scratch dir,
    so bench sessions never land in the maintainer's session history;
  - writable per-case workdir; proto/out writable for the kgq query cache.
Results: results/<arm>__<qid>.json (driver payload, metrics, session files list).

usage: run_glm.py --qids ID,ID|all --arms A,B,C,P [--par 4] [--timeout 1200] [--model glm-5.3]
"""

import argparse
import concurrent.futures as cf
import json
import shutil
import subprocess
import time
from pathlib import Path

import paths as P  # noqa: E402

BENCH = P.BENCH
HERE = P.OUT
REPO = P.REPO
PY = REPO / ".venv/bin/python"
R4CFG = Path.home() / ".config/r4"
STATE_DIRS = ("companion", "logs", "runtime", "projects-data", "research_loops", "teamup_sessions")


def load_gold() -> dict:
    # builtins.open on purpose: run_harness.load_run_glm answers this read in-process (smoke)
    with open(BENCH / "gold/gold.json") as f:
        return {g["id"]: g for g in json.load(f)}


GOLD = load_gold()

TOOLS = {
    "A": "You have the files and your normal tools (file reading, grep, shell with python/ffprobe/ffmpeg available). There is no project knowledge base for this material.",
    "B": f'Besides the files and your normal tools, you can run the platform knowledge tools from the shell (read {BENCH}/current/TOOL_CARD.md first). Run them as: cd {BENCH}/current && KG_VARIANT=llm-on ./kgq-current "query" --project P  (also ./catalog-current, ./kg-show-current).',
    "C": f"Besides the files and your normal tools, you can run the project knowledge-graph tool kgq from the shell (read {BENCH}/proto/TOOL_CARD.md first). Run it as: {BENCH}/proto/kgq --project P VERB ...",
}
TOOLS["P"] = TOOLS["C"]


def projects_for(q):
    return (
        ["example-project-a", "example-project-b"]
        if q["project"] == "cross-project"
        else [q["project"]]
    )


def context_pack(q):
    """ADR-085 context pack: kgq brief for the question (<=1.2 KB), per project."""
    parts = []
    for p in projects_for(q):
        r = subprocess.run(
            [str(BENCH / "proto/kgq"), "--project", p, "brief", q["question"]],
            capture_output=True,
            text=True,
            timeout=120,
        )
        parts.append(f"[{p}]\n{r.stdout.strip()}")
    return "\n".join(parts)


def prompt(arm, q):
    dirs = " and ".join(str(BENCH / "corpus" / p) for p in projects_for(q))
    pack = ""
    if arm == "P":
        pack = (
            "\nProject knowledge context (auto-retrieved from the project knowledge graph for this question; "
            "verify before relying on it):\n" + context_pack(q) + "\n"
        )
    return f"""You are a research assistant for the scientists of project(s) {", ".join(projects_for(q))}.
Answer this research question:

  {q["question"]}
{pack}
The project material is in {dirs} (read {BENCH}/corpus/MANIFEST.md first; the directories are symlinks, follow them).
{TOOLS[arm]}
Rules: the filesystem is read-only except your working directory; do not try to modify anything. Do not read anything under {BENCH} other than the corpus directories, MANIFEST.md and the tool card named above. Do not use the network.
Prefer the most recent/current information when sources disagree (say if something was superseded or retracted). Give exact numbers with units and cite your sources (file path + line/section, or tool ids). If the material does not contain the answer, say so instead of guessing.
End your reply with a line 'FINAL ANSWER:' followed by your concise answer."""


def fresh_work(arm, qid):
    """Empty per-case dir with the companion state dirs and tmp/ -> (work, state)."""
    work = HERE / "work" / arm / qid
    if work.exists():
        shutil.rmtree(work)
    state = work / "r4state"
    for d in STATE_DIRS:
        (state / d).mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    return work, state


def bwrap_cmd(work, state, pf, model):
    cmd = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
        "--bind",
        str(work),
        str(work),
        "--bind",
        str(BENCH / "proto/out"),
        str(BENCH / "proto/out"),
        "--ro-bind",
        str(R4CFG),
        str(R4CFG),
    ]
    for d in STATE_DIRS:
        cmd += ["--bind", str(state / d), str(R4CFG / d)]
    return cmd + [
        "--setenv",
        "TMPDIR",
        str(work / "tmp"),
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "PYTHONPATH",
        f"{REPO}/r4/src:{REPO}",
        "--chdir",
        str(REPO),
        "--die-with-parent",
        "--",
        str(PY),
        "-m",
        "benchmarks.harness.adapters.r4_companion",
        "--prompt-file",
        str(pf),
        "--workdir",
        str(work),
        "--model",
        model,
    ]


def drive(cmd, timeout):
    """Run the companion; its last JSON stdout line is the payload (+ rc, stderr tail)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        lines = [ln for ln in p.stdout.strip().splitlines() if ln.strip().startswith("{")]
        payload = json.loads(lines[-1]) if lines else {"status": "failed", "error": "no payload"}
        payload["stderr_tail"] = p.stderr[-3000:]
        payload["rc"] = p.returncode
    except subprocess.TimeoutExpired:
        payload = {"status": "timeout", "error": f"timeout {timeout}s"}
    return payload


def run_case(arm, qid, model, timeout):
    out = HERE / "results" / f"{arm}__{qid}.json"
    if out.exists():
        return json.loads(out.read_text())
    q = GOLD[qid]
    work, state = fresh_work(arm, qid)
    pf = work / "PROMPT.txt"
    pf.write_text(prompt(arm, q))
    cmd = bwrap_cmd(work, state, pf, model)
    t0 = time.monotonic()
    payload = drive(cmd, timeout)
    payload.update(
        {
            "arm": arm,
            "qid": qid,
            "model": model,
            "outer_wall_s": round(time.monotonic() - t0, 1),
            "session_files": sorted(
                str(x.relative_to(state)) for x in state.rglob("*") if x.is_file()
            ),
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-5.3")
    ap.add_argument("--qids", required=True)
    ap.add_argument("--arms", default="A,B,C,P")
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    qids = list(GOLD) if a.qids == "all" else a.qids.split(",")
    jobs = [(arm, q) for q in qids for arm in a.arms.split(",")]
    with cf.ThreadPoolExecutor(a.par) as ex:
        futs = {ex.submit(run_case, arm, q, a.model, a.timeout): (arm, q) for arm, q in jobs}
        for f in cf.as_completed(futs):
            arm, q = futs[f]
            try:
                r = f.result()
                m = r.get("metrics", {})
                print(
                    f"{arm} {q} {r.get('status')} wall={m.get('wallclock_sec')} llm={m.get('llm_calls')} "
                    f"tok={m.get('total_tokens')}",
                    flush=True,
                )
            except Exception as e:  # noqa: BLE001
                print(f"{arm} {q} ERROR {type(e).__name__}", flush=True)


if __name__ == "__main__":
    main()
