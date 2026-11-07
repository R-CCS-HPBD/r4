/**
 * Bilingual locale model for the Ja⇄En parity track.
 *
 * Strategy: minimal-intrusive — surfaces opt in by calling `t()` with a key.
 * No wholesale migration today; this installs the hook + locale resolution
 * so the rollout is "swap strings" later rather than "restructure app".
 */

export type Locale = 'en' | 'ja';

/** Detected once at load; can be overridden via localStorage or query param. */
export function resolveLocale(): Locale {
  const forced = new URLSearchParams(window.location.search).get('lang');
  if (forced === 'ja' || forced === 'en') return forced as Locale;
  const saved = window.localStorage.getItem('r4.locale');
  if (saved === 'ja' || saved === 'en') return saved as Locale;
  const nav = window.navigator.language.toLowerCase();
  return nav.startsWith('ja') ? 'ja' : 'en';
}

/** Active locale (resolved once; mutable via setLocale). */
export const locale: { current: Locale } = { current: resolveLocale() };

export function setLocale(next: Locale): void {
  locale.current = next;
  window.localStorage.setItem('r4.locale', next);
}

/** Minimal key lookups; pass both languages, pick by current locale. */
export function t(en: string, ja: string): string {
  return locale.current === 'ja' ? ja : en;
}
