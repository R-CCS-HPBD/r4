# Appendix A — Troubleshooting

## Common Issues

### Authentication

| Problem | Cause | Solution |
|---------|-------|----------|
| `Authentication failed` | Invalid or expired token | Regenerate: `umrs-server admin token generate-jwt --user USERNAME` |
| `bash: GATEWAY_URL: No such file or directory` | A pasted `<GATEWAY_URL>` placeholder (bash reads `<X>` as an input redirect) | Tokens minted on the front after the 2026-10-03 converge embed the gateway URL: `r4 auth login --api-key TOKEN`; older tokens: `--gateway-url https://HOST/SITE` with the real URL |
| Login "succeeded" but every call fails to reach `https://HOST/SITE` (2026-10-03) | The example login line (`--gateway-url https://HOST/SITE`, printed for a token without a gateway URL) was pasted unchanged; it is shell-safe, so r4 saved the example as the profile's URL (the post-login identity check fails silently) | Run the login again with the site's real URL, e.g. `r4 auth login --gateway-url https://data0.ai.r-ccs.riken.jp/rccs --api-key TOKEN` (an accepted tradeoff: the old `<GATEWAY_URL>` failed in bash at once) |
| `Connection refused` | Wrong Gateway URL | Check `r4 config show` |
| OIDC login hangs | Browser not opening | Set `$BROWSER` or copy URL manually |
| `403 Forbidden` | Missing project access | Admin: `umrs-server admin user add-to-project` |

### Connectivity

| Problem | Cause | Solution |
|---------|-------|----------|
| `r4 health` fails | Gateway unreachable | Check URL and network/firewall |
| `r4 health` OK but commands fail | Auth issue | Run `r4 auth status` and `r4 auth refresh` |
| Timeout errors | Endpoint not ready | `r4 endpoint status <id>` to check |

### Endpoints

| Problem | Cause | Solution |
|---------|-------|----------|
| `No endpoints available` | None provisioned | `r4 endpoint up --model <name> --wait` |
| Endpoint stuck in `provisioning` | Slurm queue delay or failure | `r4 endpoint logs <id>` |
| `No Slurm account configured` | Missing account binding | Admin: `umrs-server admin user set-slurm-account` |
| GPU allocation failure | Resources unavailable | Check Slurm queue: `squeue -u $USER` |

### RAG

| Problem | Cause | Solution |
|---------|-------|----------|
| Upload fails | File too large or unsupported format | Check file size and type |
| No search results | Documents not indexed | Wait for indexing job to complete: `r4 job list` |
| Low relevance scores | Wrong retriever config | Try hybrid search: `r4 rag retriever create --type hybrid` |

### CLI

| Problem | Cause | Solution |
|---------|-------|----------|
| `r4` not found | Not in PATH | `export PATH="$HOME/.local/bin:$PATH"` |
| `ModuleNotFoundError` | Missing extras | `pip install "r4[chat]"` or `"r4[companion]"` |
| Old command names | Using deprecated commands | See [CLI Reference §Renamed](13-cli-reference.md#renamed-commands) |

## Diagnostic Commands

```bash
# Full diagnostics
r4 doctor

# Check config
r4 config show
r4 config context list

# Check auth
r4 auth status

# Check health
r4 health
r4 health account

# Check endpoints
r4 endpoint list
r4 endpoint status <id>
r4 endpoint logs <id> --tail 50

# Check jobs
r4 job list --status failed
r4 job describe <id>

# UMRS direct check (operators)
umrs ping --url <umrs-url>
umrs status all --url <umrs-url>
```

## Log Locations

| Component | Log Location |
|-----------|-------------|
| Gateway (NGINX) | Container stdout / `/var/log/nginx/` |
| Gateway (Sidecar) | Container stdout |
| UMRS | Server stdout / configured log path |
| Mini-RiVault | `r4 endpoint logs <id>` |
| vLLM | Inside Mini-RiVault container |
| LiteLLM | Inside Mini-RiVault container |
