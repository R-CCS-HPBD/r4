/**
 * Entry point for the VS Code webview protocol bridge.
 *
 * Wraps acquireVsCodeApi().postMessage with nonce injection and a
 * simple subscribe/publish event system so React components never
 * touch postMessage directly.
 */

import type { InboundEvent, OutboundMessage } from '@/types/protocol';

// ── VS Code API accessor ──────────────────────────────────────────────────

type VsCodeApi = ReturnType<typeof acquireVsCodeApi>;

// `acquireVsCodeApi` may only be called once per webview document.
let _api: VsCodeApi | null = null;
let _apiResolved = false;

function getVsCode(): VsCodeApi | null {
  if (!_apiResolved) {
    _apiResolved = true;
    try {
      _api = acquireVsCodeApi();
    } catch {
      _api = null;
    }
  }
  return _api;
}

// ── Message bridge ─────────────────────────────────────────────────────────

let _token = '';
let _wired = false;
const _listeners = new Set<(event: InboundEvent) => void>();

/**
 * Wire up the inbound listener. Safe to call more than once — React StrictMode
 * mounts effects twice, and a second window listener would double every event.
 */
export function initBridge(token: string): void {
  _token = token;
  if (_wired) return;
  _wired = true;
  window.addEventListener('message', onInbound);
}

export function post(message: OutboundMessage): void {
  const vscode = getVsCode();
  if (!vscode || !_token) return;
  vscode.postMessage({ ...message, messageToken: _token });
}

export function subscribe(fn: (event: InboundEvent) => void): () => void {
  _listeners.add(fn);
  return () => {
    _listeners.delete(fn);
  };
}

function onInbound(e: MessageEvent): void {
  const data: unknown = e.data;
  if (!data || typeof data !== 'object') return;
  if (typeof (data as { type?: unknown }).type !== 'string') return;
  for (const fn of _listeners) {
    try {
      fn(data as InboundEvent);
    } catch {
      // A failing subscriber must not break delivery to the others.
    }
  }
}

// ── Utility helpers ────────────────────────────────────────────────────────

export function relTime(iso: string | undefined): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
