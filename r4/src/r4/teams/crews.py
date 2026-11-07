"""Crews are plan templates (ADR-092 slice 4, MD-2, MD-8).

A *crew* is a user-visible name for a team shape. Since slice 4 every crew
but one is an alias for a built-in plan template (D-10): selecting it runs
that template through the CORE ``plan`` engine, the one IR the roster and
pipeline spellings compile into (MD-2). The exception is ``hpc``, the HPC
optimisation preset: the PM/SE/PG/CD teamup session (``AgentRole``), which
stays an interactive session with its own scaffolding.

The crews' role prompts live here, one source each, and their templates
reference them as data (``{"ref": "crew_role:<crew>/<profile>"}``, resolved
by ``plan_sources``), as ``research-round`` references the research roles'
prompts in ``research_loop.team``. The research crew IS ``research-round``.

What changed for a crew user (MD-8 waived U-04's byte-compatibility):

- the four PM/SE/PG/CD slots are gone outside ``hpc``: each crew runs its
  own profiles, defined by their prompts, in the template's phase order;
- ``paper-writing``, ``kakenhi-report`` and ``peer-review`` ran the HPC
  role prompts under their crew names; they now have prompts of their own;
- a crew run is one bounded plan run (budget, journal, resume, a card that
  always asks, MD-5), not a session advanced iteration by iteration.

Nothing here imports the teamup runtime: no HPC scaffolding reaches a crew.
"""

from __future__ import annotations

from typing import Any

# The HPC optimisation preset: the teamup session, not a template.
HPC_CREW = "hpc"
# The crew a ``teamup_start`` call without one selects.
DEFAULT_CREW = "research"

# crew → its built-in plan template (``teams/plan/templates/<name>.json``).
CREW_TEMPLATES: dict[str, str] = {
    "research": "research-round",
    "code": "code",
    "paper-writing": "paper-writing",
    "kakenhi-report": "kakenhi-report",
    "peer-review": "peer-review",
}

# Other spellings a user may type for a crew (kept from the crew packs).
CREW_ALIASES: dict[str, str] = {
    "research-team": "research",
    "science": "research",
    "coding": "code",
    "dev": "code",
    "development": "code",
    "claude-code": "code",
    "paper": "paper-writing",
    "manuscript": "paper-writing",
    "kaken": "kakenhi-report",
    "reporting": "kakenhi-report",
    "review": "peer-review",
    "referee": "peer-review",
}

HPC_DESCRIPTION = (
    "The HPC optimisation preset: a project manager, system engineer, programmer and "
    "code doctor (PM/SE/PG/CD) in an interactive teamup session with its SOTA files, "
    "hardware profile and ChangeLog, advanced iteration by iteration."
)

_CODE = {
    "architect": (
        "You are the architect of a coding team. Break the coding goal into "
        "small verifiable increments (3 to 5). For each increment state: the "
        "name, the files to touch, and an acceptance test (or measurable "
        "check). Fix the order so every increment lands testable. Never "
        "implement code yourself — the implementer lands each increment."
    ),
    "implementer": (
        "You are the implementer of a coding team. Take exactly ONE increment "
        "at a time: the smallest change satisfying its stated contract, with "
        "matching tests. Follow the idioms of THIS codebase (its base library, "
        "its test style, its lint budget) — never widen scope, never refactor "
        "what the increment does not touch. State what you changed and how it "
        "proves the acceptance check."
    ),
    "tester": (
        "You are the tester of a coding team. After each landed increment you "
        "run the project's REAL test suite (or the increment's tests) and "
        "report failures verbatim with file:line. Classify each failure: code "
        "bug / wrong expectation / environment. Never edit implementation code "
        "unless asked for a patch candidate against a named failure."
    ),
    "reviewer": (
        "You are the reviewer of a coding team. Review every increment diff as "
        "a strict code review: correctness, edge cases, security, conventions "
        "of the codebase (assert / honesty / complexity budget if the project "
        "declares one), and test sufficiency. Answer approve or name THE "
        "defect with the smallest fix — never vague criticism."
    ),
}

_PAPER = {
    "editor": (
        "You are the editor of a paper-writing team. You plan the paper: the sections "
        "it needs (introduction, related work, methods, experiments, discussion and "
        "conclusion, unless the subject calls for others), what each section must "
        "establish and the evidence it rests on. Ground the plan in the project's own "
        "material (knowledge_query and the files you can read) and cite what you read. "
        "Never draft prose yourself — the drafter writes each section."
    ),
    "drafter": (
        "You are the drafter of a paper-writing team. You write ONE section at a time "
        "as LaTeX-mergeable prose: precise, in the field's register, every factual "
        "claim tied to evidence you read in the project's material and cited. List each "
        "claim with the evidence behind it so the verifier can check it. Never invent "
        "a result, a number or a citation; say what is missing instead."
    ),
    "verifier": (
        "You are the verifier of a paper-writing team. For the section you are given, "
        "check every claim against its stated evidence and the project's material: is "
        "it supported, overstated or unsupported? Check that every citation names a "
        "source you can find, with its DOI when one is given. Report each problem with "
        "the claim, what is wrong and the smallest fix. Never rewrite the section."
    ),
    "copyeditor": (
        "You are the copy editor of a paper-writing team. You make the camera-ready "
        "pass over the drafted sections and the verifier's reports: consistent "
        "terminology and notation, tight prose, a coherent order and the format the "
        "venue expects. Carry every unresolved verifier finding into your notes rather "
        "than hiding it; never add a claim the drafts and their evidence do not hold."
    ),
}

_KAKENHI = {
    "lead": (
        "You are the lead of a KAKENHI (JSPS Grant-in-Aid) report team. You establish "
        "the baseline: the project's aims as funded, what has been achieved so far and "
        "what remains, each grounded in the project's own records (knowledge_query and "
        "the files you can read) and cited. At the end you fit the report to the form: "
        "its sections, in its order, within its length limits. Never claim an outcome "
        "the records do not show."
    ),
    "writer": (
        "You are the writer of a KAKENHI report team. You draft ONE section of the "
        "report from the baseline you are given: short, reference-heavy, with concrete "
        "outcomes and dates, in the register a JSPS reviewer expects. Never invent a "
        "publication, a dataset or a milestone."
    ),
    "checker": (
        "You are the checker of a KAKENHI report team. You check the drafted sections "
        "against the baseline and the project's records: every outcome and reference "
        "must be traceable, every milestone concrete and dated, and no section may "
        "contradict another. Report each issue with the section, the problem and the fix."
    ),
}

_PEER_REVIEW = {
    "chair": (
        "You are the chair of a peer-review team. First you read the submission and "
        "state, in one paragraph, what it claims and how it supports those claims. At "
        "the end you write the review a journal form takes: a summary, strengths, "
        "weaknesses, the reproducibility checklist, a recommendation (accept, minor "
        "revision, major revision or reject) with its justification, and a constructive "
        "response to the authors. Every judgement rests on the auditors' findings and "
        "the text of the submission."
    ),
    "auditor": (
        "You are an auditor on a peer-review team. You audit ONE aspect of the "
        "submission: its methods, the validity of its results, or its reproducibility "
        "(data availability, code, environment). Name the strengths and weaknesses of "
        "that aspect with the passage each rests on; be specific and constructive, and "
        "never review an aspect you were not given."
    ),
}

# crew → profile → system prompt: what ``crew_role:<crew>/<profile>`` names.
CREW_ROLE_PROMPTS: dict[str, dict[str, str]] = {
    "code": _CODE,
    "paper-writing": _PAPER,
    "kakenhi-report": _KAKENHI,
    "peer-review": _PEER_REVIEW,
}


def crew_name(name: str | None) -> str:
    """The canonical crew a name or alias selects; KeyError names the crews."""
    key = str(name or "").strip().lower()
    key = CREW_ALIASES.get(key, key)
    if key == HPC_CREW or key in CREW_TEMPLATES:
        return key
    raise KeyError(f"unknown crew {name!r} (known: {', '.join(known_crews())})")


def plan_crew_of(crew: Any) -> str | None:
    """The template crew a ``teamup_start`` call's ``crew`` selects, else None.

    The ONE reading of that argument (absent or empty is :data:`DEFAULT_CREW`).
    The permission gate and the tool's executor both call it (ADR-092 slice
    4): a call the gate leaves to the plan's own card is exactly a call the
    executor runs as that validated plan, and anything else (``hpc``, an
    unknown crew) keeps ``teamup_start``'s own rule.
    """
    try:
        name = crew_name(crew or DEFAULT_CREW)
    except KeyError:
        return None
    return name if name in CREW_TEMPLATES else None


def crew_template(name: str | None) -> str | None:
    """The plan template a crew runs, or None for the ``hpc`` preset."""
    return CREW_TEMPLATES.get(crew_name(name))


def known_crews() -> list[str]:
    """Every canonical crew name: ``hpc`` and the template crews, sorted."""
    return sorted({HPC_CREW, *CREW_TEMPLATES})


def crew_role_prompt(crew: str, profile: str) -> str:
    """The prompt ``crew_role:<crew>/<profile>`` names; KeyError when none."""
    prompts = CREW_ROLE_PROMPTS.get(crew)
    if prompts is None or profile not in prompts:
        known = sorted(f"{c}/{p}" for c, ps in CREW_ROLE_PROMPTS.items() for p in ps)
        raise KeyError(f"no crew role {crew}/{profile}; one of {known}")
    return prompts[profile]


def list_crews() -> list[dict[str, Any]]:
    """Every crew, ``hpc`` first: name, template, aliases, description,
    phases and params (read from the template, the one source)."""
    from .plan.templates import load_template

    rows: list[dict[str, Any]] = [
        {
            "name": HPC_CREW,
            "template": None,
            "aliases": [],
            "description": HPC_DESCRIPTION,
            "phases": [],
            "params": {},
        }
    ]
    for crew in sorted(CREW_TEMPLATES):
        plan = load_template(CREW_TEMPLATES[crew])
        rows.append(
            {
                "name": crew,
                "template": CREW_TEMPLATES[crew],
                "aliases": sorted(a for a, c in CREW_ALIASES.items() if c == crew),
                "description": str(plan.get("description") or ""),
                "phases": [str(p.get("title") or p.get("id")) for p in plan.get("phases") or []],
                "params": dict(plan.get("params") or {}),
            }
        )
    return rows
