# ADR-008: SiteConfig Environment Overlay No-Overwrite Rule

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** SI-05 (SiteConfig no-overwrite)

## Context

UMRS uses `SiteConfig` (YAML) to configure deployment parameters: model paths,
network ports, resource limits, backend selection. `SiteConfig.inject_env()`
pushes configuration values into environment variables so that downstream
components (vLLM, LiteLLM, Singularity) can read them.

A contributor might modify `inject_env()` to overwrite existing environment
variables with YAML values, assuming the YAML config is authoritative.

## Decision

`SiteConfig.inject_env()` **never overwrites** existing environment variables.

```python
def inject_env(self):
    for key, value in self._env_pairs():
        if key not in os.environ:      # ← never overwrite
            os.environ[key] = str(value)
```

### Why

1. **`.env` files are the deployment override layer.** On RCCS-cloud, secrets
   (LITELLM_MASTER_KEY, PostgreSQL passwords) come from `.env`, not `site.yaml`.
   `site.yaml` is committed to Git; `.env` is not.

2. **Runtime overrides must win.** When an operator sets `VLLM_MAX_MODEL_LEN=8192`
   in the environment, it must take precedence over `site.yaml`'s default of 4096.

3. **Layering order:** Environment (shell/`.env`) → SiteConfig (YAML) → code defaults.

## Consequences

- `site.yaml` provides defaults; environment provides overrides
- Secrets live in `.env` files, never in `site.yaml`
- `site.yaml` can be committed to Git safely (no secrets)
- Operators can override any SiteConfig value by setting the environment variable
  before starting UMRS
- The deploy scripts (`validate_harness.sh`, `start.sh`) source `.env` before
  starting UMRS

## Alternatives Considered

- **YAML wins over environment**: Simpler mental model but prevents runtime
  overrides and forces secrets into YAML (which is committed to Git).
- **Explicit precedence flag**: Adds complexity. The "env wins" rule is universal
  in Unix convention and matches Twelve-Factor App methodology.
