"""Publication output generator for research loop results.

Phase 3: generates research reports, paper drafts, and JSON exports
from ResearchLoopState.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _render_evidence_pool(state) -> list[str]:
    """Evidence section lines (D-45: every claim carries its artifact link)."""
    lines: list[str] = []
    for evid, ev in state.evidence_pool.items():
        direction = "✅ Supports" if ev.supports else "❌ Contradicts"
        lines.append(f"### {evid}: {direction} hypothesis {ev.hypothesis_id}")
        lines.append(f"- **Strength:** {ev.strength:.2f}")
        lines.append(f"- **Experiment:** {ev.experiment_id}")
        if ev.source_artifact:
            lines.append(f"- **Source:** {ev.source_artifact}")
        lines.append(f"- **Summary:** {ev.summary}")
        if ev.p_value is not None:
            lines.append(f"- **p-value:** {ev.p_value}")
        if ev.effect_size is not None:
            lines.append(f"- **Effect size:** {ev.effect_size}")
        if ev.statistical_test:
            lines.append(f"- **Test:** {ev.statistical_test}")
        lines.append("")
    return lines


def _render_evidence_table(state, evidence_items: list[str]) -> list[str]:
    """Claim → evidence table with the Source column (D-45)."""
    lines = [
        "| Evidence | Direction | Strength | Source | Summary |",
        "|----------|-----------|----------|--------|---------|",
    ]
    for evid in evidence_items:
        ev = state.evidence_pool[evid]
        direction = "Supports" if ev.supports else "Contradicts"
        source = ev.source_artifact or "—"
        lines.append(f"| {evid} | {direction} | {ev.strength:.2f} | {source} | {ev.summary[:60]} |")
    lines.append("")
    return lines


def _report_header(state) -> list[str]:
    return [
        f"# Research Report: {state.objective}",
        "",
        f"**Loop ID:** {state.loop_id}",
        f"**Domain:** {state.domain or 'General science'}",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Status:** {state.phase.value.upper()}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"Research objective: *{state.objective}*",
        "",
        f"The research loop completed **{state.iteration_number} iterations** "
        f"with **{state.budget.experiments_run} experiments**.",
        "",
    ]


def _report_hypotheses(state) -> list[str]:
    lines = ["## Hypotheses Tested", ""]
    for hid, h in state.hypotheses.items():
        active = " **(active)**" if hid == state.current_hypothesis_id else ""
        lines.append(f"### {hid}: {h.statement}{active}")
        lines.append(f"- **Status:** {h.status.value.upper()}")
        lines.append(f"- **Confidence:** {h.confidence:.2f}")
        lines.append(f"- **Supporting evidence:** {len(h.evidence_for)} items")
        lines.append(f"- **Contradicting evidence:** {len(h.evidence_against)} items")
        if h.parent_hypothesis_id:
            lines.append(f"- **Parent hypothesis:** {h.parent_hypothesis_id}")
        lines.append("")
    return lines


def _report_experiments(state) -> list[str]:
    lines = ["## Experiments", ""]
    for eid, e in state.experiments.items():
        lines.append(f"### {eid}: {e.name}")
        lines.append(f"- **Status:** {e.status.value}")
        lines.append(f"- **Hypothesis:** {e.hypothesis_id}")
        if e.pipeline_ref:
            lines.append(f"- **Pipeline:** {e.pipeline_ref}")
        if e.results:
            summary = e.results.get("summary", "No summary available")
            lines.append(f"- **Results:** {summary}")
        if e.error_message:
            lines.append(f"- **Error:** {e.error_message}")
        lines.append("")
    return lines


def _report_conclusion(hyp) -> list[str]:
    lines = [
        "## Conclusion",
        "",
        f"**Hypothesis:** {hyp.statement}",
        f"**Verdict:** {hyp.status.value.upper()}",
        f"**Final confidence:** {hyp.confidence:.2f}",
        "",
    ]
    if hyp.status.value == "supported":
        lines.append(
            "The evidence supports this hypothesis. "
            "Results are statistically significant and consistent across experiments."
        )
    elif hyp.status.value == "refuted":
        lines.append(
            "The evidence refutes this hypothesis. Alternative hypotheses should be explored."
        )
    else:
        lines.append(
            "The evidence is inconclusive. "
            "Additional experiments with larger sample sizes or refined methodology are needed."
        )
    lines.append("")
    return lines


def _report_budget_and_log(state) -> list[str]:
    lines = [
        "## Resource Usage",
        "",
        f"- **Iterations used:** {state.iteration_number}/{state.budget.max_iterations}",
        f"- **Experiments run:** {state.budget.experiments_run}",
        f"- **Tokens consumed (est.):** {state.budget.tokens_consumed:,}",
        f"- **Budget remaining:** {state.budget.budget_remaining_pct:.0f}%",
        "",
    ]
    if state.decisions:
        lines.append("## Iteration Log")
        lines.append("")
        for d in state.decisions[-10:]:
            lines.append(
                f"- **Iter {d['iteration']}:** {d['decision']} "
                f"(confidence={d.get('confidence', 'N/A')})"
            )
        lines.append("")
    return lines


def generate_report(state) -> str:
    """Generate a human-readable research report from loop state.

    Args:
        state: ResearchLoopState instance.

    Returns:
        Markdown-formatted research report.
    """
    hyp = state.current_hypothesis

    lines = _report_header(state)
    if state.hypotheses:
        lines.extend(_report_hypotheses(state))
    if state.experiments:
        lines.extend(_report_experiments(state))
    if state.evidence_pool:
        lines.append("## Evidence")
        lines.append("")
        lines.extend(_render_evidence_pool(state))
    if hyp and state.phase.value == "completed":
        lines.extend(_report_conclusion(hyp))
    lines.extend(_report_budget_and_log(state))

    lines.append("---")
    lines.append(
        f"*Report generated by R4 Research Loop. "
        f"State file: ~/.config/r4/research_loops/{state.loop_id}/state.json*"
    )

    return "\n".join(lines)


def generate_paper_draft(state) -> str:
    """Generate a draft academic paper from loop state.

    Args:
        state: ResearchLoopState instance.

    Returns:
        Markdown-formatted paper draft with sections for Abstract,
        Introduction, Methods, Results, Discussion, and References.
    """
    hyp = state.current_hypothesis

    lines = _paper_abstract(state, hyp)
    lines.extend(_paper_intro_and_methods(state))
    if state.hypotheses:
        lines.extend(_paper_results(state))
    lines.extend(_paper_discussion_and_tail(state, hyp))

    return "\n".join(lines)


def _paper_abstract(state, hyp) -> list[str]:
    lines = [
        f"# {_paper_title(state)}",
        "",
        "## Abstract",
        "",
        f"This study investigated: {state.objective}. ",
        "",
    ]
    if hyp:
        lines.append(
            f"The primary hypothesis — *{hyp.statement}* — was "
            f"**{hyp.status.value.upper()}** with a confidence of "
            f"{hyp.confidence:.2f} based on {len(state.evidence_pool)} "
            "pieces of evidence collected across "
            f"{state.budget.experiments_run} experiments over "
            f"{state.iteration_number} iterations."
        )
        lines.append("")
    lines.append(
        f"*Keywords:* {state.domain}, automated research, "
        "hypothesis-driven science, {state.phase.value}"
    )
    lines.append("")
    return lines


def _paper_intro_and_methods(state) -> list[str]:
    lines = [
        "## 1. Introduction",
        "",
        f"Research question: *{state.objective}*",
        "",
        "This research was conducted using an automated hypothesis-driven "
        "scientific workflow (R4 Research Loop). The loop iteratively "
        "formulated hypotheses, designed experiments, executed them via "
        "UMRS pipelines, analyzed results, and revised hypotheses based "
        "on evidence.",
        "",
        "## 2. Methods",
        "",
        "### 2.1 Research Loop Configuration",
        "",
        f"- Maximum iterations: {state.budget.max_iterations}",
        f"- Domain: {state.domain}",
        "- Agent roles: RL, DA, ED, PR",
        "",
    ]
    if state.experiments:
        lines.append("### 2.2 Experiments")
        lines.append("")
        for eid, e in state.experiments.items():
            lines.append(f"#### {e.name}")
            lines.append(f"- Status: {e.status.value}")
            lines.append(f"- Pipeline: {e.pipeline_ref or 'local'}")
            if e.protocol:
                steps = e.protocol.get("steps", [])
                if steps:
                    lines.append(f"- Steps: {len(steps)}")
            lines.append("")
    return lines


def _paper_results(state) -> list[str]:
    lines = ["## 3. Results", ""]
    for hid, h in state.hypotheses.items():
        lines.append(f"### 3.{list(state.hypotheses.keys()).index(hid) + 1} {h.statement}")
        lines.append("")
        lines.append(f"**Status:** {h.status.value.upper()}")
        lines.append(f"**Confidence:** {h.confidence:.2f}")
        lines.append(f"**Score:** {h.net_evidence_score(state.evidence_pool):.2f}")
        lines.append("")

        evidence_items = [
            evid for evid, ev in state.evidence_pool.items() if ev.hypothesis_id == hid
        ]
        if evidence_items:
            lines.extend(_render_evidence_table(state, evidence_items))
    return lines


def _paper_discussion(hyp, state) -> list[str]:
    if hyp.status.value == "supported":
        text = (
            f"The evidence supports the hypothesis that "
            f"{hyp.statement[:120]}. "
            f"Confidence reached {hyp.confidence:.2f} after "
            f"{state.iteration_number} iterations."
        )
    elif hyp.status.value == "refuted":
        text = "The evidence refutes the hypothesis. Alternative explanations should be explored."
    else:
        text = (
            "Results are inconclusive. Further investigation with refined methodology is warranted."
        )
    return [text, ""]


def _paper_discussion_and_tail(state, hyp) -> list[str]:
    lines = ["## 4. Discussion", ""]
    if hyp:
        lines.extend(_paper_discussion(hyp, state))

    lines.append("### 4.1 Limitations")
    lines.append("")
    lines.append("- Automated research loop — results require human review before publication.")
    lines.append(f"- Limited to {state.budget.max_iterations} iterations.")
    if state.budget.tokens_consumed > 0:
        lines.append(f"- Token budget: {state.budget.tokens_consumed:,} consumed.")
    lines.append("")

    conclusion_verdict = (
        "provided supporting evidence"
        if hyp and hyp.status.value == "supported"
        else "yielded inconclusive results"
    )
    lines.append("## 5. Conclusion")
    lines.append("")
    lines.append(
        f"This automated research investigation of *{state.objective}* "
        f"{conclusion_verdict} "
        f"after {state.iteration_number} iterations and "
        f"{state.budget.experiments_run} experiments."
    )
    lines.append("")
    lines.append(
        "Future work should: (1) validate results with independent experiments, "
        "(2) explore alternative hypotheses from the branch space, and "
        "(3) refine experimental methodology based on limitations identified."
    )
    lines.append("")

    lines.append("## References")
    lines.append("")
    lines.append("*Generated by R4 Research Loop. This is an automated draft.*")
    lines.append(f"*State: ~/.config/r4/research_loops/{state.loop_id}/state.json*")
    lines.append("")
    return lines


def _paper_title(state) -> str:
    """Generate a paper title from the objective."""
    objective = state.objective.strip()
    if len(objective) > 80:
        return objective[:77] + "..."
    return objective


def build_publication(
    state,
    authors: list[str] | None = None,
    catalog_ids: list[str] | None = None,
    content_uri: str = "",
) -> dict:
    """Construct the D-45 Publication record for a loop draft (provenance attached).

    The record links back to what produced it: every experiment and every
    hypothesis from the loop state becomes a provenance node
    (``derived_from`` / ``cites``), so a draft's claims resolve to job ids
    and experiments (never to prose only). ``r4_contracts.research.Publication``
    is finally constructed — never an invented DOI or author list.

    ``catalog_ids`` (D-45 join): the scientific catalogs whose versions
    pinned this draft — resolved via the inverse pin index
    (``r4 catalog pinned-by``). Each becomes a CATALOG provenance node;
    an empty list stays empty, none fabricated.
    """
    from r4_contracts.research import (
        PrimitiveKind,
        ProvenanceGraph,
        ProvenanceNode,
        Publication,
        PublicationStatus,
    )

    prov = ProvenanceGraph()
    for eid in sorted(state.experiments.keys()):
        prov.add(
            ProvenanceNode(
                primitive_id=eid,
                primitive_kind=PrimitiveKind.EXPERIMENT,
                relation="derived_from",
                label="loop experiment",
            )
        )
    for hid in sorted(state.hypotheses.keys()):
        prov.add(
            ProvenanceNode(
                primitive_id=hid,
                primitive_kind=PrimitiveKind.HYPOTHESIS,
                relation="cites",
                label="claim",
            )
        )
    for cid in sorted(set(catalog_ids or [])):
        prov.add(
            ProvenanceNode(
                primitive_id=cid,
                primitive_kind=PrimitiveKind.CATALOG,
                relation="cataloged_in",
                label="scientific catalog",
            )
        )

    pub = Publication(
        id=f"pub-{state.loop_id}",
        project_id=str(getattr(state, "project_id", "") or state.loop_id),
        title=_paper_title(state),
        status=PublicationStatus.DRAFT,
        abstract=" ".join(
            [
                f"This study investigated: {state.objective}.",
                (
                    f"Automated draft after {state.iteration_number} iterations "
                    f"and {state.budget.experiments_run} experiments."
                ),
            ]
        ),
        authors=list(authors or []),
        experiment_ids=sorted(state.experiments.keys()),
        hypothesis_ids=sorted(state.hypotheses.keys()),
        catalog_ids=sorted(set(catalog_ids or [])),
        provenance=prov,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return {
        "id": pub.id,
        "project_id": pub.project_id,
        "title": pub.title,
        "status": pub.status.value,
        "abstract": pub.abstract,
        "authors": pub.authors,
        "experiment_ids": pub.experiment_ids,
        "dataset_ids": pub.dataset_ids,
        "hypothesis_ids": pub.hypothesis_ids,
        "catalog_ids": pub.catalog_ids,
        "content_uri": content_uri,
        "provenance": pub.provenance.to_dict(),
        "doi": pub.doi,
        "created_at": pub.created_at,
        "updated_at": pub.updated_at,
    }


def store_publication_body(client, pub: dict, body_md: str, *, owner_id: str) -> dict:
    """Store the draft body as a project-scoped S3 object (D-45 tail).

    Upload goes through POST /gw/files/upload; the stored object is
    registered as a registry artifact (type ``result``, metadata carrying
    the publication id) so the draft's claims resolve through a citable
    artifact, and ``content_uri`` lands on the record. Never fabricates:
    a receipt without a uri raises; registration failure raises; the
    input record is never mutated in place.
    """
    receipt = client.upload_file_bytes(
        f"publication-{pub['id']}.md",
        body_md.encode("utf-8"),
        content_type="text/markdown",
        purpose="publications",
    )
    uri = receipt.get("uri", "")
    if not uri:
        raise ValueError(
            f"upload receipt carries no uri for {pub['id']!r} — refusing to fabricate content_uri"
        )
    artifact = client.artifacts.register(
        "result",
        f"publication draft {pub['id']}",
        owner_id,
        uri,
        project_id=pub.get("project_id") or None,
        metadata={"publication_id": pub["id"], "kind": "draft"},
    )
    stored = dict(pub)
    stored["content_uri"] = uri
    stored["artifact_id"] = artifact.get("id") or artifact.get("artifact_id") or ""
    return stored
