import { FormEvent, useRef, useState } from "react";
import { ApiError, api, errorMessage, fieldError } from "../lib/api";
import type { IntakeReceipt } from "../lib/types";

const EMPTY = {
  first_name: "", last_name: "", email: "", phone: "", postcode: "",
  service_type: "bathroom" as const, message: "", preferred_contact: "email" as const,
  acknowledge_demo: false,
};

export default function QuotePage() {
  // Form state is component state only: a 409 never clears it (A28).
  const [form, setForm] = useState({ ...EMPTY });
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [receipt, setReceipt] = useState<IntakeReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  // One idempotency key per draft; kept across a 409 so a retry replays safely.
  const keyRef = useRef<string | null>(null);
  const set = (k: string, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    if (!keyRef.current) {
      keyRef.current = typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : "11111111-1111-4111-8111-111111111111";
    }
    try {
      const out = await api<IntakeReceipt>("/api/v1/intake/form", {
        method: "POST",
        headers: { "Idempotency-Key": keyRef.current },
        body: JSON.stringify({
          first_name: form.first_name,
          last_name: form.last_name || null,
          email: form.email,
          phone: form.phone || null,
          postcode: form.postcode || null,
          service_type: form.service_type,
          message: form.message,
          preferred_contact: form.preferred_contact,
          acknowledge_demo: form.acknowledge_demo ? true : undefined,
        }),
      });
      setReceipt(out);
      keyRef.current = null;
    } catch (err) {
      // Deliberately do not reset `form`: input is retained after 409/422.
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  const fe = (p: string) => (error ? fieldError(error, p) : undefined);
  return (
    <main className="card">
      <h1>Request a quote</h1>
      <p className="badge">Synthetic demo data</p>
      <p>Tell us about your renovation. This demo uses synthetic data only.</p>
      {error && <div role="alert" className="error">{errorMessage(error)}</div>}
      {receipt && (
        <div role="status" className="success">
          <p>Enquiry {receipt.display_no} accepted{receipt.replayed ? " (already received)" : ""}.</p>
          <p><a href={`#/enquiries/${receipt.enquiry_id}`}>View enquiry status</a></p>
        </div>
      )}
      <form onSubmit={submit} noValidate>
        <label htmlFor="q-first">First name</label>
        <input id="q-first" value={form.first_name} onChange={(e) => set("first_name", e.target.value)} required aria-describedby={fe("first_name") ? "q-first-err" : undefined} />
        {fe("first_name") && <p id="q-first-err" className="field-error">{fe("first_name")}</p>}
        <label htmlFor="q-last">Last name (optional)</label>
        <input id="q-last" value={form.last_name} onChange={(e) => set("last_name", e.target.value)} />
        <label htmlFor="q-email">Email</label>
        <input id="q-email" type="email" value={form.email} onChange={(e) => set("email", e.target.value)} required aria-describedby={fe("email") ? "q-email-err" : undefined} />
        {fe("email") && <p id="q-email-err" className="field-error">{fe("email")}</p>}
        <label htmlFor="q-phone">Phone (optional)</label>
        <input id="q-phone" value={form.phone} onChange={(e) => set("phone", e.target.value)} aria-describedby={fe("phone") ? "q-phone-err" : undefined} />
        {fe("phone") && <p id="q-phone-err" className="field-error">{fe("phone")}</p>}
        <label htmlFor="q-postcode">Postcode (optional, 4 digits)</label>
        <input id="q-postcode" inputMode="numeric" value={form.postcode} onChange={(e) => set("postcode", e.target.value)} />
        <label htmlFor="q-service">Service type</label>
        <select id="q-service" value={form.service_type} onChange={(e) => set("service_type", e.target.value)}>
          <option value="bathroom">Bathroom</option>
          <option value="kitchen">Kitchen</option>
          <option value="general">General renovation</option>
          <option value="unsure">Not sure yet</option>
        </select>
        <label htmlFor="q-message">Project details</label>
        <textarea id="q-message" rows={6} value={form.message} onChange={(e) => set("message", e.target.value)} required aria-describedby={fe("message") ? "q-message-err" : undefined} />
        {fe("message") && <p id="q-message-err" className="field-error">{fe("message")}</p>}
        <label htmlFor="q-contact">Preferred contact</label>
        <select id="q-contact" value={form.preferred_contact} onChange={(e) => set("preferred_contact", e.target.value)}>
          <option value="email">Email</option>
          <option value="phone">Phone</option>
        </select>
        <label className="check" htmlFor="q-ack">
          <input id="q-ack" type="checkbox" checked={form.acknowledge_demo} onChange={(e) => set("acknowledge_demo", e.target.checked)} />
          I understand this is synthetic demo data
        </label>
        <button type="submit" disabled={busy || !form.acknowledge_demo}>{busy ? "Submitting..." : "Submit enquiry"}</button>
      </form>
    </main>
  );
}
