# Demo 99: Cleanup

Inspect the final endpoint state and optionally stop the endpoint that
the demo suite reused or provisioned.

## Quick Start

```bash
# Just inspect
bash run_demo.sh

# Tear down the endpoint
bash run_demo.sh --stop-endpoint
```

## Detailed Step-by-Step (the whole suite contract)

Run ONLY after the previous demos are complete; it removes the demo-project
endpoint so your account doesn't leave GPU allocated:

```bash
bash run_demo.sh --stop-endpoint
```

The script reads the recorded endpoint id (from demo00/01 logs) and issues:
```bash
r4 endpoint down <id> --wait
r4 workflow cancel <id>   # if any execution is still warm
```

Idempotent — safe to re-run; does not touch other users' endpoints.
