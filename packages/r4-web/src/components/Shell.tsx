import type { ReactNode } from "react";

export function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <h1 style={styles.title}>{title}</h1>
      </header>
      <main style={styles.main}>{children}</main>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: "100vh",
    backgroundColor: "#0f172a",
    color: "#e2e8f0",
  },
  header: {
    padding: "1rem 2rem",
    borderBottom: "1px solid #334155",
    backgroundColor: "#1e293b",
  },
  title: {
    margin: 0,
    fontSize: "1.25rem",
    fontWeight: 600,
  },
  main: {
    padding: "2rem",
  },
};
