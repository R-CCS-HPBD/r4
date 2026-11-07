# The VLM path — image inputs through the LLM proxy (D-10)

**Status:** ACTIVE (2026-09-23) · **Design:** [R4-UMRS-DESIGN.md](R4-UMRS-DESIGN.md) gap row D-10 ·
**Pins:** `umrs/tests/test_infer_proxy_vlm.py` (multimodal bodies forward verbatim)

The platform serves vision-language models (VLMs) as a **first-class endpoint
kind**: the same chat-completions lane as text LLMs, with image inputs riding
the LLM proxy untouched. This document names the full path.

## The path, end to end

1. **Registry detection.** The model registry detects vision-capable
   architectures ([`umrs/model_registry/detect.py`](../umrs/src/umrs/model_registry/detect.py))
   and stamps `capabilities.supports_vision` on the catalog entry
   ([`ModelCapabilities`](../umrs/src/umrs/model_registry/capabilities.py)).
   Vision architectures include LLaVA, Qwen-VL, MoonViT (Kimi vision), Gemma
   multimodal, and CogVLM families; detection also matches known
   vision-config keys when the architecture string alone is ambiguous.

2. **Serving.** Vision-capable models are served by vLLM in the same
   system/project/user tiers as text LLMs. Multimodal chat requires the
   string content-format chat template (`requires_chat_template_content_format`
   in `ModelCapabilities` → vLLM `--chat-template-content-format string`),
   which is the vLLM requirement for image parts in prompts.

3. **The proxy forwards image inputs verbatim.** The LLM proxy
   ([`umrs/infer_proxy/app.py`](../umrs/src/umrs/infer_proxy/app.py)) is a
   credential-injecting **byte-stream forwarder**: the request body is
   streamed to the upstream unmodified, so OpenAI-compatible multimodal
   content parts (both `image_url` references and base64 `data:` URIs)
   reach the model server byte-identical. This is pinned end-to-end:
   the suite asserts the forwarded body equals the sent body for a
   chat containing all three part kinds (text, URL image, base64 image).

4. **Client usage.** Any OpenAI-compatible client works — send a
   chat-completions request through the session lease path:

   ```json
   {
     "model": "vlm-8b",
     "messages": [
       {
         "role": "user",
         "content": [
           {"type": "text", "text": "Describe this figure."},
           {"type": "image_url", "image_url": {"url": "https://…/fig.png"}}
         ]
       }
     ]
   }
   ```

   Base64-embedded images use the `data:image/png;base64,…` URI form in the
   same `image_url` slot.

## Policy families

- Chat with image parts rides the **chat-completions** policy family
  (the proxy's per-family allowlists treat it as chat traffic — the body
  is opaque to the proxy).
- Image *generation* endpoints (`/v1/images/…`) are their own policy family
  ([`policies.py`](../umrs/src/umrs/infer_proxy/policies.py)); they are a
  different surface from VLM *inputs* and are not part of this path.

## The two vision lanes (do not conflate)

| Lane | What it is | Path |
|---|---|---|
| **VLM (this document)** | LLM chat completions with image inputs — a model server kind | LLM proxy → vLLM, `supports_vision` models |
| **Vision service** | Domain vision *services* (SAM3, MAE, HMAE adapters) — BentoML task APIs, not chat | `umrs/src/umrs/vision_service/`, durable `vision_infer` jobs |

A figure-understanding question to a VLM is chat; a segmentation job is the
vision service. Both are first-class; neither routes through the other.

## What this document closes

Gap row D-10's named tail: "Document and test the VLM path (image inputs
through the LLM proxy) as a first-class endpoint kind." The path above is
the whole tail — detection, serving requirements, the verbatim-forward
guarantee, client shape, and the policy/lane boundaries.
