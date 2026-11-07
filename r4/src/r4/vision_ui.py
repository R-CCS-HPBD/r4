"""Local Gradio UI for platform vision inference (``r4 vision ui``).

Design note
-----------
The UI deliberately runs on the *client* side (r4 SDK -> Gateway
``/gw/vision/*`` + ``/gw/files/*``), NOT inside the BentoML GPU container.

Mounting Gradio directly on the BentoML service
(``bentoml.gradio.mount_gradio_app``) is possible but would:

* expose compute-node ports / require a websocket proxy chain through the
  Gateway (clients must never talk to BentoML directly),
* ship UI dependencies inside the offline inference sandbox, and
* die with each ephemeral Slurm mini-RiVault endpoint.

Serving the UI at the client keeps Gateway auth + audit intact and preserves
the S3 result contract: the submit response already returns
``outputs[].uri`` (``s3://bucket/key``), which this UI surfaces verbatim.
Previews are fetched through the audited ``/gw/files/download/{key}`` route.

Gradio is an optional dependency: ``pip install gradio``.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import R4Error

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 600.0
_TERMINAL_STATUSES = {"success", "failure", "cancelled", "canceled", "error"}
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_DEFAULT_ADAPTERS = ("hmae", "sam3", "huggingface")


def _require_gradio():
    try:
        import gradio
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise R4Error("Gradio is not installed. Install it with: pip install gradio") from exc
    return gradio


def _task_id_from_submit(response: dict[str, Any]) -> str:
    task = response.get("task") if isinstance(response, dict) else None
    if isinstance(task, dict):
        task_id = str(task.get("task_id") or task.get("id") or "")
        if task_id:
            return task_id
    raise R4Error(f"Submit response did not include a task_id: {response!r}")


def _status_value(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("status") or "").lower()
    return ""


def poll_until_terminal(
    vision_client: Any,
    task_id: str,
    *,
    model: str | None = None,
    endpoint_id: str | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Poll task status until it reaches a terminal state or times out."""
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        response = vision_client.status(task_id, model=model, endpoint_id=endpoint_id)
        status = _status_value(response)
        if status in _TERMINAL_STATUSES:
            return status
        time.sleep(poll_interval)
    return status or "timeout"


def fetch_previews(
    vision_client: Any,
    outputs: list[dict[str, Any]],
    *,
    dest_dir: str | None = None,
) -> list[tuple[str, str]]:
    """Download image output artifacts for preview.

    Returns a list of ``(local_path, s3_uri)`` tuples. Non-image artifacts
    and artifacts that are not (yet) present are skipped silently.
    """
    directory = Path(dest_dir or tempfile.mkdtemp(prefix="r4-vision-ui-"))
    previews: list[tuple[str, str]] = []
    for artifact in outputs:
        key = str(artifact.get("key") or "")
        uri = str(artifact.get("uri") or key)
        if not key.lower().endswith(_IMAGE_SUFFIXES):
            continue
        try:
            data = vision_client.download_file(key)
        except Exception:  # noqa: BLE001 - artifact may not exist yet
            continue
        local_path = directory / Path(key).name
        local_path.write_bytes(data)
        previews.append((str(local_path), uri))
    return previews


def run_inference_flow(
    vision_client: Any,
    image_path: str,
    *,
    adapter: str,
    inference_config: dict[str, Any] | None = None,
    model: str | None = None,
    endpoint_id: str | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Submit an image, wait for completion, and collect S3 outputs + previews.

    This is the UI-independent core so it can be unit-tested without Gradio.
    """
    submit_response = vision_client.submit(
        image_path,
        model=model or None,
        endpoint_id=endpoint_id or None,
        adapter=adapter,
        inference_config=inference_config or {},
    )
    task_id = _task_id_from_submit(submit_response)
    outputs = list(submit_response.get("outputs") or [])

    status = poll_until_terminal(
        vision_client,
        task_id,
        model=model,
        endpoint_id=endpoint_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )

    previews: list[tuple[str, str]] = []
    if status == "success":
        previews = fetch_previews(vision_client, outputs)

    return {
        "task_id": task_id,
        "status": status,
        "endpoint_id": submit_response.get("endpoint_id"),
        "endpoint_model": submit_response.get("endpoint_model"),
        "adapter": submit_response.get("adapter") or adapter,
        "outputs": outputs,
        "output_uris": [a.get("uri") for a in outputs if a.get("uri")],
        "previews": previews,
    }


def _parse_config_json(config_json: str) -> dict[str, Any]:
    text = (config_json or "").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Inference config must be a JSON object")
    return data


def merge_ui_config(
    config: dict[str, Any],
    *,
    prompt: str = "",
    task: str = "",
) -> dict[str, Any]:
    """Merge the dedicated UI fields into the inference config.

    Dedicated fields win over the advanced JSON so what the user sees in the
    form is what gets submitted (``prompt`` for promptable adapters such as
    sam3, ``task`` for multi-mode adapters such as hmae).
    """
    merged = dict(config)
    if prompt.strip():
        merged["prompt"] = prompt.strip()
    if task.strip():
        merged["task"] = task.strip()
    return merged


def _validated_ui_inputs(gr, image_path, adapter, config_json) -> tuple[str, str, dict]:
    """Validate raw Gradio inputs, mapping problems to gr.Error."""
    if not image_path:
        raise gr.Error("Choose an input image first.")
    adapter_name = str(adapter).strip()
    if not adapter_name:
        raise gr.Error("Choose a vision adapter.")
    try:
        config = _parse_config_json(config_json)
    except (ValueError, json.JSONDecodeError) as exc:
        raise gr.Error(f"Invalid inference config: {exc}") from exc
    return str(image_path), adapter_name, config


def _format_outcome(outcome: dict[str, Any]) -> tuple[str, str, list, dict]:
    """Shape an inference outcome into the four Gradio output components."""
    status_line = (
        f"task {outcome['task_id']} — {outcome['status']} "
        f"(endpoint {outcome['endpoint_id']} / {outcome['endpoint_model']})"
    )
    uris_text = "\n".join(outcome["output_uris"]) or "(no outputs declared)"
    gallery = [(path, uri) for path, uri in outcome["previews"]]
    raw = {k: v for k, v in outcome.items() if k != "previews"}
    return status_line, uris_text, gallery, raw


def build_app(
    *,
    default_adapter: str = "hmae",
    default_model: str = "",
    default_endpoint_id: str = "",
    timeout: float = DEFAULT_TIMEOUT,
):
    """Build the Gradio Blocks app (lazy import; returns gr.Blocks)."""
    gr = _require_gradio()

    from .vision import VisionClient

    vision_client = VisionClient()

    def _run(image_path, adapter, prompt, task, config_json, model, endpoint_id):
        image, adapter_name, config = _validated_ui_inputs(gr, image_path, adapter, config_json)
        config = merge_ui_config(config, prompt=str(prompt or ""), task=str(task or ""))
        try:
            outcome = run_inference_flow(
                vision_client,
                image,
                adapter=adapter_name,
                inference_config=config,
                model=str(model).strip() or None,
                endpoint_id=str(endpoint_id).strip() or None,
                timeout=timeout,
            )
        except R4Error as exc:
            raise gr.Error(str(exc)) from exc

        return _format_outcome(outcome)

    with gr.Blocks(title="R4 Vision Inference") as app:
        gr.Markdown(
            "## R4 Vision Inference\n"
            "Runs through the Platform Gateway (`/gw/vision/*`). "
            "Results stay in project S3 storage — the `s3://` object keys "
            "below are the canonical result paths."
        )
        with gr.Row():
            with gr.Column():
                image_in = gr.Image(type="filepath", label="Input image")
                adapter_in = gr.Dropdown(
                    choices=list(_DEFAULT_ADAPTERS),
                    value=default_adapter,
                    allow_custom_value=True,
                    label="Vision adapter",
                )
                prompt_in = gr.Textbox(
                    value="",
                    label="Prompt (promptable adapters, e.g. sam3: 'cell')",
                    placeholder="Text query for segmentation/detection adapters",
                )
                task_in = gr.Dropdown(
                    choices=["", "reconstruction", "mae_reconstruction", "feature_extraction"],
                    value="reconstruction",
                    allow_custom_value=True,
                    label="Task (multi-mode adapters, e.g. hmae)",
                )
                config_in = gr.Textbox(
                    value="",
                    label="Advanced inference config (JSON, optional)",
                    placeholder='{"threshold": 0.5}',
                    lines=3,
                )
                model_in = gr.Textbox(value=default_model, label="Endpoint model (optional)")
                endpoint_in = gr.Textbox(value=default_endpoint_id, label="Endpoint ID (optional)")
                run_btn = gr.Button("Run inference", variant="primary")
            with gr.Column():
                status_out = gr.Textbox(label="Status", interactive=False)
                uris_out = gr.Textbox(
                    label="S3 result paths", interactive=False, lines=4, show_copy_button=True
                )
                gallery_out = gr.Gallery(label="Output previews", columns=2)
                raw_out = gr.JSON(label="Raw response")

        run_btn.click(
            _run,
            inputs=[image_in, adapter_in, prompt_in, task_in, config_in, model_in, endpoint_in],
            outputs=[status_out, uris_out, gallery_out, raw_out],
        )

    return app


def launch_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 7860,
    default_adapter: str = "hmae",
    default_model: str = "",
    default_endpoint_id: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Build and launch the local Gradio UI (blocking)."""
    app = build_app(
        default_adapter=default_adapter,
        default_model=default_model,
        default_endpoint_id=default_endpoint_id,
        timeout=timeout,
    )
    # Local-only by default: no public share links from a platform client.
    app.launch(server_name=host, server_port=port, share=False, show_api=False)
