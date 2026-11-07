# @r4/web — R4 Research Operations Web Console

Authenticated web shell for the R4 platform, served behind NGINX `/app/`.

## View honesty contract (FR-2)

(Home since 2026-10-19 — this definition moved here from the retired
`docs/development/web-ui-roadmap.md`, archived the same week. The code cites
it as "web-ui-roadmap FR-2" at `src/App.tsx` and `src/demo-mode.ts`.)

Every view renders from the shared Read API with the same honesty contract
as the TUI: each view is one of **empty / loading / stale / error /
degraded** at all times, never a mock row dressed as live data. A view
without live wiring is **hidden by default**; the demo projection exists
only as an explicit opt-in (`VITE_R4_WEB_DEMO=1`), and is labeled as such.


## Structure

```
packages/r4-web/
├── index.html            # Vite entry point
├── package.json
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── main.tsx           # React entry, AuthProvider wrapping
    ├── App.tsx            # Route definitions (placeholder pages)
    ├── index.css          # Global styles
    ├── auth/
    │   ├── AuthProvider.tsx  # JWT/cookie auth, context provider
    │   └── index.ts
    ├── components/
    │   ├── Layout.tsx        # Sidebar + Outlet layout
    │   ├── Shell.tsx         # Header shell component
    │   └── index.ts
    └── routes/
        └── index.ts
```

## Routes

| Path | Component | Description |
|------|-----------|-------------|
| `/` | Overview | Platform overview dashboard |
| `/report` | Report | Report generation |
| `/artifacts` | Artifacts | Artifact browser |
| `/graph` | Graph | Dependency/task graph |
| `/timeline` | Timeline | Event timeline |

## Auth

- Cookie-based: reads `r4_jwt` cookie set by NGINX auth sidecar (SAML/OIDC flow)
- localStorage fallback: reads `r4_jwt_token` for CLI-managed sessions
- Validates via `POST /auth/validate` endpoint

## Development

```bash
cd packages/r4-web
bun run dev            # Dev server on :3000, proxies /gw/* → :8000
bun run build          # Production build → dist/
bun run preview        # Preview production build
```

## NGINX Deployment

Served from NGINX under `/app/` prefix:

```nginx
location /app/ {
    alias /path/to/packages/r4-web/dist/;
    try_files $uri $uri/ /app/index.html;
}
```
