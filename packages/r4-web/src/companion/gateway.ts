/**
 * Gateway token resolution + authenticated fetch for the companion surface.
 *
 * Mirrors AuthProvider's credential precedence (NGINX sidecar cookie first,
 * CLI-managed localStorage token second) so companion API calls work under
 * both login paths. The sidecar consumes the Bearer header and issues the
 * signed X-User-ID / X-Verified-* identity tuple upstream (ADR-027).
 */

const JWT_COOKIE = "r4_jwt";
const JWT_STORAGE_KEY = "r4_jwt_token";

export function resolveGatewayToken(): string | null {
  const match = document.cookie.match(new RegExp("(^| )" + JWT_COOKIE + "=([^;]+)"));
  if (match) {
    return decodeURIComponent(match[2]);
  }
  return localStorage.getItem(JWT_STORAGE_KEY);
}

/** fetch() with the resolved Bearer token attached (never overrides an explicit header). */
export async function gatewayFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = resolveGatewayToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(path, { ...init, headers });
}
