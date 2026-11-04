import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { errorMessage } from "../lib/api";
import type { ScenarioList, ScenarioRun, ScenarioRunSummary } from "../lib/types";

const STANDARD = ["demo-happy", "demo-crash", "demo-provider-503", "demo-director-unavailable"];

const FAULT_FOR_SCENARIO: Record<string, { provider: string; mode: string }> = {
  "demo-happy": { provider: "ai", mode: "invalid_ai_response" },
  "demo-crash": { provider: "hubspot", mode: "crash_after_remote_write" },
  "demo-provider-503": { provider: "hubspot", mode: "http_503_before_write" },
  "demo-director-unavailable": { provider: "hubspot", mode: "http_503_before_write" },
};

export default function DemoPage() {
  const [runs, setRuns] = useState<ScenarioRunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api<ScenarioList>("/api/v1/scenarios");
      setRuns(data.items); setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount fetches remote data; setState happens after the await, restructuring would change behaviour
  useEffect(() => { void load(); }, [load]);

  async function arm(scenarioId: string) {
    setBusy(true);
    try {
      const run = await api<ScenarioRun>("/api/v1/scenarios", { method: "POST", body: JSON.stringify({ scenario_id: scenarioId }) });
      const fault = FAULT_FOR_SCENARIO[scenarioId];
      if (fault) {
        await api(`/api/v1/scenarios/${run.id}/faults`, {
          method: "POST",
          body: JSON.stringify({ provider: fault.provider, mode: fault.mode, uses: 1, expires_in_seconds: 600 }),
        });
      }
      await load();
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  return (
    <main className="card">
      <h1>Failure demonstration</h1>
      <p>Arm a standard scenario, then watch its triggered or inconclusive state here.</p>
      {error ? <p role="alert">{error}</p> : null}
      <div>
        {STANDARD.map((id) => (
          <button key={id} type="button" disabled={busy} onClick={() => void arm(id)}>Arm {id}</button>
        ))}
      </div>
      {runs.length === 0 ? <p>No scenario runs yet.</p> : (
        <ul>
          {runs.map((run) => (
            <li key={run.id}>
              <strong>{run.scenario_id}</strong>{" "}
              <span>{run.status}</span>{" "}
              {run.fault_triggered ? <span>fault triggered</span> : null}{" "}
              {run.status === "inconclusive" ? <span>inconclusive: director unavailable, business work continued</span> : null}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
