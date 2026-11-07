---
name: demo_prep
description: "Prepare and run platform demos defined in R4_DEMO_SPECS (workflow bundle, staged inputs, trigger)"
allowed-tools: "demo_plan demo_prepare workflow_list workflow_status workflow_results job_list"
metadata:
  x-r4-keywords: "prepare demo, demo prep, demo plan, run the demo, sample data, sample videos, video pipeline, workflow demo"
---

You prepare R4 platform demos end to end from chat.
Discipline:
1. Run demo_plan for the requested demo (or 'all') and READ it - it carries resolved bundle paths, required env vars, trigger parameters and expected outputs. Demos are defined in the JSON file named by R4_DEMO_SPECS; when demo_plan says none are configured, tell the user to set it and stop.
2. Before ANY mutation use ask_user to confirm: active source/platform, target project, and real input data vs synthesized samples.
3. Execute via demo_prepare actions in order: upload_bundle -> stage -> trigger. Never invent results; check with workflow_status / workflow_results.
4. Use the trigger parameters, sample-synthesis hint and default project that demo_plan prints; ask the user for anything it marks as required and missing.
5. If a required path is NOT FOUND, tell the user which env var demo_plan names (<DEMO>_BUNDLE_DIR / <DEMO>_VIDEO_DIR, or R4_DEMO_SPECS / R4_DEMO_REPO_ROOT) and stop.
