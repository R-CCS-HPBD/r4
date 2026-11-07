# R4 Custom Distributions

Custom distributions allow organizations to ship a preconfigured r4
with pinned models, MCP servers, default settings, and optional branding.

## How It Works

A custom distribution is a standard Python wheel built from a thin
wrapper package that depends on `r4` and ships a `defaults.toml` file.

### 1. Create the wrapper package

```
my-org-r4/
├── pyproject.toml
├── src/
│   └── my_org_r4/
│       ├── __init__.py
│       └── defaults.toml
```

### 2. `pyproject.toml`

```toml
[project]
name = "my-org-r4"
version = "1.0.0"
dependencies = ["r4>=0.1.0"]

[project.scripts]
my-r4 = "r4.cli:main"

[project.entry-points."r4.defaults"]
my_org = "my_org_r4:defaults.toml"
```

### 3. `defaults.toml`

```toml
[gateway]
url = "https://llm.my-org.example.com"

[model]
default = "rivault-gpt-oss-static"
allowed = ["rivault-gpt-oss-static", "Qwen/Qwen2.5-7B-Instruct"]

[mcp]
# Auto-register these MCP servers
servers = [
  { name = "my-tools", command = "python -m my_org_tools" }
]

[branding]
app_name = "MyOrg AI"
welcome_message = "Welcome to MyOrg AI Assistant"
```

### 4. Build and distribute

```bash
cd my-org-r4
pip install build
python -m build
# → dist/my_org_r4-1.0.0-py3-none-any.whl
```

Users install with:
```bash
pip install my-org-r4
my-r4 chat
```

## Layered Config Precedence

Custom distribution defaults sit at the bottom of the config stack:

1. **Environment variables** (highest priority)
2. **`.r4/local.toml`** (repo-local, gitignored)
3. **`.r4/config.toml`** (repo-shared)
4. **`~/.config/r4/config.json`** (user profile)
5. **Distribution `defaults.toml`** (from entry-point)
6. **Built-in defaults** (lowest priority)

## What Can Be Customized

| Setting | Example |
|---------|---------|
| Gateway URL | `gateway.url` |
| Default model | `model.default` |
| Allowed models | `model.allowed` |
| MCP servers | `mcp.servers` |
| Branding | `branding.app_name`, `branding.welcome_message` |
| Provider list | `providers.enabled` |
| Skill bundles | `skills.auto_load` |
| Security policies | `security.tool_policies` |

## Loading Logic

r4 discovers distribution defaults at startup via the
`r4.defaults` entry-point group:

```python
from importlib.metadata import entry_points

eps = entry_points(group="r4.defaults")
for ep in eps:
    defaults_path = importlib.resources.files(ep.load().__package__) / ep.name
    # merge into config stack
```

## Notes

- The wrapper package re-exports `r4.cli:main` as its own script entry
- No code changes to r4 are needed — it is config-only
- Multiple distributions can coexist (last-loaded wins)
- UMRS is not affected — it has no concept of distributions
