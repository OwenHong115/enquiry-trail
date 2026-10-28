import { useCallback, useEffect, useState } from "react";
import { api, setCsrfToken, setOnUnauthenticated } from "./lib/api";
import type { SessionInfo } from "./lib/types";
import LoginPage from "./pages/LoginPage";
import QuotePage from "./pages/QuotePage";
import EnquiriesPage from "./pages/EnquiriesPage";
import EnquiryDetailPage from "./pages/EnquiryDetailPage";
import TasksPage from "./pages/TasksPage";
import IntegrationsPage from "./pages/IntegrationsPage";

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || "#/enquiries");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/enquiries");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export default function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checked, setChecked] = useState(false);
  const route = useHashRoute();

  const applySession = useCallback((s: SessionInfo | null) => {
    setSession(s);
    setCsrfToken(s?.csrf_token ?? null);
  }, []);

  useEffect(() => {
    setOnUnauthenticated(() => applySession(null));
    api<SessionInfo>("/api/v1/auth/session")
      .then(applySession)
      .catch(() => applySession(null))
      .finally(() => setChecked(true));
    return () => setOnUnauthenticated(null);
  }, [applySession]);

  async function logout() {
    try { await api("/api/v1/auth/logout", { method: "POST" }); } catch { /* session already gone */ }
    applySession(null);
    window.location.hash = "#/login";
  }

  if (!checked) return <main className="card narrow"><p>Loading...</p></main>;
  if (!session) {
    return <LoginPage onLogin={(s) => { applySession(s); window.location.hash = "#/enquiries"; }} />;
  }

  const detailMatch = route.match(/^#\/enquiries\/([^/]+)$/);
  return (
    <div>
      <header className="topbar">
        <strong>EnquiryTrail</strong>
        <span className="badge">Synthetic demo data</span>
        <nav aria-label="Primary">
          <a href="#/quote">New quote</a>
          <a href="#/enquiries">Enquiries</a>
          <a href="#/tasks">Tasks</a>
          <a href="#/integrations">Integrations</a>
        </nav>
        <span>Signed in as {session.username}</span>
        <button type="button" onClick={() => void logout()}>Sign out</button>
      </header>
      {detailMatch ? <EnquiryDetailPage id={detailMatch[1]} />
        : route.startsWith("#/tasks") ? <TasksPage />
        : route.startsWith("#/integrations") ? <IntegrationsPage />
        : route.startsWith("#/quote") ? <QuotePage />
        : <EnquiriesPage />}
    </div>
  );
}
