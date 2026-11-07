# 5. Projects & Models

## Projects

Projects are isolated workspaces for teams or research groups. All platform resources (data, models, endpoints, workflows) are scoped to a project.

### Project Commands

```bash
# List all projects
r4 project list

# Create a new project (on this machine)
r4 project create mylab-r4 --name "My Lab Research"

# Register it on the platform (server), once
r4 project register mylab-r4 --name "My Lab Research"

# Switch active project
r4 project switch mylab-r4

# Show current project
r4 project current

# Delete a project
r4 project delete mylab-r4
```

After `r4 auth login`, `r4 project list` shows:
- the projects you own;
- the projects an admin added you to (`umrs admin user add-to-project`);
- the projects where you hold a circle.

`r4 project switch` makes the next requests act in that project. You can
act in an owned project or one you were added to. A circle alone lets you
see the project and what is shared with your circle, not act in it. The
gateway checks each request against your current memberships, so being added to a
project needs no new token. A project you are not a member of answers
`403 project_not_member`.

### Registering a project on the platform

*Added 2026-11-03.*

`r4 project create` (and `/project create` in the console) makes a project
on **your machine only**. The platform does not know it yet, so loops, jobs
and knowledge in it answer `403 project_not_member`. Register it once:

```bash
r4 project register mylab-r4 --name "My Lab Research"
```

In the console: `/project register mylab-r4`. Any signed-in user may
register a project; you become its owner, and repeating the command is
safe. If the id already belongs to someone else, the command says so; pick
another id. Registering does not switch your current project. To work in
someone else's project, ask its owner (or an administrator:
`umrs admin user add-to-project <user> <project>`) to add you instead.

### Project Scoping

Most commands accept `--project` to override the active project:

```bash
r4 rag upload paper.pdf --project team-alpha
r4 model list --project team-alpha
r4 job list --project team-alpha
```

### Data class: what may leave your machine

*Added 2026-10-16.*

A project that the deployment has recorded has a **data class**. The
class decides which model providers may receive the project's data when you
chat, run research loops or start jobs in it.

A project you made only with `r4 project create` exists on your machine
alone. The deployment has no record of it, so it has no data class:
nothing is refused for it, and `r4 project data-class get` reports that the
project is not found. Before you put sensitive data in such a project,
register it on the platform (`r4 project register <project>`, see
"Registering a project on the platform" above; *corrected 2026-11-03*: this
used to say to ask the administrators, but any signed-in user may). On a
stand-alone stack, `./r4-local register <user> <project>` creates it and
makes it the project the `standalone` source uses.

| Class | Who may run the models for the project |
|---|---|
| `sensitive` | the platform's own models; **your institution's provider** (the deployment keeps the list of them; at RIKEN this is Rikyu); models running **on your own machine** (for example ollama on `localhost`) |
| `non_sensitive` | all of the above, plus **public and commercial providers** (OpenAI, Gemini, Anthropic and the like), with your own key |

- A shared deployment, such as a cluster site, records new projects as
  `sensitive`.
- A stand-alone stack on your own machine records them as `non_sensitive`:
  the data is already on your machine, and you are its only user
  ([Chapter 29](29-standalone-deployment.md)). Narrow a project there if its
  data must not go to a commercial provider.
- Only the people who run the deployment decide which services count as
  "your institution's provider". You cannot mark a source as institutional
  yourself: a source you add with any other address counts as public.
- "On your own machine" is decided from the address alone: `localhost`,
  a `127.x.x.x` address or `::1`. r4 cannot see what a program at that
  address does with the data: a local relay or an `ssh -L` tunnel to a
  commercial provider also counts as on your machine. Do not point a
  `sensitive` project at one.
- A model address that includes a user name or password
  (`http://user:pass@localhost:4000`) always counts as public, even on your
  own machine. Give the key to the source as its API key instead
  ([Chapter 11](11-sources.md)).
- A source for a cloud service that chooses its own address, such as
  Amazon Bedrock, always counts as public, whatever address you registered
  with it.

#### When a provider is refused

In a `sensitive` project, a public or commercial provider is refused
**before anything is sent**:

- **On your machine.** r4 stops with an error that begins
  `Public inference route '<source>' refused: the active project is data_class=sensitive`.
  The message names the source and the command that widens the class.
- **In a remote session.** The session does not start when it would carry
  a public provider's key. The error is `source_class_refused`, and it names
  the sources that were refused.
- **Through the platform's model proxy.** The request is refused with
  `source_class_refused`.

What to do:

1. Use a model that is allowed for the project: one of the platform's models
   (`model@<platform-source>`, see [Chapter 11](11-sources.md)), your
   institution's provider, or a model on your own machine.
2. If the data may really go to that provider, ask the project's owner to
   widen the class (below).

#### Check or change the class

```bash
r4 project data-class get mylab-r4                  # the class, and what it allows
r4 project data-class set mylab-r4 sensitive        # narrow: takes effect at once
r4 project data-class set mylab-r4 non_sensitive \
  --confirm 'mylab-r4 non_sensitive'                # widen: typed confirmation
```

- Only the project's owner or an administrator can change the class.
- The deployment records every change of class: who made it, on which
  project, and from which class to which.
- **Narrowing** to `sensitive` needs no confirmation.
- **Widening** to `non_sensitive` lets the project's data go to public
  providers, so it needs a typed confirmation: the project name and the new
  class, exactly as shown. In a terminal, r4 asks you to type it. In a
  script, pass it with `--confirm`. Without it, or with any other text,
  nothing changes.
- Without a project name, both commands use your current project.
- A project on another deployment: put `R4_SOURCE=<source>` in front, for
  example `R4_SOURCE=standalone r4 project data-class get default`.

## Models

The model registry tracks LLM, embedding, and vision models with scoped access control.

### Model Scopes

| Scope | Owner | Access |
|-------|-------|--------|
| `system` | Admin | All users (read) |
| `project` | Project admin | Project members |
| `user` | User | Owner only |

### Listing Models

```bash
# List all visible models
r4 model list

# Filter by scope
r4 model list --scope system
r4 model list --scope project

# Filter by type
r4 model list --type llm
r4 model list --type embedding
r4 model list --type vision

# Filter by status
r4 model list --status available
r4 model list --status staging
```

### Model Information

```bash
r4 model info Qwen/Qwen2.5-7B-Instruct
```

### Staging Models from HuggingFace

Stage a model from HuggingFace Hub using controlled UMRS download jobs:

```bash
# Stage a model (registers and initiates download)
r4 model stage Qwen/Qwen2.5-7B-Instruct \
  --name my-qwen \
  --scope project

# Stage with default name
r4 model stage Qwen/Qwen2.5-7B-Instruct

# Check staging job status
r4 job list
```

> **Important:** Model downloads are the ONLY operations with public egress. Inference containers are always offline (`TRANSFORMERS_OFFLINE=1`).

### Registering Pre-Staged Models

Register a model already on the filesystem:

```bash
r4 model register my-local-model \
  --path /shared/models/my-model \
  --scope project \
  --type llm

# For vision models with BentoML backend
r4 model register sam3 \
  --path /shared/models/sam3 \
  --type vision \
  --backend bentoml \
  --service-kind bentoml_vision
```

### Publishing and Deleting

```bash
# Publish a staged model to a project (make available for project members)
r4 model publish my-qwen --project mylab-r4

# Delete a model
r4 model delete my-qwen
```

## Using Models

Once a model is available, use it with any command:

```bash
# Chat with a specific model
r4 chat "Hello" --model my-qwen

# Provision an endpoint for a model
r4 endpoint up --model my-qwen

# Set as default in your profile
r4 auth login --model my-qwen

# Set the active chat model (shortcut)
r4 model use my-qwen

# Bind a model to a named source for cross-source routing
r4 model route my-qwen --source my-hpc

# Auto-detect capabilities from model files (context len, tool support, ...)
r4 model detect my-qwen
```

## Python SDK

```python
from r4 import Client

client = Client()

# List models
models = client.list_models()
for model in models:
    print(f"{model.name} ({model.scope}, {model.status})")

# Chat with a specific model
response = client.chat("Hello", model="my-qwen")
```

## Next Steps

- [Chapter 6 — Endpoints](06-endpoints.md)
- [Chapter 7 — RAG](07-rag.md)
