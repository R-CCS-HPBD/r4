import { Routes, Route } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Shell } from "./components/Shell";
import { DEMO_MODE } from "./demo-mode";
import { Overview } from "./views/Overview";
import { Report } from "./views/Report";
import { Artifacts } from "./views/Artifacts";
import { Graph } from "./views/Graph";
import { Timeline } from "./views/Timeline";
import { Companion } from "./views/Companion";
import { Models } from "./views/Models";
import { Jobs } from "./views/Jobs";
import { Endpoints } from "./views/Endpoints";
import { Assets } from "./views/Assets";

/**
 * Placeholder for the four mock-only views when the demo projection is off
 * (G-P3-1): no fabricated rows are served — the view says why it is hidden
 * and how to opt into the demo explicitly.
 */
function HiddenUntilLive({ view }: { view: string }) {
  return (
    <Shell title={view}>
      <div style={{ padding: "2rem", maxWidth: "40rem" }}>
        <h3 style={{ marginTop: 0 }}>{view} is hidden until it renders live data</h3>
        <p>
          This view still renders from hard-coded rows. The read-only honesty
          contract (FR-2 — ./README.md, since 2026-10-19) forbids serving mock data as if it
          were live, so it is unavailable by default.
        </p>
        <p>
          To inspect the mock projection for a demo, restart the console with{" "}
          <code>VITE_R4_WEB_DEMO=1</code> set in the environment.
        </p>
      </div>
    </Shell>
  );
}

function App() {
  const { status } = useAuth();

  if (status === "loading") {
    return <Shell title="R4 Console">Loading...</Shell>;
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={DEMO_MODE ? <Overview /> : <HiddenUntilLive view="Overview" />} />
        <Route path="/report" element={DEMO_MODE ? <Report /> : <HiddenUntilLive view="Report" />} />
        <Route path="/artifacts" element={DEMO_MODE ? <Artifacts /> : <HiddenUntilLive view="Artifacts" />} />
        <Route path="/graph" element={<Graph />} />
        <Route path="/models" element={<Models />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/endpoints" element={<Endpoints />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/timeline" element={DEMO_MODE ? <Timeline /> : <HiddenUntilLive view="Timeline" />} />
        <Route path="/companion" element={<Companion />} />
      </Routes>
    </Layout>
  );
}

export { App };
