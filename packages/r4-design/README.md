# @r4/design-tokens — Aurora

Single source of truth for the R4 Platform visual identity. All user-facing
surfaces (VS Code webview, r4-tui-v3, r4-web, r4 CLI) derive their look from
this package so motion, color, and typography stay consistent.

## Files

| File | Consumer |
|------|----------|
| `tokens.json` | Source of truth (edit me) |
| `tokens.css` | CSS custom properties + canonical state animations (thinking / verifying / streaming / live) |
| `tokens.ts` | Typed TS export for TUI/web theming |
| `theme.cli.json` | Python `rich` theme mapping for the r4 CLI |

## Canonical states (motion spec)

| State | Visual | Class |
|-------|--------|-------|
| Thinking | Aurora shimmer sweep | `.r4-thinking-shimmer` |
| Verifying | Rotating tri-color gradient ring | `.r4-verifying-ring` |
| Streaming | Blinking cyan caret | `.r4-caret` |
| Live / ready | Pulsing success dot | `.r4-status-dot` |
| Entrance | Fade + rise | `.r4-anim-fade-up` |
| Pop (approvals/badges) | Spring pop | `.r4-anim-pop` |

## Brand accents

- Primary **Aurora Violet** `#7C6CFF`, secondary **Ray Cyan** `#2DD4EE`
- Gradient: `linear-gradient(135deg, #7C6CFF, #2DD4EE)` — the "aurora"
- CJK-aware type stack (Noto Sans JP / Hiragino / Yu Gothic) for JA⇄EN parity

## Rules

- Edit `tokens.json`, regenerate the rest (`npm run build`).
- Never hardcode hex values in consumers — always reference tokens.
- Respect `prefers-reduced-motion` (handled in `tokens.css`).
