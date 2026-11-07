# 4. Chat & Companion

R4 provides multiple interfaces for conversational AI — from simple one-shot commands to rich interactive TUIs.

## One-Shot Chat

```bash
# Simple question
r4 chat "What is quantum computing?"

# With streaming output
r4 chat "Count from 1 to 10." --stream

# Specify model
r4 chat "Explain vLLM" --model Qwen/Qwen2.5-7B-Instruct

# With system prompt
r4 chat "Summarize this code" --system "You are a code reviewer"

# Control generation
r4 chat "Write a haiku" --temperature 0.9 --max-tokens 100
```

## Interactive Chat

```bash
# Start interactive session (launches JS companion TUI)
r4 chat -i

# Or equivalently
r4 chat --interactive
```

In interactive mode, type your messages and press Enter. Type `exit` or `Ctrl-D` to quit.

## Companion TUI

The companion is a JS (Ink) interactive assistant with tool use and session management.

### Launch

```bash
r4
r4 companion
r4 chat --companion
r4 chat -i              # interactive mode (same JS TUI)
```

**Requirements:** Node.js ≥ 18; JS bundle at `r4/companion-tui-js/dist/`.

### One-Shot Mode

```bash
# Run a single prompt and exit
r4 -p "Summarize this project"

# With auto-approved tool calls
r4 -p "Find all Python files" --yolo
```

### Interaction Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `chat` | Conversational Q&A | General questions, exploration |
| `plan` | Planning and reasoning | Complex multi-step tasks |
| `autopilot` | Auto-execute tool calls | Automated workflows |

```bash
r4 --mode chat
r4 --mode plan
r4 --mode autopilot
```

Switch modes during a session with the `/mode` slash command.

### Slash Commands

Inside the companion TUI, type `/` to see available commands:

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/h`, `/?` | Show all slash commands |
| `/dashboard` | `/status`, `/refresh` | Backend overview: endpoints, jobs, MCP, models |
| `/endpoint list` | | List active endpoints |
| `/endpoint up` | `/up` | Provision or reuse an endpoint |
| `/endpoint down` | `/down` | Stop an endpoint |
| `/mcp list` | | List registered MCP servers |
| `/mcp tools` | | List all available MCP tools |
| `/rag query` | | Query indexed documents |
| `/rag upload` | | Upload a document for RAG |
| `/job list` | | List durable jobs |
| `/workflow list` | | List registered workflows |
| `/mode <mode>` | | Switch interaction mode |
| `/models` | `/model` | Show or switch the active model |
| `/skills` | `/skill` | Manage skill bundles |
| `/usage` | | Token usage and cost for this session |
| `/sessions` | `/resume` | Session management |
| `/health` | | Check connectivity |
| `/quit` | `/exit`, `/q` | Save session and exit |

Dozens more live in the shared registry — research (`/research`, `/project`,
`/knowledge`), governance (`/governance`, `/audit`, `/traces`), agents
(`/agent`, `/team`, `/search`), and utility commands.
See [15. TUI Reference](15-tui-reference.md) for the full table.

### Root-Level Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-p / --prompt TEXT` | — | One-shot prompt and exit |
| `-m / --model NAME` | profile default | Override model |
| `--mode chat\|plan\|autopilot` | `chat` | Interaction mode |
| `--allow-all-tools / --yolo` | off | Auto-approve tool calls |
| `--fs-root [PATH]` | — | Enable local filesystem tools |
| `--secure` | off | Content scan and per-file consent |

### Filesystem Tools

Enable the companion to read and navigate local files:

```bash
r4 --fs-root .                # Current directory
r4 --fs-root /path/to/data    # Specific directory
```

### Secure Mode

Pre-send content scanning and per-file attachment consent:

```bash
r4 --secure
```

## Session Management

Sessions persist conversation history locally.

```bash
# List sessions
r4 session list

# Resume a session
r4 chat --resume <session-id>

# Show session details
r4 session show <session-id>

# Delete session
r4 session delete <session-id>
```

### Companion Sessions

```bash
r4 companion sessions            # List companion sessions
r4 companion --resume <session-id>   # Resume a companion session
r4 companion export <session-id>     # Export a session transcript
r4 session show <session-id>         # Show companion session details
```

## Skills

Skills are bundled capabilities that customize companion behavior:

```bash
r4 skill list                 # List available skills
r4 skill search "chemistry"   # Search the registry
r4 skill install md-analyzer  # Install a skill
r4 skill info md-analyzer     # Show skill details
r4 skill update               # Update installed skills from sources
r4 skill scan ./my-skill      # Security-scan a skill directory
r4 skill github org/repo      # Install from any GitHub repository
```

Use skills in chat:

```bash
r4 chat "Analyze this molecule" --skill chemistry
```

## Python SDK

```python
from r4 import Client

client = Client()

# Simple chat
response = client.chat("Hello!")
print(response.content)

# Streaming
for chunk in client.chat("Tell a story", stream=True):
    print(chunk, end="", flush=True)

# With options
response = client.chat(
    "Explain vLLM",
    model="Qwen/Qwen2.5-7B-Instruct",
    temperature=0.7,
    max_tokens=500,
    system="You are a systems expert",
)

# Multi-turn with chat_completion
from r4 import Client

client = Client()
response = client.chat_completion([
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is R4?"},
])
```

## Next Steps

- [Chapter 5 — Projects & Models](05-projects-models.md)
- [Chapter 7 — RAG](07-rag.md)
- [Chapter 15 — TUI Reference](15-tui-reference.md) for detailed TUI documentation
