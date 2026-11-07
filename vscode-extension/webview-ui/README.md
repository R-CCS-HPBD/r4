# R4 Webview UI

React + Vite webview for the R4 VS Code Extension.

## Development

```bash
# Install dependencies
npm install

# Start dev server (http://localhost:5173)
npm run dev
```

## Build

```bash
# Build for production (outputs to ../dist/webview/)
npm run build
```

## Integration

The webview is served by the extension host. During development:

1. Start the dev server: `cd webview-ui && npm run dev`
2. Enable dev mode: set `R4_WEBVIEW_DEV=1` in your environment
3. Reload the VS Code window

For production builds, the extension automatically uses the bundled output.

## Architecture

- `src/types/` — Protocol types (mirrors extension host types)
- `src/lib/` — Bridge, markdown, utilities
- `src/hooks/` — React hooks for state management
- `src/components/` — UI components
- `src/index.css` — Global styles (VS Code themed)

## CSP

The Content Security Policy is set by the extension host.
- Dev mode: allows `http://localhost:5173` and `'unsafe-eval'`
- Prod mode: allows `vscode-resource://` and `'unsafe-eval'`

Never add `'self'` to script-src in a webview — VS Code webviews are
sandboxed and don't use `'self'`.
