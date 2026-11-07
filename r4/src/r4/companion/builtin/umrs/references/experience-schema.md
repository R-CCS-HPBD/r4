# UMRS experience.json — complete reference

experience.json defines a per-user UMRS **experience**: one `xp_name` with one
or more **modalities**. After `umrs experience add` + `umrs reload`, UMRS
provisions per user: MinIO buckets per modality (+ optional output bucket), a
Postgres role + database, and pgvector embedding tables. `umrs data` uploads
to a bucket alias then trigger background ingestion with the declared model.

This is a **client-side config fragment** merged into
`~/.config/umrs/users/<user>.json` — NOT a workflow bundle (workflow.json) and
NOT an r4/gateway tool operation. All experience ops run via the `umrs` CLI.

## Contents

- experience.json (annotated schema)
- Field reference
- Example: text embedding experience
- Example: custom workflow experience
- model_type URL schemes
- Operations (add / validate / reload / list / remove / recover)

## experience.json (annotated schema)

The file contains ONE experience object, or an array of objects:

```json
{
  "xp_name": "Text Embedding",
  "sql_username": "demo_text",
  "modalities": [
    {
      "modality_name": "text",
      "slurm": {
        "partition": "gpu",
        "reservation": "my_reservation",
        "nodelist": "gpu-node-01"
      },
      "s3": {
        "bucket_alias": "demo-texts",
        "need_output_bucket": false,
        "data": { "type": "inline", "mime_type": "text/plain" }
      },
      "model": {
        "model_name": "intfloat/e5-base-v2",
        "vector_size": "768",
        "model_type": "rivault://openai://http://localhost:{LOCAL_PORT}/v1",
        "return_type": null,
        "spacy_model": "en_core_web_sm"
      },
      "sql": {
        "store_embeddings": "yes",
        "table_name": "text",
        "meta_data": [{ "name": "keywords", "type": "TEXT" }]
      },
      "auto_compute": true,
      "keep_endpoint": true
    }
  ]
}
```

## Field reference

| Field | Required | Meaning |
| --- | --- | --- |
| xp_name | yes | Experience id; bucket path prefix is derived from it |
| sql_username | yes | Postgres role created on reload (one per experience) |
| modalities[] | yes | At least one entry |
| modality_name | yes | e.g. `text`, `text file`, `image`, `movie`, `segmentation` |
| slurm | no | Optional per-modality scheduler overrides (partition / reservation / nodelist). Values are deployment-specific — never invent reservation names; take them from the site config or the user |
| s3.bucket_alias | yes | Alias users upload to: `umrs data dir-upload ./dir <alias>/<run-id>` |
| s3.need_output_bucket | no (default false) | `true` = also provision an output bucket. **Hard invariant: requires model.return_type non-null**, else the server assert-fails reload |
| s3.data.type | yes | `inline` (objects are the payload) or `file` |
| s3.data.mime_type | yes | e.g. `text/plain`, `image/png`, `video/mp4` |
| model.model_name | yes | Registry-model or HF id |
| model.vector_size | yes | String. Must match embedding output dim (e5-base-v2 → `"768"`). `"0"` for non-vector / custom workflow modalities |
| model.model_type | yes | URL scheme — see below |
| model.return_type | yes* | `null` when store_embeddings only; `file` / `embedding` / `raw` when outputs are produced. *MUST be non-null when need_output_bucket is true |
| model.spacy_model | no | spaCy model for text tokenization (text modalities) |
| sql.store_embeddings | yes | `yes` or `no` |
| sql.table_name | yes | Table created/validated on reload |
| sql.meta_data | yes | Extra columns: `[{"name": str, "type": "TEXT"|...}]` (may be `[]`) |
| auto_compute | no | Compute automatically on upload |
| keep_endpoint | no | Keep the mini-RiVault endpoint warm after ingest (skip auto-release) |

## Example: text embedding experience

Full copyable shape (deployment slurm block left out — add only if needed):

```json
{
  "xp_name": "Text Embedding",
  "sql_username": "demo_text",
  "modalities": [
    {
      "modality_name": "text",
      "s3": {
        "bucket_alias": "demo-texts",
        "data": { "type": "inline", "mime_type": "text/plain" }
      },
      "model": {
        "model_name": "intfloat/e5-base-v2",
        "vector_size": "768",
        "model_type": "rivault://openai://http://localhost:{LOCAL_PORT}/v1",
        "return_type": null,
        "spacy_model": "en_core_web_sm"
      },
      "sql": {
        "store_embeddings": "yes",
        "table_name": "text",
        "meta_data": [{ "name": "keywords", "type": "TEXT" }]
      },
      "auto_compute": true,
      "keep_endpoint": true
    }
  ]
}
```

## Example: custom workflow (pipeline step) experience

```json
{
  "xp_name": "Custom Workflow",
  "sql_username": "demo_custom",
  "modalities": [
    {
      "modality_name": "text",
      "s3": {
        "bucket_alias": "demo-custom-text",
        "need_output_bucket": true,
        "data": { "type": "file", "mime_type": "text/plain" }
      },
      "model": {
        "model_name": "custom-workflow",
        "vector_size": "0",
        "model_type": "pipeline://custom_step:CustomWorkflowStep",
        "return_type": "file"
      },
      "sql": { "store_embeddings": "no", "table_name": "custom_wf", "meta_data": [] }
    }
  ]
}
```

## model_type URL schemes

| Scheme | Use |
| --- | --- |
| `rivault://openai://http://localhost:{LOCAL_PORT}/v1` | Inference via mini-RiVault; UMRS substitutes `{LOCAL_PORT}` with the job's LiteLLM port |
| `pipeline://<module_stem>:<ClassName>` | Custom pipeline step class (e.g. `pipeline://custom_step:CustomWorkflowStep`); module must be importable in the UMRS runtime |
| plain names (e.g. `TransformerEncoder`) | Legacy form still accepted |

## Operations

```bash
# Authoring feedback loop
umrs experience validate experience.json   # schema + model availability
umrs experience add experience.json --update
umrs reload                                # provision buckets + Postgres + tables
umrs bucket list                           # confirm the alias exists

# Smoke ingest (mirrors workflow authoring smoke-run)
umrs data dir-upload ./sample-data demo-texts/smoke
umrs status                                # PROCESS_OBJECTS row appears

# Introspection / removal / recovery
umrs experience list
umrs experience remove "Text Embedding"    # stanza only; data RETAINED
umrs reload
umrs experience recover "Text Embedding" \
  --username <user> --s3cfg ~/.config/umrs/s3cfg_<user> \
  --output-path recovered-experience.json  # or --merge --update
```

Recovery notes: MinIO bucket names prove username/xp/bucket-alias/output-bucket;
Postgres inspection infers db/table/vector size. Model identity CANNOT be
proven from storage — pass `--model-name`, `--model-type`, `--vector-size`,
`--return-type` overrides. `--no-postgres` / `--no-object-count` speed it up.
