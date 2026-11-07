import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { DEMO_MODE } from "../demo-mode";

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout, isAuthenticated } = useAuth();
  const location = useLocation();

  // Mock-projection views are tagged till-live when the demo is off
  // (G-P3-1) so the sidebar never presents mock data as a live surface.
  const tillLive = (label: string) => (DEMO_MODE ? label : `${label} · till-live`);
  const navItems = [
    { path: "/", label: tillLive("Overview") },
    { path: "/report", label: tillLive("Report") },
    { path: "/artifacts", label: tillLive("Artifacts") },
    { path: "/graph", label: "Graph" },
    { path: "/models", label: "Models" },
    { path: "/jobs", label: "Jobs" },
    { path: "/endpoints", label: "Endpoints" },
    { path: "/assets", label: "Assets" },
    { path: "/timeline", label: tillLive("Timeline") },
    { path: "/companion", label: "Companion" },
  ];

  return (
    <div style={styles.layout}>
      {/* Sidebar */}
      <nav style={styles.sidebar}>
        <div style={styles.brand}>
          <span style={styles.brandIcon}>◆</span>
          <span style={styles.brandText}>R4</span>
        </div>
        <ul style={styles.navList}>
          {navItems.map((item) => (
            <li key={item.path}>
              <Link
                to={item.path}
                style={{
                  ...styles.navItem,
                  ...(location.pathname === item.path ? styles.navItemActive : {}),
                }}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
        <div style={styles.sidebarFooter}>
          {isAuthenticated && user ? (
            <>
              <div style={styles.userBadge}>
                <span style={styles.userDot} />
                <span style={styles.userName}>{user.name}</span>
              </div>
              <button style={styles.logoutBtn} onClick={logout}>
                Logout
              </button>
            </>
          ) : (
            <Link to="/login" style={styles.loginLink}>
              Login
            </Link>
          )}
        </div>
      </nav>

      {/* Content area */}
      <div style={styles.content}>
        {children}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  layout: {
    display: "flex",
    minHeight: "100vh",
    backgroundColor: "#0f172a",
  },
  sidebar: {
    width: "220px",
    backgroundColor: "#1e293b",
    borderRight: "1px solid #334155",
    display: "flex",
    flexDirection: "column",
    position: "fixed" as const,
    top: 0,
    left: 0,
    bottom: 0,
  },
  brand: {
    padding: "1.25rem 1rem",
    borderBottom: "1px solid #334155",
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
  },
  brandIcon: {
    color: "#38bdf8",
    fontSize: "1.25rem",
  },
  brandText: {
    color: "#f1f5f9",
    fontWeight: 700,
    fontSize: "1.125rem",
  },
  navList: {
    listStyle: "none",
    margin: 0,
    padding: "0.75rem 0",
    flex: 1,
  },
  navItem: {
    display: "block",
    padding: "0.5rem 1rem",
    color: "#94a3b8",
    textDecoration: "none",
    fontSize: "0.875rem",
    transition: "color 0.15s, background-color 0.15s",
  },
  navItemActive: {
    color: "#f1f5f9",
    backgroundColor: "#334155",
    borderLeft: "2px solid #38bdf8",
  },
  sidebarFooter: {
    padding: "1rem",
    borderTop: "1px solid #334155",
  },
  userBadge: {
    display: "flex",
    alignItems: "center",
    gap: "0.375rem",
    marginBottom: "0.5rem",
    fontSize: "0.75rem",
    color: "#94a3b8",
  },
  userDot: {
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    backgroundColor: "#22c55e",
    display: "inline-block",
  },
  userName: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  logoutBtn: {
    width: "100%",
    padding: "0.375rem 0.75rem",
    backgroundColor: "#334155",
    border: "1px solid #475569",
    borderRadius: "4px",
    color: "#cbd5e1",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  loginLink: {
    color: "#38bdf8",
    textDecoration: "none",
    fontSize: "0.875rem",
  },
  content: {
    flex: 1,
    marginLeft: "220px",
  },
};
