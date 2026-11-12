# Service status

Checked **2026-11-12 17:48 JST**. The check runs every 30 minutes and rewrites this page on a change or every 6 hours.

| Site | State | Model endpoints ready | Live service roles | Service models |
|---|---|---|---|---|
| rccs | **up** | 7 / 7 | `svc-decision`, `svc-embed`, `svc-fast`, `svc-ocr`, `svc-rerank`, `svc-vision` | 6/6 up |
| ai4s | **up** | 0 / 0 | — | none configured |
| bdrt | **up** | 2 / 2 | `svc-chat`, `svc-fast` | idle |
| octopus | **up** | 0 / 0 | — | none configured |

**How to read it.** *up*: the site's gateway answers and its server reports healthy. *degraded*: some model endpoints are unhealthy. *down*: the gateway or the server does not answer. *unknown*: the check could not reach the site.
