import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, fieldError } from "../lib/api";
import { usePolling } from "../lib/polling";
import type { IntegrationItem, WorkerHealth } from "../lib/types";

export default function IntegrationsPage() {
  const [items, setItems] = useState<IntegrationItem[] | null>(null);
  const [workers, setWorkers] = useState<WorkerHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<unknown>(null);
  const [failedProvider, setFailedProvider] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, health] = await Promise.all([
        api<{ items: IntegrationItem[] }>("/api/v1/integrations"),
        api<WorkerHealth>("/api/v1/health/workers"),
      ]);
      setItems(list.items); setWorkers(health); setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount fetches remote data; setState happens after the await, restructuring would change behaviour
  useEffect(() => { void load(); }, [load]);
  usePolling(load, 15000);

  async function resume(item: IntegrationItem) {
    setActionError(null);
    setFailedProvider(null);
    try {
      await api(`/api/v1/integrations/${encodeURIComponent(item.provider)}/resume`, {
        method: "POST",
        body: JSON.stringify({
          expected_status: "blocked",
          expected_updated_at: item.updated_at,
          reason: reasons[item.provider] ?? "",
        }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
      setActionError(err);
      setFailedProvider(item.provider);
    }
  }

  return (
    <main className="card">
      <h1>Integrations</h1>
      <p className="badge">Synthetic demo data</p>
      {error && <div role="alert" className="error">{error}</div>}
      {!items && !error && <p>Loading integrations...</p>}
      {items && items.length === 0 && <p>No integrations reported.</p>}
      {items && items.map((it) => {
        const reason = reasons[it.provider] ?? "";
        const fe = failedProvider === it.provider ? fieldError(actionError, "reason") : undefined;
        return (
          <article key={it.provider} className="task">
            <h2>{it.provider}</h2>
            <p>Status: {it.status}{it.last_error_code ? ` - last error: ${it.last_error_code}` : ""}</p>
            <p>Updated: {new Date(it.updated_at).toLocaleString()}</p>
            {it.status === "blocked" && (
              <div>
                <label htmlFor={`resume-${it.provider}`}>Resume reason (10-500 characters)</label>
                <input id={`resume-${it.provider}`} value={reason} required minLength={10} maxLength={500}
                  aria-describedby={fe ? `resume-${it.provider}-err` : undefined}
                  onChange={(e) => {
                    setReasons((r) => ({ ...r, [it.provider]: e.target.value }));
                    if (failedProvider === it.provider) { setActionError(null); setFailedProvider(null); }
                  }} />
                {fe && <p id={`resume-${it.provider}-err`} className="field-error">{fe}</p>}
                <button type="button" disabled={reason.length < 10} onClick={() => void resume(it)}>Resume integration</button>
              </div>
            )}
          </article>
        );
      })}
      <h2>Workers</h2>
      {!workers && <p>Loading worker health...</p>}
      {workers && workers.workers.length === 0 && <p>No workers reported.</p>}
      {workers && (
        <ul>
          {workers.workers.map((w) => (
            <li key={w.worker_id}>{w.worker_id} - {w.status} - active jobs: {w.active_job_count} - last seen {new Date(w.last_seen_at).toLocaleString()}</li>
          ))}
        </ul>
      )}
    </main>
  );
}
