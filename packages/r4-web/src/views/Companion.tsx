/**
 * Companion view — r4-web panel for the companion worker venue (wave T3, m6).
 *
 * Wires every route the m5 surface exposes: worker status, turn-attempt
 * chat with SSE reattach (ADR-028), pending-approval scaffold. The panel
 * never invents semantics the server does not have yet: the permission-mode
 * toggle renders reserved/disabled until the roundtrip milestone (T3c)
 * lands the route, and approvals render the (currently empty) real list.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Shell } from "../components/Shell";
import { useAuth } from "../auth";
import {
  ApiError,
  beginTurn,
  getWorker,
  listPendingApprovals,
  recordApprovalDecision,
  registryKeyFor,
  streamTurnEvents,
  type PendingApproval,
  type WorkerRow,
} from "../companion/api";
import { DEFAULT_PROJECT_ID } from "../companion/project";
import {
  createSession,
  loadActiveSessionId,
  loadHistory,
  loadSessions,
  saveActiveSessionId,
  saveHistory,
  saveSessions,
  type ChatMessage,
  type CompanionSession,
} from "../companion/sessions";

/** Project lane is single-tenant today (S2 finding); S5 lands isolation. */
const PROJECT_ID = DEFAULT_PROJECT_ID;
const MAX_REATTACH = 20;
const REATTACH_DELAY_MS = 1_000;
const APPROVALS_POLL_MS = 5_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function stateColor(state: string): string {
  const map: Record<string, string> = {
    active: "#34d399",
    running: "#34d399",
    draining: "#fb923c",
    exhausted: "#f87171",
    committed: "#34d399",
    failed: "#f87171",
    pending: "#fbbf24",
  };
  return map[state] ?? "#a5a3c2";
}

/* ── sub-panels ─────────────────────────────────────────────────────────── */

function WorkerCard({
  worker,
  error,
  onRefresh,
}: {
  worker: WorkerRow | null | undefined;
  error: string | null;
  onRefresh: () => void;
}) {
  if (error) {
    return (
      <div style={styles.card}>
        <div style={styles.cardTitle}>Companion worker</div>
        <div style={styles.errorText}>{error}</div>
        <button style={styles.smallButton} onClick={onRefresh}>
          Retry
        </button>
      </div>
    );
  }
  if (worker === undefined) {
    return (
      <div style={styles.card}>
        <div style={styles.cardTitle}>Companion worker</div>
        <div style={styles.dimText}>Checking…</div>
      </div>
    );
  }
  if (worker === null) {
    return (
      <div style={styles.card}>
        <div style={styles.cardTitle}>Companion worker</div>
        <div style={styles.dimText}>
          No companion worker provisioned for your account yet — the venue
          supervisor spawns one per (user, project) when your first turn is
          accepted.
        </div>
        <button style={styles.smallButton} onClick={onRefresh}>
          Retry
        </button>
      </div>
    );
  }
  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>Companion worker</div>
      <div style={styles.workerRow}>
        <span style={{ color: stateColor(worker.state), fontWeight: 600 }}>
          ● {worker.state}
        </span>
        <span style={styles.mono}>{worker.registry_key}</span>
      </div>
      <div style={styles.dimText}>
        job {worker.job_id} · restart budget {worker.attempt_index}/{worker.attempts_max}
      </div>
      <button style={styles.smallButton} onClick={onRefresh}>
        Refresh
      </button>
    </div>
  );
}

function ApprovalsPanel({
  pending,
  deciding,
  onDecide,
}: {
  pending: PendingApproval[];
  deciding: string | null;
  onDecide: (approvalId: string, decision: "once" | "always" | "no") => void;
}) {
  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>Pending approvals</div>
      {pending.length === 0 ? (
        <div style={styles.dimText}>
          None. Tool-call approval requests appear here; once/always/no
          decision routes are wired against the live surface.
        </div>
      ) : (
        pending.map((p, i) => {
          const id = p.approval_id ?? `pending-${i}`;
          return (
            <div key={id} style={styles.approvalRow}>
              <div style={styles.approvalBody}>
                <span style={styles.mono}>{p.tool_name ?? id}</span>
                {p.args_summary ? <span style={styles.dimText}>{p.args_summary}</span> : null}
              </div>
              <div style={styles.approvalActions}>
                {(["once", "always", "no"] as const).map((d) => (
                  <button
                    key={d}
                    style={styles.smallButton}
                    disabled={!p.approval_id || deciding === id}
                    onClick={() => p.approval_id && onDecide(p.approval_id, d)}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

function ModeCard() {
  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>Permission mode</div>
      <div style={styles.approvalActions}>
        <button style={{ ...styles.smallButton, ...styles.modeActive }} disabled>
          Ask
        </button>
        <button style={styles.smallButton} disabled>
          Auto
        </button>
      </div>
      <div style={styles.dimText}>
        Reserved — the server-side mode route lands with the chat roundtrip
        (T3c); this panel ships against the m5 surface only.
      </div>
    </div>
  );
}

function SessionsRail({
  sessions,
  activeId,
  onSelect,
  onNew,
}: {
  sessions: CompanionSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div style={styles.sessionsRail}>
      <button style={styles.newSessionButton} onClick={onNew}>
        + New chat
      </button>
      <div style={styles.sessionList}>
        {sessions.map((s) => (
          <button
            key={s.id}
            style={{
              ...styles.sessionItem,
              ...(s.id === activeId ? styles.sessionItemActive : {}),
            }}
            onClick={() => onSelect(s.id)}
            title={s.id}
          >
            {s.title}
          </button>
        ))}
      </div>
      <div style={styles.sessionsNote}>
        Sessions are local to this browser (GAP-COMP-3); the gateway-backed
        store arrives in T4.
      </div>
    </div>
  );
}

function Transcript({ messages }: { messages: ChatMessage[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);
  if (messages.length === 0) {
    return (
      <div style={styles.transcriptEmpty}>
        Send a message to start a turn. The assistant reply streams back over
        the turn-attempt channel and reattaches automatically after dropped
        connections — it never re-executes.
      </div>
    );
  }
  return (
    <div style={styles.transcript}>
      {messages.map((m, i) => (
        <div key={`${m.ts}-${i}`} style={m.role === "user" ? styles.msgUser : styles.msgAssistant}>
          {m.state === "pending" ? (
            <span style={styles.dimText}>…working (streaming turn events)</span>
          ) : m.state === "failed" ? (
            <span style={styles.errorText}>{m.text}</span>
          ) : (
            m.text
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}

/* ── main view ──────────────────────────────────────────────────────────── */

export function Companion() {
  const { user, isAuthenticated } = useAuth();
  const registryKey = user ? registryKeyFor(user.id, PROJECT_ID) : null;

  const [worker, setWorker] = useState<WorkerRow | null | undefined>(undefined);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [deciding, setDeciding] = useState<string | null>(null);

  const [sessions, setSessions] = useState<CompanionSession[]>(() => loadSessions());
  const [activeId, setActiveId] = useState<string | null>(() => loadActiveSessionId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  // Bootstrap: exactly one session exists and is active.
  useEffect(() => {
    if (sessions.length === 0) {
      const first = createSession();
      const next = [first];
      setSessions(next);
      setActiveId(first.id);
      saveSessions(next);
      saveActiveSessionId(first.id);
    } else if (!activeId || !sessions.some((s) => s.id === activeId)) {
      const fallback = sessions[0].id;
      setActiveId(fallback);
      saveActiveSessionId(fallback);
    }
  }, [sessions, activeId]);

  // Load history when the active session changes; abort any in-flight turn.
  useEffect(() => {
    abortRef.current?.abort();
    setSending(false);
    setMessages(activeId ? loadHistory(activeId) : []);
  }, [activeId]);

  // Persist history (skip the initial load render per session).
  const persistRef = useRef(false);
  useEffect(() => {
    if (!activeId) return;
    if (!persistRef.current) {
      persistRef.current = true;
      return;
    }
    saveHistory(activeId, messages);
  }, [messages, activeId]);
  useEffect(() => {
    persistRef.current = false;
  }, [activeId]);

  const refreshWorker = useCallback(async () => {
    if (!registryKey) return;
    setWorkerError(null);
    try {
      setWorker(await getWorker(registryKey));
    } catch (err) {
      const note =
        err instanceof ApiError && err.status === 401
          ? "Sign-in required — the companion surface is ownership-pinned."
          : err instanceof Error
            ? err.message
            : "worker fetch failed";
      setWorkerError(note);
      setWorker(null);
    }
  }, [registryKey]);

  useEffect(() => {
    refreshWorker();
  }, [refreshWorker]);

  // Poll pending approvals while a worker is known.
  useEffect(() => {
    if (!worker || !registryKey) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const rows = await listPendingApprovals(registryKey);
        if (!cancelled) setPendingApprovals(rows);
      } catch {
        /* keep last good list */
      }
    };
    tick();
    const timer = setInterval(tick, APPROVALS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [worker, registryKey]);

  // Abort the in-flight stream when the view unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const patchLastPendingAssistant = useCallback((patch: Partial<ChatMessage>) => {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].role === "assistant" && next[i].state === "pending") {
          next[i] = { ...next[i], ...patch };
          return next;
        }
      }
      return prev;
    });
  }, []);

  const streamAttempt = useCallback(
    async (attemptId: string, signal: AbortSignal): Promise<void> => {
      for (let reattach = 0; ; reattach += 1) {
        let outcome: "result" | "failed" | null = null;
        let resultRef: string | null = null;
        let resultContent: string | null = null;
        let failureNote = "turn attempt failed";
        await streamTurnEvents(
          attemptId,
          (ev) => {
            if (ev.type === "result") {
              outcome = "result";
              resultRef = ev.committed_response_ref;
              resultContent = ev.content ?? null;
            } else if (ev.type === "attempt_failed") {
              outcome = "failed";
            } else if (ev.type === "attempt_not_found") {
              outcome = "failed";
              failureNote = "turn attempt not found (registry row expired)";
            }
          },
          signal,
        );
        if (outcome === "result") {
          patchLastPendingAssistant({
            state: "committed",
            text:
              resultContent ??
              (resultRef
                ? `Response committed — ref ${resultRef}`
                : "Response committed."),
          });
          return;
        }
        if (outcome === "failed") {
          patchLastPendingAssistant({ state: "failed", text: failureNote });
          return;
        }
        if (signal.aborted) return;
        if (reattach >= MAX_REATTACH) {
          patchLastPendingAssistant({
            state: "failed",
            text: "turn still in flight — reopen this session later to reattach",
          });
          return;
        }
        await sleep(REATTACH_DELAY_MS);
      }
    },
    [patchLastPendingAssistant],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !worker || !registryKey || !activeId || sending) return;
    const now = new Date().toISOString();
    setMessages((prev) => [
      ...prev,
      { role: "user", text, ts: now },
      { role: "assistant", text: "", state: "pending", ts: now },
    ]);
    // Title the session from its first user message.
    setSessions((prev) => {
      const idx = prev.findIndex((s) => s.id === activeId);
      if (idx < 0 || prev[idx].title !== "New chat") return prev;
      const next = [...prev];
      next[idx] = { ...next[idx], title: text.length > 40 ? text.slice(0, 40) + "…" : text };
      saveSessions(next);
      return next;
    });
    setDraft("");
    setSending(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const accepted = await beginTurn({
        registryKey,
        message: text,
        sessionId: activeId,
        requestToken: crypto.randomUUID(),
      });
      await streamAttempt(accepted.attempt_id, controller.signal);
    } catch (err) {
      if (!controller.signal.aborted) {
        patchLastPendingAssistant({
          state: "failed",
          text: err instanceof Error ? err.message : "turn request failed",
        });
      }
    } finally {
      setSending(false);
    }
  }, [draft, worker, registryKey, activeId, sending, streamAttempt, patchLastPendingAssistant]);

  const decide = useCallback(
    async (approvalId: string, decision: "once" | "always" | "no") => {
      if (!registryKey) return;
      setDeciding(approvalId);
      try {
        await recordApprovalDecision(approvalId, decision);
        setPendingApprovals(await listPendingApprovals(registryKey));
      } catch {
        /* surface stays; next poll resynchronizes */
      } finally {
        setDeciding(null);
      }
    },
    [registryKey],
  );

  if (!isAuthenticated) {
    return (
      <Shell title="Companion">
        <div style={styles.dimText}>
          Sign in to use the companion panel — every route is ownership-pinned
          to your verified identity.
        </div>
      </Shell>
    );
  }

  return (
    <Shell title="Companion">
      <div style={styles.topGrid}>
        <WorkerCard worker={worker} error={workerError} onRefresh={refreshWorker} />
        <ApprovalsPanel pending={pendingApprovals} deciding={deciding} onDecide={decide} />
        <ModeCard />
      </div>
      <div style={styles.chatGrid}>
        <SessionsRail
          sessions={sessions}
          activeId={activeId}
          onSelect={(id) => {
            setActiveId(id);
            saveActiveSessionId(id);
          }}
          onNew={() => {
            const s = createSession();
            const next = [s, ...sessions];
            setSessions(next);
            saveSessions(next);
            setActiveId(s.id);
            saveActiveSessionId(s.id);
          }}
        />
        <div style={styles.chatColumn}>
          <Transcript messages={messages} />
          <div style={styles.composer}>
            <textarea
              style={styles.textarea}
              value={draft}
              placeholder={
                worker
                  ? "Message the companion…"
                  : "Waiting for a companion worker — see the status card above."
              }
              disabled={!worker || sending}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button
              style={styles.sendButton}
              disabled={!worker || sending || !draft.trim()}
              onClick={() => void send()}
            >
              {sending ? "Working…" : "Send"}
            </button>
          </div>
        </div>
      </div>
    </Shell>
  );
}

/* ── styles (dark theme, matches existing views) ────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  topGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
    gap: "1rem",
    marginBottom: "1.5rem",
  },
  card: {
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "0.5rem",
    padding: "1rem",
  },
  cardTitle: {
    fontSize: "0.8125rem",
    fontWeight: 600,
    color: "#94a3b8",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: "0.5rem",
  },
  workerRow: {
    display: "flex",
    gap: "0.75rem",
    alignItems: "center",
    marginBottom: "0.25rem",
  },
  mono: {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: "0.8125rem",
    color: "#e2e8f0",
  },
  dimText: {
    color: "#94a3b8",
    fontSize: "0.8125rem",
    lineHeight: 1.5,
  },
  errorText: {
    color: "#f87171",
    fontSize: "0.8125rem",
  },
  smallButton: {
    marginTop: "0.5rem",
    padding: "0.25rem 0.75rem",
    fontSize: "0.8125rem",
    color: "#e2e8f0",
    backgroundColor: "#334155",
    border: "1px solid #475569",
    borderRadius: "0.375rem",
    cursor: "pointer",
  },
  modeActive: {
    borderColor: "#38bdf8",
    color: "#38bdf8",
  },
  approvalRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "0.75rem",
    padding: "0.5rem 0",
    borderTop: "1px solid #334155",
  },
  approvalBody: {
    display: "flex",
    flexDirection: "column",
    gap: "0.125rem",
  },
  approvalActions: {
    display: "flex",
    gap: "0.375rem",
  },
  chatGrid: {
    display: "grid",
    gridTemplateColumns: "220px 1fr",
    gap: "1rem",
    minHeight: "28rem",
  },
  sessionsRail: {
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "0.5rem",
    padding: "0.75rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.75rem",
  },
  newSessionButton: {
    padding: "0.5rem",
    fontSize: "0.8125rem",
    fontWeight: 600,
    color: "#0f172a",
    backgroundColor: "#38bdf8",
    border: "none",
    borderRadius: "0.375rem",
    cursor: "pointer",
  },
  sessionList: {
    flex: 1,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: "0.25rem",
  },
  sessionItem: {
    textAlign: "left",
    padding: "0.375rem 0.5rem",
    fontSize: "0.8125rem",
    color: "#94a3b8",
    backgroundColor: "transparent",
    border: "none",
    borderRadius: "0.25rem",
    cursor: "pointer",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  sessionItemActive: {
    color: "#f1f5f9",
    backgroundColor: "#334155",
  },
  sessionsNote: {
    fontSize: "0.6875rem",
    color: "#64748b",
    lineHeight: 1.4,
  },
  chatColumn: {
    display: "flex",
    flexDirection: "column",
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "0.5rem",
    overflow: "hidden",
  },
  transcript: {
    flex: 1,
    overflowY: "auto",
    padding: "1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  transcriptEmpty: {
    flex: 1,
    padding: "2rem",
    color: "#64748b",
    fontSize: "0.875rem",
    lineHeight: 1.6,
  },
  msgUser: {
    alignSelf: "flex-end",
    maxWidth: "70%",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#0c4a6e",
    borderRadius: "0.75rem",
    fontSize: "0.875rem",
    whiteSpace: "pre-wrap",
  },
  msgAssistant: {
    alignSelf: "flex-start",
    maxWidth: "70%",
    padding: "0.5rem 0.75rem",
    backgroundColor: "#334155",
    borderRadius: "0.75rem",
    fontSize: "0.875rem",
    whiteSpace: "pre-wrap",
  },
  composer: {
    display: "flex",
    gap: "0.5rem",
    padding: "0.75rem",
    borderTop: "1px solid #334155",
  },
  textarea: {
    flex: 1,
    minHeight: "2.5rem",
    maxHeight: "8rem",
    padding: "0.5rem 0.75rem",
    fontSize: "0.875rem",
    color: "#e2e8f0",
    backgroundColor: "#0f172a",
    border: "1px solid #475569",
    borderRadius: "0.375rem",
    resize: "vertical",
    fontFamily: "inherit",
  },
  sendButton: {
    padding: "0.5rem 1.25rem",
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#0f172a",
    backgroundColor: "#38bdf8",
    border: "none",
    borderRadius: "0.375rem",
    cursor: "pointer",
    alignSelf: "flex-end",
  },
};
