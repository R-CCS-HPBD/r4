# Example Scripts

This directory contains ready-to-run example scripts demonstrating the `r4` SDK.

## Quick Start

```bash
# Install r4
pip install -e ./r4

# Set up credentials
r4 auth login --gateway-url http://gateway:8080 --api-key YOUR_KEY

# Run an example
python docs/r4/examples/simple_chat.py
```

## Examples

| File | Description |
|------|-------------|
| [simple_chat.py](simple_chat.py) | Send a one-shot message and print the response. |
| [streaming_chat.py](streaming_chat.py) | Stream tokens as they arrive for a responsive UX. |
| [multi_turn_conversation.py](multi_turn_conversation.py) | Build a conversation with history across multiple turns. |
| [rag_workflow.py](rag_workflow.py) | Upload a document, build a RAG index, and query it. |
| [mcp_tools.py](mcp_tools.py) | Discover MCP servers and invoke tools programmatically. |
| [batch_processing.py](batch_processing.py) | Process a list of items through the LLM efficiently. |
| [error_handling.py](error_handling.py) | Robust error handling patterns (retries, timeouts, fallback). |

## Running on HPC

```bash
# Run from an interactive allocation or your site's batch script
python docs/r4/examples/simple_chat.py
```

For Slurm-based runs, wrap the example invocation in your existing job script or
site-specific submission helper instead of relying on a repository-local helper
that is not shipped in this directory.
