import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { usePolling } from "../lib/polling";
import type { EnquiryDetail, EventList } from "../lib/types";

export default function EnquiryDetailPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<EnquiryDetail | null>(null);
  const [events, setEvents] = useState<EventList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [retryReason, setRetryReason] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const [d, ev] = await Promise.all([
        api<EnquiryDetail>(`/api/v1/enquiries/${id}`),
        api<EventList>(`/api/v1/enquiries/${id}/events?limit=25`),
      ]);
      setDetail(d); setEvents(ev); setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  usePolling(load, 10000);

  async function resolveReview(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    try {
      const out = await api<EnquiryDetail>(`/api/v1/enquiries/${id}/review/resolve`, {
        method: "POST",
        body: JSON.stringify({ expected_version: detail.version, resolution_note: note }),
      });
      setDetail(out); setNote("");
    } catch (err) { setError(errorMessage(err)); }
  }

  async function retryJob(jobId: string, state: string, updatedAt: string) {
    const reason = retryReason[jobId] ?? "";
    try {
      await api(`/api/v1/jobs/${jobId}/retry`, {
        method: "POST",
        body: JSON.stringify({ expected_state: state, expected_updated_at: updatedAt, reason }),
      });
      await load();
    } catch (err) { setError(errorMessage(err)); }
  }

  if (error && !detail) return <main className="card"><h1>Enquiry</h1><div role="alert" className="error">{error}</div></main>;
  if (!detail) return <main className="card"><h1>Enquiry</h1><p>Loading enquiry...</p></main>;

  return (
    <main className="card">
      <h1>Enquiry {detail.display_no}</h1>
      <p className="badge">Synthetic demo data</p>
      {error && <div role="alert" className="error">{error}</div>}
      <dl className="grid">
        <dt>Status</dt><dd>{detail.processing_summary} (attention: {detail.attention})</dd>
        <dt>Service</dt><dd>{detail.service_type}</dd>
        <dt>Received</dt><dd>{new Date(detail.received_at).toLocaleString()}</dd>
        <dt>Contact</dt>
        <dd>{detail.contact.first_name}{detail.contact.last_name ? ` ${detail.contact.last_name}` : ""} - {detail.contact.email}
          {detail.contact.phone_e164 ? ` - ${detail.contact.phone_e164}` : ""} (version {detail.contact.version})</dd>
        <dt>Owner</dt><dd>{detail.owner ? detail.owner.display_name : "Unassigned"}</dd>
        <dt>CRM</dt><dd>{detail.crm.status}{detail.crm.last_error_code ? ` (${detail.crm.last_error_code})` : ""}</dd>
        <dt>Preferred contact</dt><dd>{detail.preferred_contact}</dd>
      </dl>
      <h2>Message</h2>
      {/* Plain text interpolation only; never HTML injection (A22). */}
      <p>{detail.message}</p>

      {detail.requires_review && (
        <form onSubmit={resolveReview}>
          <h2>Resolve review</h2>
          {detail.review_reason_codes.length > 0 && <p>Reasons: {detail.review_reason_codes.join(", ")}</p>}
          <label htmlFor="review-note">Resolution note (10-500 characters)</label>
          <textarea id="review-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} required minLength={10} maxLength={500} />
          <button type="submit">Resolve review</button>
        </form>
      )}

      <h2>Related jobs</h2>
      {detail.related_jobs.length === 0 && <p>No related jobs.</p>}
      <ul>
        {detail.related_jobs.map((j) => (
          <li key={j.id}>
            {j.kind} - {j.state} (updated {new Date(j.updated_at).toLocaleString()})
            {(j.state === "dead" || j.state === "blocked") && (
              <span>
                <label htmlFor={`retry-${j.id}`}>Retry reason</label>
                <input id={`retry-${j.id}`} value={retryReason[j.id] ?? ""}
                  onChange={(e) => setRetryReason((r) => ({ ...r, [j.id]: e.target.value }))} />
                <button type="button" onClick={() => void retryJob(j.id, j.state, j.updated_at)}>Retry job</button>
              </span>
            )}
          </li>
        ))}
      </ul>

      <h2>Timeline</h2>
      {!events && <p>Loading timeline...</p>}
      {events && events.items.length === 0 && <p>No events yet.</p>}
      {events && (
        <ol>
          {events.items.map((ev) => (
            <li key={ev.id}>
              <strong>{ev.event_type}</strong> ({ev.severity}) - {new Date(ev.created_at).toLocaleString()}
              <pre>{JSON.stringify(ev.details)}</pre>
            </li>
          ))}
        </ol>
      )}
      <p><a href="#/enquiries">Back to enquiries</a></p>
    </main>
  );
}
