/**
 * Map companion bridge state/dashboard payloads into Bun workspace fields.
 * Do not apply bridge payloads directly — shapes may differ.
 */

import type { UiEndpoint, UiJob, UiModel, UiSkill } from "../types";
import { actions } from "../stores";
import { mapEndpoint, mapJob, mapModel, mapSkill } from "../adapters/mappers";
import { mapSnapshotVenueRuntime } from "../lib/venue-runtime";
import { mapWebAccess } from "../lib/web-access";
import { mapSandbox } from "../lib/sandbox";

export interface BridgeStatePatch {
  /** G-P9-2: live session id from snapshot session.id (fork source). */
  sessionId?: string;
  currentModel?: string | null;
  endpoints?: UiEndpoint[];
  models?: UiModel[];
  jobs?: UiJob[];
  skills?: UiSkill[];
  /** Wave C: server mode/permission ladder + plan-mode state. */
  interactionMode?: string;
  permissionMode?: string;
  /** ADR-035: badge honesty for TRUST — 'server'|'legacy'|'' (empty = not TRUST). */
  trustSource?: string;
  planAwaitingConfirmation?: boolean;
  planPendingRequest?: string;
  /** Session knowledge-acquisition mode (write gate, mid-session switchable). */
  knowledgeAcquisition?: { mode: string; graphs: string[] };
  /** ADR-075 G2: session workflow arm (absent = payload did not carry it). */
  workflowArmed?: boolean;
  /** Wave E: identity context (snapshot config). */
  projectId?: string;
  profile?: string;
  sourceName?: string;
  /** ADR-086 identity chip: display-only JWT subject from snapshot config. */
  username?: string;
  /** Additive (2026-10-07): "jwt" | "api-key" | "none" — why username may be empty. */
  credential?: string;
  /** Wave 2026-09-21 (ADR-046 A2): session venue + remote runtime projection. */
  sessionVenue?: string;
  venueRuntime?: import("../lib/venue-runtime").VenueRuntime | null;
  /** ADR-099 D-2: the session's web-access choice (absent: bridge predates it). */
  webAccess?: import("../lib/web-access").WebAccessChoice | null;
  /** ADR-101: the session's local tool sandbox (absent: bridge predates it). */
  sandbox?: import("../lib/sandbox").SandboxView | null;
  degradedFields: string[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Extract known Bun-relevant fields from bridge `state` and `dashboard`.
 * Unknown fields are ignored; unmapped nested shapes are listed in degradedFields.
 */
function mapModelField(source: Record<string, unknown>, patch: BridgeStatePatch): boolean {
  if ("model" in source || "current_model" in source) {
    const model = source.model ?? source.current_model;
    patch.currentModel = model == null || model === "" ? null : String(model);
    return true;
  }
  return false;
}

/** Session-block fields mapped verbatim when string-typed (Wave C ladder). */
const LADDER_STRING_FIELDS: Array<[string, "interactionMode" | "permissionMode" | "trustSource" | "planPendingRequest"]> = [
  ["interaction_mode", "interactionMode"],
  ["permission_mode", "permissionMode"],
  ["trust_source", "trustSource"],
  ["plan_pending_request", "planPendingRequest"],
];

/** Fields whose empty string means "none now" (ADR-104): leaving autopilot
 * clears the trust source, an answered plan its pending request. An empty
 * mode or permission string is a missing value instead (kept as is). */
const CLEARABLE_LADDER_FIELDS = new Set(["trust_source", "plan_pending_request"]);

/** Wave C: mode/permission ladder + fork's session id (snapshot session block). */
function mapSessionLadder(st: Record<string, unknown>, patch: BridgeStatePatch): void {
  const session = asRecord(st.session);
  if (!session) return;
  // G-P9-2: fork needs the live session id client-side.
  if (typeof session.id === "string" && session.id) {
    patch.sessionId = session.id;
  }
  mapLadderStrings(session, patch);
  if (typeof session.plan_awaiting_confirmation === "boolean") {
    patch.planAwaitingConfirmation = session.plan_awaiting_confirmation;
  }
  mapKnowledgeAcquisition(session, patch);
  // Session workflow arm (ADR-075 G2) — undefined leaves the local value.
  if (typeof session.workflow_armed === "boolean") {
    patch.workflowArmed = session.workflow_armed;
  }
}

/** ADR-035: badge honesty — trust_source is 'server' (allowlist-enforced)
 * vs 'legacy' (process-global trust, NOT server-enforced); empty when the
 * mode isn't autopilot (and an empty string clears, ADR-104). */
function mapLadderStrings(session: Record<string, unknown>, patch: BridgeStatePatch): void {
  for (const [key, patchKey] of LADDER_STRING_FIELDS) {
    const value = session[key];
    if (typeof value === "string" && (value || CLEARABLE_LADDER_FIELDS.has(key))) {
      patch[patchKey] = value;
    }
  }
}

/** Session knowledge-acquisition mode (write gate). */
function mapKnowledgeAcquisition(session: Record<string, unknown>, patch: BridgeStatePatch): void {
  const acq = asRecord(session.knowledge_acquisition);
  if (!acq) return;
  patch.knowledgeAcquisition = {
    mode: typeof acq.mode === "string" ? acq.mode : "open",
    graphs: Array.isArray(acq.graphs) ? acq.graphs.map(String) : [],
  };
}

function mapSkillsField(
  st: Record<string, unknown>,
  patch: BridgeStatePatch,
  degradedFields: string[],
): void {
  if ("skills" in st && Array.isArray(st.skills)) {
    try {
      patch.skills = st.skills.map((s) => mapSkill(s as never));
    } catch {
      degradedFields.push("state.skills");
    }
  }
}

/** Wave 2026-09-21: venue + remote runtime (snapshot session block). */
function mapSessionVenue(st: Record<string, unknown>, patch: BridgeStatePatch): void {
  const session = asRecord(st.session);
  if (!session) return;
  if (typeof session.venue === "string") patch.sessionVenue = session.venue;
  if ("web_access" in session) patch.webAccess = mapWebAccess(session.web_access);
  if ("sandbox" in session) patch.sandbox = mapSandbox(session.sandbox);
  if ("venue_runtime" in session) {
    patch.venueRuntime = mapSnapshotVenueRuntime(session.venue_runtime);
  }
}

/** Wave E: identity context (snapshot config block). */
function mapConfigIdentity(st: Record<string, unknown>, patch: BridgeStatePatch): void {
  const cfg = asRecord(st.config);
  if (!cfg) return;
  if (typeof cfg.project_id === "string" && cfg.project_id) patch.projectId = cfg.project_id;
  if (typeof cfg.profile === "string" && cfg.profile) patch.profile = cfg.profile;
  if (typeof cfg.source_name === "string" && cfg.source_name) patch.sourceName = cfg.source_name;
  if (typeof cfg.username === "string" && cfg.username) patch.username = cfg.username;
  if (typeof cfg.credential === "string" && cfg.credential) patch.credential = cfg.credential;
}

function mapSnapshotBlock(
  st: Record<string, unknown>,
  patch: BridgeStatePatch,
  degradedFields: string[],
): void {
  const session = asRecord(st.session);
  if (!mapModelField(st, patch) && ("session" in st || "interaction_mode" in st) && session) {
    mapModelField(session, patch);
  }
  // Wave C: mode/permission ladder (snapshot root fields).
  mapSessionLadder(st, patch);
  mapSkillsField(st, patch, degradedFields);
  mapConfigIdentity(st, patch);
  mapSessionVenue(st, patch);
}

function mapEndpointsField(
  dash: Record<string, unknown>,
  patch: BridgeStatePatch,
  degradedFields: string[],
): void {
  if (Array.isArray(dash.endpoints)) {
    try {
      patch.endpoints = dash.endpoints.map((e) => mapEndpoint(e as never));
    } catch {
      degradedFields.push("dashboard.endpoints");
    }
  } else if ("endpoints" in dash) {
    degradedFields.push("dashboard.endpoints");
  }
}

function mapDashboardBlock(
  dash: Record<string, unknown>,
  patch: BridgeStatePatch,
  degradedFields: string[],
): void {
  mapEndpointsField(dash, patch, degradedFields);
  if (Array.isArray(dash.models)) {
    try {
      patch.models = dash.models.map((m) => mapModel(m as never));
    } catch {
      degradedFields.push("dashboard.models");
    }
  }
  if (Array.isArray(dash.jobs)) {
    try {
      patch.jobs = dash.jobs.map((j) => mapJob(j as never));
    } catch {
      degradedFields.push("dashboard.jobs");
    }
  }
}

export function mapBridgeStateToWorkspace(
  state: unknown,
  dashboard: unknown,
): BridgeStatePatch {
  const degradedFields: string[] = [];
  const patch: BridgeStatePatch = { degradedFields };

  const st = asRecord(state);
  if (st) {
    mapSnapshotBlock(st, patch, degradedFields);
  } else if (state != null) {
    degradedFields.push("state");
  }

  const dash = asRecord(dashboard);
  if (dash) {
    mapDashboardBlock(dash, patch, degradedFields);
  } else if (dashboard != null) {
    degradedFields.push("dashboard");
  }

  return patch;
}

/** Apply a mapped patch into the Bun workspace store. */
export function applyBridgeStatePatch(patch: BridgeStatePatch): void {
  const backend: {
    endpoints?: UiEndpoint[];
    models?: UiModel[];
    jobs?: UiJob[];
    skills?: UiSkill[];
  } = {};
  if (patch.endpoints) backend.endpoints = patch.endpoints;
  if (patch.models) backend.models = patch.models;
  if (patch.jobs) backend.jobs = patch.jobs;
  if (patch.skills) backend.skills = patch.skills;
  if (Object.keys(backend).length > 0) {
    actions.setBackendState(backend);
  }
  if (patch.sessionId !== undefined) {
    actions.setSessionId(patch.sessionId);
  }
  if (patch.currentModel !== undefined) {
    actions.setCurrentModel(patch.currentModel);
  }
  // Wave C: mode/permission ladder + plan-mode state (null-safe per-field).
  actions.setInteractionModeState({
    interactionMode: patch.interactionMode,
    permissionMode: patch.permissionMode,
    trustSource: patch.trustSource,
    planAwaitingConfirmation: patch.planAwaitingConfirmation,
    planPendingRequest: patch.planPendingRequest,
  });
  // Session knowledge-acquisition mode (undefined = payload did not carry it).
  actions.setKnowledgeAcquisition(patch.knowledgeAcquisition);
  actions.setWorkflowArmed(patch.workflowArmed);
  // Wave E: identity context.
  actions.setIdentityState({
    projectId: patch.projectId,
    profile: patch.profile,
    sourceName: patch.sourceName,
    username: patch.username,
    credential: patch.credential,
  });
  applyVenuePatch(patch);
  if (patch.degradedFields.length > 0) {
    actions.addEvent({
      id: `bridge-map-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type: "system",
      actor: "bridge",
      action: "degraded-map",
      detail: patch.degradedFields.join(", "),
    });
  }
}

/** Wave 2026-09-21: venue + remote runtime (undefined = payload did not carry it). */
function applyVenuePatch(patch: BridgeStatePatch): void {
  if (patch.sessionVenue !== undefined) actions.setSessionVenue(patch.sessionVenue);
  if (patch.venueRuntime !== undefined) actions.setVenueRuntime(patch.venueRuntime);
  if (patch.webAccess !== undefined) actions.setWebAccess(patch.webAccess);
  if (patch.sandbox !== undefined) actions.setSandbox(patch.sandbox);
}
