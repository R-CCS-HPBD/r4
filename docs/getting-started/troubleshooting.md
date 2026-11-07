# R4 Platform — Troubleshooting Guide

> **Audience:** End users, operators, administrators
> **Status:** Current as of 2026-06-07

This guide covers common problems, their causes, and solutions. For operational
error codes and diagnostics, see Operations: Errors and
Operations: Diagnostics.

---

## Table of Contents

1. [Client-Side Issues](#client-side-issues)
2. [Authentication Issues](#authentication-issues)
3. [Endpoint Issues](#endpoint-issues)
4. [Chat/RAG Issues](#chatrag-issues)
5. [Workflow/Job Issues](#workflowjob-issues)
6. [Deployment Issues](#deployment-issues)
7. [Performance Issues](#performance-issues)
8. [Getting Help](#getting-help)

---

## Client-Side Issues

### "Connection refused" or "Cannot reach gateway"

**Symptoms:** Any `r4` command fails with connection error.

**Causes:**
- Gateway is not running
- Wrong gateway URL configured
- Network/VPN not connected
- Firewall blocking the port

**Solutions:**
```bash
# 1. Check configured gateway URL
r4 config show | grep gateway_url

# 2. Test connectivity
curl -v https://<gateway-host>:8080/health

# 3. Try with explicit gateway URL
r4 --gateway-url https://<gateway-host>:8080 chat "test"

# 4. Check if VPN is required and connected
```

### "SSL certificate verify failed"

**Symptoms:** SSL errors when connecting to gateway.

**Solutions:**
```bash
# For self-signed certs in development:
r4 config set ssl_verify false

# For production: install the CA certificate
# Or set the CA bundle path:
export REQUESTS_CA_BUNDLE=/path/to/ca-bundle.crt
```

### "Command not found: r4"

**Symptoms:** `r4: command not found`

**Solutions:**
```bash
# Check installation
pip show r4

# Reinstall
pip install --upgrade r4

# Check PATH
which r4
echo $PATH
```

---

## Authentication Issues

### "401 Unauthorized"

**Symptoms:** Every request returns 401.

**Causes:**
- Token expired
- Invalid API key
- Wrong gateway URL (authenticated against different gateway)
- Gateway rejecting the token

**Solutions:**
```bash
# 1. Re-authenticate
r4 auth login --gateway-url <correct-url>

# 2. Check who you're authenticated as
r4 auth whoami

# 3. List profiles and switch
r4 auth profiles
r4 auth switch <profile-name>

# 4. Clear and re-authenticate
r4 auth logout
r4 auth login --gateway-url <url>
```

### "403 Forbidden"

**Symptoms:** 403 on specific operations.

**Causes:**
- Insufficient permissions (project membership, model access)
- Rate limit exceeded
- Operation requires admin role

**Solutions:**
```bash
# Check project membership
r4 project member list <project>

# Check available models (scope-limited)
r4 model list

# Contact admin for access
```

### OIDC login opens browser but fails

**Symptoms:** Browser opens, login succeeds, but CLI doesn't receive token.

**Solutions:**
```bash
# Use API key instead
r4 auth login --api-key sk-xxxx

# Or check callback URL reachability
# OIDC callback must be reachable from the browser
```

---

## Endpoint Issues

### Endpoint stuck in "provisioning"

**Symptoms:** `r4 endpoint up` returns a job ID, but endpoint never becomes ready.

**Causes:**
- Slurm queue is full — job is waiting for resources
- No GPU nodes available
- Slurm partition misconfigured

**Solutions:**
```bash
# 1. Check Slurm queue (admin)
squeue -p gpu

# 2. Check specific endpoint status
r4 endpoint show <endpoint-id>

# 3. Check UMRS job status
r4 job status <job-id>

# 4. Cancel and retry with different model
r4 endpoint down <endpoint-id>
r4 endpoint up --model <smaller-model>

# 5. Check Slurm logs (admin)
sacct -j <slurm-job-id>
```

### Endpoint "starting" but never "ready"

**Symptoms:** Endpoint shows `starting` status indefinitely.

**Causes:**
- Model loading is slow (large model on cold start)
- vLLM failed to start
- LiteLLM health check failing
- Container runtime error

**Solutions:**
```bash
# Wait — large models can take 5-15 minutes to load
# Check endpoint details for errors
r4 endpoint show <endpoint-id>

# Admin: check Slurm job output
scontrol show job <slurm-job-id>
# Check container logs on compute node

# Cancel and retry
r4 endpoint down <endpoint-id>
```

### "No endpoints available"

**Symptoms:** Can't chat or run inference.

**Solutions:**
```bash
# Check active endpoints
r4 endpoint list

# Provision an endpoint
r4 endpoint up --model <model-name>

# If endpoints exist but are "draining":
# Wait for drain to complete or provision new one
r4 endpoint up --model <model-name>
```

---

## Chat/RAG Issues

### Chat returns empty or timeout

**Symptoms:** Chat request hangs or returns empty.

**Solutions:**
```bash
# 1. Check endpoint is ready
r4 endpoint list

# 2. Try with explicit model
r4 chat "test" --model <model-name>

# 3. Increase timeout
r4 config set request_timeout 120

# 4. Check streaming vs non-streaming
r4 chat "test" --no-stream
```

### RAG returns no results

**Symptoms:** `r4 rag query` returns empty or irrelevant results.

**Solutions:**
```bash
# 1. Verify documents were uploaded
r4 rag index list --project <project>

# 2. Re-upload documents
r4 rag upload ./docs/ --project <project>

# 3. Try broader query
r4 rag query "broader search terms" --project <project>

# 4. Check project is correct
r4 project current
```

### "Model not found"

**Symptoms:** Chat or endpoint provision fails with model not found.

**Solutions:**
```bash
# List available models
r4 model list

# Check model name spelling
# Model names are static — use exact name from the list

# If model is missing (admin):
r4 model register <model-name> --path /shared/models/<model-name>
```

---

## Workflow/Job Issues

### Job fails immediately

**Symptoms:** Job transitions to `failed` shortly after submission.

**Solutions:**
```bash
# Check job details for error
r4 job show <job-id>

# Check job logs
r4 job logs <job-id>

# Common causes:
# - Invalid workflow YAML syntax
# - Missing required parameters
# - Model not available
# - File not found in S3
```

### Job stuck in "running"

**Symptoms:** Job shows `running` for much longer than expected.

**Solutions:**
```bash
# Check for long-running steps
r4 job show <job-id> --verbose

# Cancel and retry
r4 job cancel <job-id>
r4 workflow run <workflow> --params '{"input": "data.csv"}'

# Admin: check UMRS logs for reconciler issues
```

---

## Deployment Issues

### Gateway won't start

**Symptoms:** Gateway service fails to start.

**Solutions:**
```bash
# Check environment
echo $GATEWAY_ENVIRONMENT

# Check JWT secret is configured
echo $JWT_SECRET | wc -c  # Should be > 32

# Check NGINX config
nginx -t

# Check auth sidecar logs
journalctl -u r4-gateway-sidecar -n 50

# Check port availability
lsof -i :8080
```

### UMRS can't connect to PostgreSQL

**Symptoms:** UMRS startup fails with database connection error.

**Solutions:**
```bash
# Verify PostgreSQL is running
pg_isready -h <host> -p 5432

# Check connection from UMRS host
psql -h <host> -U umrs -d umrs -c "SELECT 1"

# Check pgvector extension
psql -h <host> -U umrs -d umrs -c "SELECT * FROM pg_extension WHERE extname='vector'"

# Check firewall rules between UMRS and PostgreSQL
```

### MinIO/S3 unreachable

**Symptoms:** File uploads fail, RAG indexing fails.

**Solutions:**
```bash
# Check MinIO health
curl http://<s3-host>:9000/status            # SeaweedFS; MinIO profiles: /minio/health/live

# Test with mc client
mc alias set s3 http://<s3-host>:9000 <access-key> <secret-key>
mc ls minio/

# Check bucket exists
mc ls minio/rivault/

# Check S3 credentials in .env match
```

---

## Performance Issues

### Chat latency is high

**Causes:**
- Model is large and cold (first request after endpoint provision)
- HPC network latency
- Proxy overhead

**Mitigations:**
```bash
# Pre-warm endpoint with a simple request
r4 chat "ping" --model <model>

# Use smaller model for latency-sensitive tasks
r4 endpoint up --model <smaller-model>

# Check endpoint node location vs client location
```

### RAG search is slow

**Causes:**
- Large document collection
- Vector index not optimized

**Mitigations:**
```bash
# Use namespace filtering
r4 rag query "..." --project <project> --namespace <dataset>

# Rebuild index with optimized settings (admin)
```

### Endpoint provisioning is slow

**Causes:**
- Slurm queue contention
- Large model loading time
- Cold HPC node boot

**Mitigations:**
```bash
# Use warm pool (system tier, admin-configured)
# Provision during off-peak hours
# Use smaller model for quick iterations
```

---

## Getting Help

### Diagnostic Commands

```bash
# Full config dump
r4 config show

# Version info
r4 --version

# Check all active resources
r4 endpoint list
r4 project list
r4 model list
r4 job list --limit 10

# Gateway health
curl https://<gateway>:8080/health

# Verbose mode for any command
r4 --verbose chat "test"
```

### Log Locations

| Component | Log Path |
|-----------|----------|
| Gateway NGINX | `/var/log/r4-gateway/access.log`, `error.log` |
| Auth Sidecar | `journalctl -u r4-gateway-sidecar` |
| UMRS | `journalctl -u umrs-server` |
| Mini-RiVault | Via `r4 endpoint show <id> --logs` |
| Slurm jobs | `/var/log/slurm/` or `sacct` |

### Escalation

1. Check this guide and Operations: Errors
2. Check Operator Runbook (admin)
3. Run diagnostic commands and collect logs
4. Open a GitHub issue with: r4 version, command used, full error output, logs

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [User Guide](user-guide.md) | Usage instructions |
| Admin Guide | Deployment and administration |
| Operations: Errors | Error code reference |
| Operations: Diagnostics | Diagnostic procedures |
| Operator Runbook | Failure detection and remediation |
| [User Manual: Appendix A](../user-manual/appendix-a-troubleshooting.md) | Additional troubleshooting |
