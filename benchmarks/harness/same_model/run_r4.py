#!/usr/bin/env python3
"""Hardened R4 lane: the R4 companion on glm-5.3@rikyu under the SAME sandbox, proxy and
accounting as the public harnesses (run_harness.py). Replaces ../run_glm.py's sandbox for the
same-model harness comparison; run_glm.py itself is left untouched (its prompt builder is
imported, so the prompt text is byte-identical).

Differences from run_glm.py (review fixes 2026-10-03):
  sandbox    sandbox.py: own netns (loopback only), private /run /tmp /dev/shm, allow-listed home
             (corpus + arm tool tree + repo r4/ and its editable package sources + .venv + HF
             hub), --clearenv with the pinned base_env of the public lanes (+ PYTHONPATH,
             TMPDIR), so gold/, results*/, other cases, ~/.config/r4 (the real key) and the
             launcher's environment (HF_TOKEN, SSH_AUTH_SOCK, ...) are not visible.
  model      R4 cannot reach rikyu directly any more: a scratch r4 config (XDG_CONFIG_HOME in the
             case dir) registers ONE public source "rikyu" whose URL is the local passthrough
             (http://127.0.0.1:8332/case/<case-tag>/v1) with a dummy key; the passthrough forces
             glm-5.3, holds the real key and logs every request -> proxy_usage, identical to the
             OpenCode lane. Model ref: glm-5.3@rikyu.
  decisions  R4_DECISION_MODEL=off by default (the public lanes get no second model);
             --decision-model <name> to measure it on (then it also goes through the passthrough,
             i.e. it would be forced to glm-5.3 — record that if you use it).
  platform   no gateway/UMRS is reachable from the sandbox, so R4's platform knowledge tools
             (if advertised) can only fail; proxy_usage.tool_names / .called record whether
             they were advertised / called (R4 advantage check for arm A).
State: everything R4 writes (sessions.db, memory, lessons, logs) lands in the case dir
(HOME and XDG dirs are scratch), never in the maintainer's ~/.config/r4.

Results: harness/results_r4/<arm>__<qid>.json (smoke: smoke/results_r4/).

usage:
  run_r4.py --qids ID,ID|all --arms A,C [--par 2] [--timeout 1200]
  run_r4.py --smoke --arms A
  run_r4.py --smoke --probe [--arms A]        # sandbox probe incl. an r4 import check
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE))
import run_harness as RH  # noqa: E402
import sandbox as S  # noqa: E402

HERE = S.HERE  # lane outputs (results_r4_<variant>, work) — outside git

PY = S.VENV / "bin/python"
SOURCE = "rikyu"


VARIANT = ""  # bench: harness-gap fix variant name ("" = repo R4)
R4_SRC = ""  # bench: patched r4 source dir for the variant


def write_r4_config(d: dict[str, Path], tag: str) -> None:
    """Scratch r4 config: one public source pointing at the local passthrough (dummy key)."""
    cfg = d["xdg_config"] / "r4"
    cfg.mkdir(parents=True, exist_ok=True)
    port = S.SERVICES["passthrough"][0]
    sources = {
        "active": SOURCE,
        "sources": [
            {
                "name": SOURCE,
                "kind": "public",
                "url": f"http://127.0.0.1:{port}/case/{tag}/v1",
                "api_key": RH.DUMMY_KEY,
                "description": "bench: local passthrough (glm-5.3 forced; the proxy holds the real key)",
            }
        ],
    }
    p = cfg / "sources.json"
    p.write_text(json.dumps(sources, indent=1))
    p.chmod(0o600)


def r4_env(d: dict[str, Path], decision_model: str) -> dict[str, str]:
    env = RH.base_env(d)
    env.update(
        {
            "XDG_CONFIG_HOME": str(d["xdg_config"]),
            "XDG_DATA_HOME": str(d["xdg_data"]),
            "XDG_STATE_HOME": str(d["xdg_state"]),
            "XDG_CACHE_HOME": str(d["xdg_cache"]),
            "UMRS_CONFIG": str(d["xdg_config"] / "umrs"),
            "PYTHONPATH": f"{R4_SRC or (str(S.REPO) + '/r4/src')}:{S.REPO}",
            "R4_SOURCE": SOURCE,
            "R4_DECISION_MODEL": decision_model,
        }
    )
    return env


def _lane() -> str:
    """Results/work dir name of this lane: r4, or r4_<variant> for a patched-R4 variant."""
    return "r4" if not VARIANT else f"r4_{VARIANT}"


def _results_dir(smoke: bool) -> Path:
    return RH.results_dir(smoke, "r4") if not VARIANT else HERE / f"results_r4_{VARIANT}"


def _extra_ro() -> list[Path]:
    """A variant's patched sources live outside the repo: bind their parent read-only."""
    return [Path(R4_SRC).parent] if R4_SRC else []


def _r4_cmd(pf: Path, d: dict[str, Path], model: str) -> list[str]:
    return [
        str(PY),
        "-m",
        "benchmarks.harness.adapters.r4_companion",
        "--prompt-file",
        str(pf),
        "--workdir",
        str(d["ws"]),
        "--model",
        f"{model}@{SOURCE}",
    ]


def _driver(stdout: str) -> tuple[dict, dict]:
    """The driver's last JSON stdout line -> (payload, copy of its metrics)."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip().startswith("{")]
    try:
        drv = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        drv = {}
    return drv, dict(drv.get("metrics") or {})


def _add_proxy_counts(m: dict, pu: dict) -> None:
    m["agent_steps"] = m.get("llm_calls")
    m["model_requests"] = pu["requests"]
    m["tool_calls"] = sum(pu["called"].values())  # R4's driver reports none; the proxy saw them
    m["tool_calls_by_name"] = pu["called"]


def _error(status: str, drv: dict, timed_out: bool) -> str | None:
    if status == "success":
        return None
    return drv.get("error") or ("timeout" if timed_out else "no payload")


def run_case(Q: RH.Questions, arm: str, qid: str, a, run_meta: dict) -> dict:
    out = _results_dir(Q.smoke) / f"{arm}__{qid}.json"
    prev = RH.cached_result(out, a.redo_failed)
    if prev is not None:
        return prev
    work = RH.work_dir(Q.smoke, _lane(), arm, qid)
    d = RH.fresh_case_dirs(work)
    text = Q.prompt(arm, qid)
    pf = work / "PROMPT.txt"
    pf.write_text(text)
    tag = RH.new_tag("r4" if not VARIANT else f"r4{VARIANT}", arm, qid)  # r4<variant>, no "_"
    write_r4_config(d, tag)
    env = r4_env(d, a.decision_model)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    box_kw = {
        "lane": "r4",
        "arm": arm,
        "work": work,
        "cwd": d["ws"],
        "env": env,
        "cmd": _r4_cmd(pf, d, a.model),
        "corpus_root": Q.root(),
        "tool_tree": not Q.smoke,
        "extra_ro": _extra_ro(),
    }
    rc, stdout, stderr, timed_out, wall, denials = RH.guarded_run(tag, box_kw, None, a.timeout)
    (work / "stdout.txt").write_text(stdout)
    (work / "stderr.txt").write_text(stderr)
    drv, m = _driver(stdout)
    pu = RH.case_proxy_usage("r4", tag, timed_out)
    _add_proxy_counts(m, pu)
    resp = drv.get("response_text") or ""
    status = RH.case_status(timed_out, rc == 0 and drv.get("status") == "success" and resp)
    payload = {
        "harness": "r4",
        "variant": VARIANT,
        "r4_src": R4_SRC or None,
        "arm": arm,
        "qid": qid,
        "model": a.model,
        "model_ref": f"{a.model}@{SOURCE}",
        "case_tag": tag,
        "status": status,
        "rc": rc,
        "error": _error(status, drv, timed_out),
        "response_text": resp,
        "has_final_answer": "FINAL ANSWER:" in resp,
        "wall_s": wall,
        "metrics": m,
        "tool_calls": {"total": m["tool_calls"], "by_name": m["tool_calls_by_name"]},
        "proxy_usage": pu,
        "egress_denied": denials,
        "bench_path_mentions": RH.bench_path_mentions(work),
        "decision_model": a.decision_model,
        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "smoke": Q.smoke,
        "run": {**run_meta, "case_started_at": started_at},
        "metrics_doc": RH.METRICS_DOC,
        "stderr_tail": stderr[-3000:],
        "session_files": RH.session_files(work, d),
    }
    if not Q.smoke:
        payload["prompt_matches_run_glm"] = RH.r4_prompt_match(arm, qid, text)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    return payload


def import_check(root: Path, smoke: bool, arm: str) -> dict:
    work = RH.work_dir(smoke, "_probe", f"r4-import-{arm}")
    d = RH.fresh_case_dirs(work)
    env = r4_env(d, "off")
    cmd = [
        str(PY),
        "-c",
        "import r4.companion.session, r4.client, benchmarks.harness.adapters.r4_companion",
    ]
    box = S.Box(
        lane="r4",
        arm=arm,
        work=work,
        cwd=d["ws"],
        env=env,
        cmd=cmd,
        corpus_root=root,
        services=("passthrough",),
        tool_tree=not smoke,
    )
    rc, _out, err, _ = S.run(box, None, 120)
    return {
        "check": "r4_imports",
        "result": rc == 0,
        **({} if rc == 0 else {"err": err.strip()[-200:]}),
    }


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", default="glm-5.3")
    ap.add_argument(
        "--variant", default="", help="bench: name of a patched-R4 variant (results_r4_<variant>/)"
    )
    ap.add_argument(
        "--r4-src", default="", help="bench: patched r4 source dir (contains the r4 package)"
    )
    ap.add_argument("--qids", default=None)
    ap.add_argument("--arms", default="A,C")
    ap.add_argument("--par", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument(
        "--decision-model",
        default="off",
        help="R4_DECISION_MODEL for the case ('off' = same single model as the public lanes)",
    )
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--redo-failed", action="store_true")
    ap.add_argument(
        "--run-note",
        default="",
        help="free text recorded in every result, e.g. which other lanes run concurrently on rikyu",
    )
    return ap


def _probe_main(a, arms: list[str]) -> int:
    root = S.SMOKE if a.smoke else S.BENCH
    allok = True
    for arm in arms:
        checks = RH.probe(root, a.smoke, "r4", arm, a.model) + [import_check(root, a.smoke, arm)]
        allok &= RH.print_checks(f"r4 {arm}", checks)
    return 0 if allok else 1


def _run_meta(a, arms: list[str], qids: list[str]) -> dict:
    return {
        "runner": RH.RUNNER_VERSION + "+r4",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "par": a.par,
        "run_note": a.run_note,
        "lanes_in_this_invocation": ["r4"],
        "arms_in_this_invocation": arms,
        "n_cases_in_this_invocation": len(qids) * len(arms),
        "decision_model": a.decision_model,
        "note": "wall-clock depends on rikyu load and prefix caching: compare tokens/steps/tool calls "
        "first; wall only between runs with similar par/concurrency",
    }


def main() -> int:
    a = _parser().parse_args()
    global VARIANT, R4_SRC
    VARIANT, R4_SRC = a.variant, a.r4_src
    if bool(VARIANT) != bool(R4_SRC):
        raise SystemExit("--variant and --r4-src go together")
    arms = RH.csv_list(a.arms)
    if a.smoke and any(x != "A" for x in arms):
        sys.exit("smoke mode is arm A only")
    if a.probe:
        return _probe_main(a, arms)
    Q = RH.Questions(smoke=a.smoke)
    qids = RH.select_qids(Q, a)
    RH.preflight(["r4"], arms, a.smoke)
    run_meta = _run_meta(a, arms, qids)
    jobs = [(f"r4 {arm} {q}", (run_case, Q, arm, q, a, run_meta)) for q in qids for arm in arms]
    RH.run_jobs(a.par, jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
