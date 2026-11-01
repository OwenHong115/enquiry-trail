import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, errorMessage, fieldError } from "../lib/api";
import { usePolling } from "../lib/polling";
import type { Draft, EnquiryDetail, EventList } from "../lib/types";

export default function EnquiryDetailPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<EnquiryDetail | null>(null);
  const [events, setEvents] = useState<EventList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [retryReason, setRetryReason] = useState<Record<string, string>>({});
  const [reviewError, setReviewError] = useState<unknown>(null);
  const [retryError, setRetryError] = useState<unknown>(null);
  const [failedJobId, setFailedJobId] = useState<string | null>(null);
  const [ownerCode, setOwnerCode] = useState("");
  const [ownerError, setOwnerError] = useState<unknown>(null);
  const [draftSubject, setDraftSubject] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  const [draftError, setDraftError] = useState<unknown>(null);
  const [draftSaved, setDraftSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, ev] = await Promise.all([
        api<EnquiryDetail>(`/api/v1/enquiries/${id}`),
        api<EventList>(`/api/v1/enquiries/${id}/events?limit=25`),
      ]);
      setDetail(d); setEvents(ev); setError(null);
    } catch (err) { setError(errorMessage(err)); }
  }, [id]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount fetches remote data; setState happens after the await, restructuring would change behaviour
  useEffect(() => { void load(); }, [load]);
  usePolling(load, 10000);

  // The draft is loaded once, separately from the polled detail, so polling
  // never overwrites text the operator is editing.
  useEffect(() => {
    let cancelled = false;
    api<Draft>(`/api/v1/enquiries/${id}/draft`)
      .then((d) => {
        if (cancelled) return;
        setDraftSubject(d.subject);
        setDraftBody(d.body);
        setDraftVersion(d.draft_version);
      })
      .catch((err) => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [id]);

  async function resolveReview(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    setReviewError(null);
    try {
      const out = await api<EnquiryDetail>(`/api/v1/enquiries/${id}/review/resolve`, {
        method: "POST",
        body: JSON.stringify({ expected_version: detail.version, resolution_note: note }),
      });
      setDetail(out); setNote("");
    } catch (err) {
      setError(errorMessage(err));
      setReviewError(err);
    }
  }

  async function retryJob(jobId: string, state: string, updatedAt: string) {
    const reason = retryReason[jobId] ?? "";
    setRetryError(null);
    setFailedJobId(null);
    try {
      await api(`/api/v1/jobs/${jobId}/retry`, {
        method: "POST",
        body: JSON.stringify({ expected_state: state, expected_updated_at: updatedAt, reason }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
      setRetryError(err);
      setFailedJobId(jobId);
    }
  }

  async function changeOwner() {
    if (!detail) return;
    setOwnerError(null);
    try {
      const out = await api<EnquiryDetail>(`/api/v1/enquiries/${id}/owner/change`, {
        method: "POST",
        body: JSON.stringify({ owner_code: ownerCode, expected_version: detail.version }),
      });
      setDetail(out);
      setOwnerCode("");
    } catch (err) {
      // On a 409 the typed owner code is retained for correction.
      setError(errorMessage(err));
      setOwnerError(err);
    }
  }

  async function saveDraft() {
    setDraftError(null);
    setDraftSaved(false);
    try {
      const out = await api<Draft>(`/api/v1/enquiries/${id}/draft`, {
        method: "PUT",
        body: JSON.stringify({
          subject: draftSubject,
          body: draftBody,
          expected_draft_version: draftVersion,
        }),
      });
      setDraftSubject(out.subject);
      setDraftBody(out.body);
      setDraftVersion(out.draft_version);
      setDraftSaved(true);
    } catch (err) {
      // On a 409 the edited draft text is retained (A28).
      setError(errorMessage(err));
      setDraftError(err);
    }
  }

  if (error && !detail) return <main className="card"><h1>Enquiry</h1><div role="alert" className="error">{error}</div></main>;
  if (!detail) return <main className="card"><h1>Enquiry</h1><p>Loading enquiry...</p></main>;

  const reviewFe = fieldError(reviewError, "resolution_note");
  const ownerFe = fieldError(ownerError, "owner_code");
  const draftSubjectFe = fieldError(draftError, "subject");
  const draftBodyFe = fieldError(draftError, "body");

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

      <h2>Change owner</h2>
      <label htmlFor="owner-code">New owner code</label>
      <input id="owner-code" value={ownerCode} required
        aria-describedby={ownerFe ? "owner-code-err" : undefined}
        onChange={(e) => { setOwnerCode(e.target.value); setOwnerError(null); }} />
      {ownerFe && <p id="owner-code-err" className="field-error">{ownerFe}</p>}
      <button type="button" disabled={!ownerCode.trim()} onClick={() => void changeOwner()}>Change owner</button>

      <h2>Reply draft</h2>
      {draftVersion === null && <p>Loading draft...</p>}
      {draftVersion !== null && (
        <div>
          <label htmlFor="draft-subject">Draft subject</label>
          <input id="draft-subject" value={draftSubject} required maxLength={200}
            aria-describedby={draftSubjectFe ? "draft-subject-err" : undefined}
            onChange={(e) => { setDraftSubject(e.target.value); setDraftSaved(false); }} />
          {draftSubjectFe && <p id="draft-subject-err" className="field-error">{draftSubjectFe}</p>}
          <label htmlFor="draft-body">Draft body</label>
          <textarea id="draft-body" rows={8} value={draftBody} required maxLength={3000}
            aria-describedby={draftBodyFe ? "draft-body-err" : undefined}
            onChange={(e) => { setDraftBody(e.target.value); setDraftSaved(false); }} />
          {draftBodyFe && <p id="draft-body-err" className="field-error">{draftBodyFe}</p>}
          <p>Draft version: {draftVersion}</p>
          {draftSaved && <p role="status">Draft saved.</p>}
          <button type="button" disabled={!draftSubject || !draftBody} onClick={() => void saveDraft()}>Save draft</button>
        </div>
      )}

      {detail.requires_review && (
        <form onSubmit={resolveReview}>
          <h2>Resolve review</h2>
          {detail.review_reason_codes.length > 0 && <p>Reasons: {detail.review_reason_codes.join(", ")}</p>}
          <label htmlFor="review-note">Resolution note (10-500 characters)</label>
          <textarea id="review-note" rows={3} value={note} onChange={(e) => { setNote(e.target.value); setReviewError(null); }} required minLength={10} maxLength={500}
            aria-describedby={reviewFe ? "review-note-err" : undefined} />
          {reviewFe && <p id="review-note-err" className="field-error">{reviewFe}</p>}
          <button type="submit" disabled={note.length < 10}>Resolve review</button>
        </form>
      )}

      <h2>Related jobs</h2>
      {detail.related_jobs.length === 0 && <p>No related jobs.</p>}
      <ul>
        {detail.related_jobs.map((j) => {
          const reason = retryReason[j.id] ?? "";
          const fe = failedJobId === j.id ? fieldError(retryError, "reason") : undefined;
          return (
            <li key={j.id}>
              {j.kind} - {j.state} (updated {new Date(j.updated_at).toLocaleString()})
              {(j.state === "dead" || j.state === "blocked") && (
                <span>
                  <label htmlFor={`retry-${j.id}`}>Retry reason (10-500 characters)</label>
                  <input id={`retry-${j.id}`} value={reason} required minLength={10} maxLength={500}
                    aria-describedby={fe ? `retry-${j.id}-err` : undefined}
                    onChange={(e) => {
                      setRetryReason((r) => ({ ...r, [j.id]: e.target.value }));
                      if (failedJobId === j.id) { setRetryError(null); setFailedJobId(null); }
                    }} />
                  {fe && <p id={`retry-${j.id}-err`} className="field-error">{fe}</p>}
                  <button type="button" disabled={reason.length < 10} onClick={() => void retryJob(j.id, j.state, j.updated_at)}>Retry job</button>
                </span>
              )}
            </li>
          );
        })}
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
