#!/usr/bin/env node
/**
 * Generate Aurora design-token artifacts from tokens.json (source of truth).
 *
 * Outputs:
 *   ../tokens.css                       — CSS custom properties + canonical states
 *   ../tokens.ts                        — typed TS export
 *   ../theme.cli.json                   — rich theme mapping (canonical)
 *   ../../../r4/resources/theme.aurora.json — packaged copy consumed by r4.ui_theme
 *
 * Usage:
 *   node scripts/generate.mjs          regenerate all artifacts
 *   node scripts/generate.mjs --check  fail (exit 1) if any artifact is stale
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = join(pkgRoot, '..', '..');
const tokens = JSON.parse(readFileSync(join(pkgRoot, 'tokens.json'), 'utf8'));
const checkOnly = process.argv.includes('--check');

// ---------------------------------------------------------------------------
// tokens.css
// ---------------------------------------------------------------------------
const cssVars = `:root {
  /* Accent */
  --r4-accent: ${tokens.color.accent.primary.toLowerCase()};
  --r4-accent-hover: ${tokens.color.accent.primaryHover.toLowerCase()};
  --r4-accent-secondary: ${tokens.color.accent.secondary.toLowerCase()};
  --r4-accent-tertiary: ${tokens.color.accent.tertiary.toLowerCase()};

  /* Semantic */
  --r4-success: ${tokens.color.semantic.success.toLowerCase()};
  --r4-warning: ${tokens.color.semantic.warning.toLowerCase()};
  --r4-error: ${tokens.color.semantic.error.toLowerCase()};
  --r4-info: ${tokens.color.semantic.info.toLowerCase()};

  /* Surface */
  --r4-surface-base: ${tokens.color.surface.base.toLowerCase()};
  --r4-surface-raised: ${tokens.color.surface.raised.toLowerCase()};
  --r4-surface-overlay: ${tokens.color.surface.overlay.toLowerCase()};
  --r4-surface-border: ${tokens.color.surface.border.toLowerCase()};

  /* Text */
  --r4-text-primary: ${tokens.color.text.primary.toLowerCase()};
  --r4-text-secondary: ${tokens.color.text.secondary.toLowerCase()};
  --r4-text-muted: ${tokens.color.text.muted.toLowerCase()};
  --r4-text-on-accent: ${tokens.color.text.onAccent.toLowerCase()};

  /* Gradients */
  --r4-gradient-aurora: ${tokens.color.gradient.aurora};
  --r4-gradient-aurora-soft: ${tokens.color.gradient.auroraSoft};
  --r4-gradient-verifying: ${tokens.color.gradient.verifying};

  /* Spacing */
  --r4-space-xs: ${tokens.spacing.xs};
  --r4-space-sm: ${tokens.spacing.sm};
  --r4-space-md: ${tokens.spacing.md};
  --r4-space-lg: ${tokens.spacing.lg};
  --r4-space-xl: ${tokens.spacing.xl};

  /* Radius */
  --r4-radius-sm: ${tokens.radius.sm};
  --r4-radius-md: ${tokens.radius.md};
  --r4-radius-lg: ${tokens.radius.lg};
  --r4-radius-pill: ${tokens.radius.pill};

  /* Motion */
  --r4-duration-instant: ${tokens.motion.duration.instant};
  --r4-duration-fast: ${tokens.motion.duration.fast};
  --r4-duration-base: ${tokens.motion.duration.base};
  --r4-duration-slow: ${tokens.motion.duration.slow};
  --r4-ease-standard: ${tokens.motion.easing.standard};
  --r4-ease-emphasized: ${tokens.motion.easing.emphasized};
  --r4-ease-spring: ${tokens.motion.easing.spring};

  /* Typography (CJK-aware pairing for JA/EN parity) */
  --r4-font-sans: ${tokens.typography.family.sans.replace(/"/g, '"')};
  --r4-font-mono: ${tokens.typography.family.mono};

  /* Shadow */
  --r4-shadow-sm: ${tokens.shadow.sm};
  --r4-shadow-md: ${tokens.shadow.md};
  --r4-shadow-glow: ${tokens.shadow.glow};
  --r4-shadow-glow-cyan: ${tokens.shadow.glowCyan};
}
`;

const CSS_STATES = readFileSync(join(pkgRoot, 'scripts', 'states.css'), 'utf8');
const CSS_HEADER = `/* R4 Design Tokens — "Aurora" theme
   GENERATED FILE — do not edit. Source of truth: tokens.json.
   Regenerate with \`npm run build\` in packages/r4-design.
   Consumers: vscode-extension/webview-ui, packages/r4-web, packages/r4-tui-v3 (mapped), r4 CLI (theme.cli.json). */

`;
const tokensCss = `${CSS_HEADER}${cssVars}\n${CSS_STATES}`;

// ---------------------------------------------------------------------------
// tokens.ts
// ---------------------------------------------------------------------------
const tokensTs = `/**
 * R4 Design Tokens — typed TS mirror of tokens.json.
 * GENERATED FILE — do not edit. Regenerate via \`npm run build\`.
 */
export const tokens = ${JSON.stringify(tokens, null, 2)
  .replace(/"([^"]+)":/g, '$1:')
  .replace(/\$schema[^,]*,\n/, '')
  .trim()
  .replace(/^/, '').replace(/;$/, '')} as const;

export type R4Tokens = typeof tokens;
export default tokens;
`;

// ---------------------------------------------------------------------------
// theme.cli.json (+ packaged copy for r4)
// ---------------------------------------------------------------------------
const theme = {
  name: tokens.name,
  source: 'packages/r4-design/tokens.json',
  styles: {
    'r4.accent': tokens.color.accent.primary,
    'r4.accent2': tokens.color.accent.secondary,
    'r4.tertiary': tokens.color.accent.tertiary,
    'r4.success': tokens.color.semantic.success,
    'r4.warning': tokens.color.semantic.warning,
    'r4.error': tokens.color.semantic.error,
    'r4.info': tokens.color.semantic.info,
    'r4.muted': tokens.color.text.muted,
    'r4.title': `bold ${tokens.color.accent.primary}`,
    'r4.subtitle': tokens.color.text.secondary,
    'r4.thinking': `italic ${tokens.color.accent.secondary}`,
    'r4.tool': tokens.color.semantic.info,
    'r4.approval': `bold ${tokens.color.semantic.warning}`,
    'r4.banner': `bold ${tokens.color.accent.primary} on ${tokens.color.surface.base}`,
  },
};
const themeJson = `${JSON.stringify(theme, null, 2)}\n`;

// ---------------------------------------------------------------------------
// Write or check
// ---------------------------------------------------------------------------
const outputs = new Map([
  [join(pkgRoot, 'tokens.css'), tokensCss],
  [join(pkgRoot, 'tokens.ts'), tokensTs],
  [join(pkgRoot, 'theme.cli.json'), themeJson],
  [join(repoRoot, 'r4', 'resources', 'theme.aurora.json'), themeJson],
]);

let stale = [];
for (const [file, content] of outputs) {
  let current = null;
  try {
    current = readFileSync(file, 'utf8');
  } catch {
    /* missing = stale */
  }
  if (checkOnly) {
    if (current !== content) stale.push(file);
  } else {
    writeFileSync(file, content);
    console.log(`wrote ${file}`);
  }
}

if (checkOnly && stale.length > 0) {
  console.error('Design-token artifacts out of date. Run `npm run build` in packages/r4-design:');
  for (const f of stale) console.error(`  - ${f}`);
  process.exit(1);
}
if (checkOnly) console.log('Design-token artifacts up to date.');
