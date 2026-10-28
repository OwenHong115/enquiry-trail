import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { usePolling } from "../lib/polling";
import type { TaskList } from "../lib/types";

export default function TasksPage() {
  const [data, setData] = useState<TaskList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("open");
  const [reasons, setReasons] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "25" });
    if (status) params.set("status", status);
    try {
      setData(await api<TaskList>(`/api/v1/tasks?${params.toString()}`));
      setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, [status]);

  useEffect(() => { void load(); }, [load]);
  usePolling(load, 15000);

  async function act(taskId: string, version: number, action: "complete" | "cancel") {
    const reason = reasons[taskId] ?? "";
    try {
      await api(`/api/v1/tasks/${taskId}/${action}`, {
        method: "POST",
        body: JSON.stringify({ expected_version: version, reason }),
      });
      await load();
    } catch (err) { setError(errorMessage(err)); }
  }

  return (
    <main className="card">
      <h1>Follow-up tasks</h1>
      <p className="badge">Synthetic demo data</p>
      {error && <div role="alert" className="error">{error}</div>}
      <label htmlFor="task-status">Task status filter</label>
      <select id="task-status" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="open">Open</option>
        <option value="completed">Completed</option>
        <option value="cancelled">Cancelled</option>
      </select>
      {!data && !error && <p>Loading tasks...</p>}
      {data && data.items.length === 0 && <p>No tasks found.</p>}
      {data && data.items.map((t) => (
        <article key={t.id} className="task">
          <h2>{t.title}</h2>
          <p>{t.status} - due {new Date(t.due_at).toLocaleString()} - owner {t.owner.display_name}</p>
          <p>Enquiry <a href={`#/enquiries/${t.enquiry.id}`}>{t.enquiry.display_no}</a> ({t.enquiry.service_type})</p>
          {t.status === "open" && (
            <div>
              <label htmlFor={`reason-${t.id}`}>Reason (10-500 characters)</label>
              <input id={`reason-${t.id}`} value={reasons[t.id] ?? ""}
                onChange={(e) => setReasons((r) => ({ ...r, [t.id]: e.target.value }))} />
              <button type="button" onClick={() => void act(t.id, t.version, "complete")}>Complete task</button>
              <button type="button" onClick={() => void act(t.id, t.version, "cancel")}>Cancel task</button>
            </div>
          )}
        </article>
      ))}
    </main>
  );
}
