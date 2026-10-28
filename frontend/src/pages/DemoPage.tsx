import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { errorMessage } from "../lib/api";

interface ScenarioRun {
  id: string; scenario_id: string; status: string; fault_triggered: boolean;
  assertion_results: unknown[]; created_at: string; finished_at: string | null;
}

const STANDARD = ["demo-happy", "demo-crash", "demo-provider-503", "demo-director-unavailable"];

export default function DemoPage() {
  const [runs, setRuns] = useState<ScenarioRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api<{ items: ScenarioRun[] }>("/api/v1/scenarios");
      setRuns(data.items); setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function arm(scenarioId: string) {
    setBusy(true);
    try {
      await api("/api/v1/scenarios", { method: "POST", body: JSON.stringify({ scenario_id: scenarioId }) });
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
