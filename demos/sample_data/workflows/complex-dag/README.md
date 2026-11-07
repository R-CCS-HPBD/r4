# R4 Complex DAG Demo

A five-step UMRS DAG workflow that demonstrates parallel analysis branches
and fan-in synthesis using built-in LLM step templates.

## DAG Structure

```
collect_context
      ├── methods_review   (parallel)
      └── risk_review      (parallel)
              └── merge_findings
                      └── final_report
```

## Steps

1. **collect_context** — Creates a compact research context brief.
2. **methods_review** — Extracts methods, inputs, and reproducibility checks.
3. **risk_review** — Identifies risks, missing inputs, and validation gates.
4. **merge_findings** — Merges both analyses into a unified execution plan.
5. **final_report** — Produces a structured JSON report.

## Model Placeholder

The steps name no `model`, so the platform routes each LLM step to a served
model (2026-10-02; this copy used a `{{model}}` placeholder that nothing
replaced on a direct upload). `parameter_schema` declares the two values a
run needs, `sample_id` and `question`; `project_id` comes from your project.

## Usage

`demos/06_complex_workflow/run_demo.sh` uploads its own copy (with the model
it picks); this one uploads as is:

```bash
r4 workflow upload demos/sample_data/workflows/complex-dag --name r4-complex-dag-demo
r4 workflow trigger r4-complex-dag-demo \
  --param project_id=my-project \
  --param sample_id=sample-001 \
  --param "question=prepare a reproducible workflow"
```
