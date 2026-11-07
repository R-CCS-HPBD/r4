#!/usr/bin/env bash
# Demo 09: Vision Inference (SAM3.1) & Interactive Gradio UI
# Submit a text-prompted SAM3.1 segmentation task through the Gateway vision
# path, then show the interactive browser UI (`r4 vision ui`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEMOS_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 09: Vision Inference (SAM3.1) & Interactive UI" \
    "Text-prompted segmentation via r4 vision submit, plus the Gradio UI."

ensure_demo_project

VISION_ADAPTER="${R4_DEMO_VISION_ADAPTER:-sam3}"
VISION_PROMPT="${R4_DEMO_VISION_PROMPT:-cat}"

# ---------------------------------------------------------------------------
# Input image resolution: env override -> co-located sample -> umrs sample
# ---------------------------------------------------------------------------
resolve_demo_image() {
    if [[ -n "${R4_DEMO_VISION_IMAGE:-}" ]]; then
        printf '%s\n' "$R4_DEMO_VISION_IMAGE"
        return 0
    fi
    local candidate
    for candidate in \
        "$SCRIPT_DIR/sample_image.png" \
        "$REPO_ROOT/umrs/demos/03_sam3_segmentation/.upload_staging/Cat_1.jpg" \
        "$REPO_ROOT/umrs/demos/04_vision_inference/.upload_staging/Cat_1.jpg"; do
        [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
    done
    # Last resort: synthesize a small test image with Pillow if available.
    python3 - "$SCRIPT_DIR/sample_image.png" <<'PY' || return 1
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit(1)

path = sys.argv[1]
img = Image.new("RGB", (256, 256), "white")
draw = ImageDraw.Draw(img)
draw.ellipse((60, 60, 196, 196), fill="gray", outline="black", width=3)
img.save(path)
print(path)
PY
}

demo_step "Check vision-capable models registered on the platform"
demo_try_cmd r4 model list --type vision

demo_step "Resolve the input image"
if ! IMAGE="$(resolve_demo_image)"; then
    demo_die "No input image found. Set R4_DEMO_VISION_IMAGE to an image path."
fi
demo_info "Input image: $IMAGE"

demo_step "Submit SAM3.1 text-prompted segmentation (prompt: '$VISION_PROMPT')"
demo_note "The submit response lists the S3 result paths (outputs[].uri) up front."
SUBMIT_JSON="$(r4 vision submit "$IMAGE" --adapter "$VISION_ADAPTER" \
    --config "prompt=$VISION_PROMPT" --config threshold=0.5 --json 2>/dev/null || true)"
demo_try_cmd r4 vision submit "$IMAGE" --adapter "$VISION_ADAPTER" \
    --config "prompt=$VISION_PROMPT" --config threshold=0.5

TASK_ID=""
if [[ -n "$SUBMIT_JSON" ]]; then
    TASK_ID="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
task = data.get("task") or {}
print(task.get("task_id") or task.get("id") or "")
' <<<"$SUBMIT_JSON")"
fi

if [[ -n "$TASK_ID" ]]; then
    demo_step "Check task status"
    demo_try_cmd r4 vision status "$TASK_ID"
else
    demo_note "No task_id returned (vision endpoint may not be running) — skipping status check."
fi

# ---------------------------------------------------------------------------
# Interactive Gradio UI
# ---------------------------------------------------------------------------
demo_step "Interactive browser UI"
demo_note "The same flow is available interactively: upload an image, type a"
demo_note "prompt (e.g. '$VISION_PROMPT'), pick the adapter, and click Run."
demo_note "Results show live status, copyable s3:// paths, and image previews."
echo
echo "  pip install gradio    # one-time: installs gradio"
echo "  r4 vision ui --adapter $VISION_ADAPTER"
echo
if [[ "${R4_DEMO_LAUNCH_UI:-0}" = "1" ]]; then
    demo_note "R4_DEMO_LAUNCH_UI=1 — launching the UI now (Ctrl+C to stop)."
    demo_try_cmd r4 vision ui --adapter "$VISION_ADAPTER"
else
    demo_note "Set R4_DEMO_LAUNCH_UI=1 to launch the UI from this script (blocking)."
fi

echo
echo "Fetch results later with: r4 vision get <task_id>"
echo "Optional cleanup: ../99_cleanup/run_demo.sh --stop-endpoint"
