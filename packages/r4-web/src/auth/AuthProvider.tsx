import { useState, useEffect, createContext, useContext, type ReactNode } from "react";

export interface UserInfo {
  id: string;
  name: string;
  email: string;
  admin: boolean;
}

export type AuthStatus = "unauthenticated" | "loading" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: UserInfo | null;
  login: (apiKeyName: string, baseUrl: string) => Promise<void>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

function readStorageToken(): string | null {
  return localStorage.getItem("r4_jwt_token") || null;
}

async function validateSession(token: string): Promise<UserInfo | null> {
  try {
    const resp = await fetch("/auth/validate", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return {
      id: data.user_id || data.x_user_id || "unknown",
      name: data.name || data.x_user_id || "User",
      email: data.email || "",
      admin: data.admin || false,
    };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<UserInfo | null>(null);

  useEffect(() => {
    const init = async () => {
      // Cookie from NGINX auth sidecar (SAML/OIDC flow)
      const cookieToken = readCookie("r4_jwt");
      if (cookieToken) {
        const info = await validateSession(cookieToken);
        if (info) {
          setUser(info);
          setStatus("authenticated");
          return;
        }
      }

      // localStorage fallback (from CLI-managed login)
      const storedToken = readStorageToken();
      if (storedToken) {
        const info = await validateSession(storedToken);
        if (info) {
          setUser(info);
          setStatus("authenticated");
          return;
        }
      }

      setStatus("unauthenticated");
    };
    init();
  }, []);

  const login = async (apiKeyName: string, baseUrl: string) => {
    setStatus("loading");
    try {
      const resp = await fetch(`${baseUrl}/gw/ops/ping`, {
        headers: { Authorization: `Bearer ${apiKeyName}` },
      });
      if (!resp.ok) {
        setStatus("unauthenticated");
        return;
      }
      // Store token for future use
      const token = apiKeyName;
      localStorage.setItem("r4_jwt_token", token);
      const info: UserInfo = {
        id: "local",
        name: apiKeyName.slice(0, 12) + "...",
        email: "",
        admin: false,
      };
      setUser(info);
      setStatus("authenticated");
    } catch {
      setStatus("unauthenticated");
    }
  };

  const logout = async () => {
    localStorage.removeItem("r4_jwt_token");
    // Clear the r4_jwt cookie if present
    document.cookie = "r4_jwt=; path=/app/; max-age=0";
    setUser(null);
    setStatus("unauthenticated");
  };

  return (
    <AuthContext.Provider
      value={{
        status,
        user,
        login,
        logout,
        isAuthenticated: status === "authenticated",
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
