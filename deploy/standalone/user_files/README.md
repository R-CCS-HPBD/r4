# Stand-alone user document

`conf_umrs.json` requires at least one user (`users: minItems 1`), and boot
provisions each one: a Postgres role per experience, an S3 bucket per modality,
and a per-user S3 identity whose credentials are written to
`s3_configuration_file`.

- **`s3_configuration_file` is `/var/lib/umrs/s3cfg_scientist`, on the durable
  `umrs_state` volume, and must stay there.** Boot writes the user's S3 secret
  to it. Pointing it at the server's admin `/etc/umrs/s3cfg` would overwrite the
  admin credential; leaving it to the code's fallback (`~/.config/umrs/…`) puts
  it on the container's ephemeral filesystem, so every restart would mint a new
  secret and invalidate the old one. It never leaves the server — this is not
  the client-delivered s3cfg lane of gap D-16.
- **The embedder is the image's bundled `/opt/umrs/models/e5-base-v2`**
  (768-d) — the same e5-base-v2 embedding space the cluster sites use, so
  `.kb` bundles move between stand-alone and remote with their vectors intact
  (2026-10-15: the config had M0's hand-staged `/models/e5-base-v2`, which
  nothing stages now — the rag service died on it live). `./standalone.sh
  embedder pull` stages a *different* embedder into `/models/<name>` for
  `UMRS_KNOWLEDGE_EMBED_MODEL`. Changing it after vectors exist is not
  supported yet (ADR-090 D-4); on a dev stack, `./standalone.sh reset --yes`
  starts over.
- `model` requires `return_type` (`shared/json_config.py`); the older example
  in `umrs/docker/user_files/` omits it and would fail validation.
