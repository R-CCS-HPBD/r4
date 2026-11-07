/**
 * Client-side companion session registry.
 *
 * Chat sessions are local-only today (GAP-COMP-3); the gateway-backed store
 * is T4. Until then the r4-web panel persists session shells + transcripts
 * in localStorage, keyed by session id, so switching sessions never loses
 * visible history. Server-side turn attempts are idempotent by
 * request_token — re-sends after a reload never double-post.
 */

export interface CompanionSession {
  id: string;
  title: string;
  createdAt: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  /** assistant rows only: lifecycle of the backing turn attempt */
  state?: "pending" | "committed" | "failed";
  ts: string;
}

const SESSIONS_KEY = "r4_companion_sessions";
const ACTIVE_KEY = "r4_companion_active_session";
const HISTORY_PREFIX = "r4_companion_history_";
const MAX_HISTORY = 100;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota exceeded — session stays in memory only */
  }
}

export function loadSessions(): CompanionSession[] {
  return readJson<CompanionSession[]>(SESSIONS_KEY, []);
}

export function saveSessions(sessions: CompanionSession[]): void {
  writeJson(SESSIONS_KEY, sessions);
}

export function createSession(): CompanionSession {
  return {
    id: `sess-${crypto.randomUUID()}`,
    title: "New chat",
    createdAt: new Date().toISOString(),
  };
}

export function loadActiveSessionId(): string | null {
  return localStorage.getItem(ACTIVE_KEY);
}

export function saveActiveSessionId(id: string): void {
  localStorage.setItem(ACTIVE_KEY, id);
}

export function loadHistory(sessionId: string): ChatMessage[] {
  return readJson<ChatMessage[]>(HISTORY_PREFIX + sessionId, []);
}

export function saveHistory(sessionId: string, messages: ChatMessage[]): void {
  writeJson(HISTORY_PREFIX + sessionId, messages.slice(-MAX_HISTORY));
}
