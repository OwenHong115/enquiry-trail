import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { usePolling } from "../lib/polling";
import type { EnquiryList } from "../lib/types";

export default function EnquiriesPage() {
  const [data, setData] = useState<EnquiryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [attention, setAttention] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "25" });
    if (q) params.set("q", q);
    if (attention) params.set("attention", attention);
    try {
      setData(await api<EnquiryList>(`/api/v1/enquiries?${params.toString()}`));
      setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, [q, attention]);

  useEffect(() => { void load(); }, [load]);
  usePolling(load, 10000);

  return (
    <main className="card">
      <h1>Enquiries</h1>
      <p className="badge">Synthetic demo data</p>
      {error && <div role="alert" className="error">{error}</div>}
      <form onSubmit={(e) => { e.preventDefault(); void load(); }} role="search">
        <label htmlFor="eq-search">Search enquiries</label>
        <input id="eq-search" value={q} onChange={(e) => setQ(e.target.value)} />
        <label htmlFor="eq-attention">Attention filter</label>
        <select id="eq-attention" value={attention} onChange={(e) => setAttention(e.target.value)}>
          <option value="">Any</option>
          <option value="none">None</option>
          <option value="review_required">Review required</option>
          <option value="retry_scheduled">Retry scheduled</option>
          <option value="integration_blocked">Integration blocked</option>
          <option value="intervention_required">Intervention required</option>
        </select>
        <button type="submit">Search</button>
      </form>
      {!data && !error && <p>Loading enquiries...</p>}
      {data && data.items.length === 0 && <p>No enquiries found.</p>}
      {data && data.items.length > 0 && (
        <table>
          <caption>Enquiries</caption>
          <thead><tr><th>Reference</th><th>Received</th><th>Service</th><th>Status</th><th>Attention</th><th>CRM</th><th>Contact</th></tr></thead>
          <tbody>
            {data.items.map((it) => (
              <tr key={it.id}>
                <td><a href={`#/enquiries/${it.id}`}>{it.display_no}</a></td>
                <td>{new Date(it.received_at).toLocaleString()}</td>
                <td>{it.service_type}</td>
                <td>{it.processing_summary}</td>
                <td>{it.attention}</td>
                <td>{it.crm_status}</td>
                {/* React text rendering escapes untrusted values (A22). */}
                <td>{it.contact.first_name} ({it.contact.email})</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
