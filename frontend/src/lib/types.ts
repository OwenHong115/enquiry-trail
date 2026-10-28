// Hand-aligned to the backend OpenAPI/Pydantic shapes (generation impractical
// in this sandbox); field names mirror backend/app/api/*.py exactly.
export interface SessionInfo {
  operator_id: string;
  username: string;
  csrf_token: string;
  expires_at: string;
}
export interface IntakeForm {
  first_name: string;
  last_name: string | null;
  email: string;
  phone: string | null;
  postcode: string | null;
  service_type: "bathroom" | "kitchen" | "general" | "unsure";
  message: string;
  preferred_contact: "email" | "phone";
  acknowledge_demo: true;
}
export interface IntakeReceipt {
  receipt_id: string;
  enquiry_id: string;
  display_no: string;
  accepted: true;
  replayed: boolean;
  deduplication: "new" | "content_duplicate";
  status_url: string;
}
export interface Owner { id: string; code: string; display_name: string }
export interface Contact {
  id: string; email: string; first_name: string; last_name: string | null;
  phone_e164: string | null; version: number;
}
export interface Crm {
  status: string; remote_id: string | null; remote_url: string | null;
  synced_contact_version: number; next_attempt_at: string | null; last_error_code: string | null;
}
export interface TaskRef {
  id: string; enquiry_id: string; kind: string; title: string; owner: Owner;
  status: string; due_at: string; completed_at: string | null;
  cancelled_at: string | null; version: number;
}
export interface RelatedJob { id: string; kind: string; state: string; updated_at: string }
export interface EnquiryDetail {
  id: string; display_no: string; contact: Contact; service_type: string;
  postcode: string | null; owner: Owner | null; processing_summary: string;
  attention: string; requires_review: boolean; crm: Crm; received_at: string;
  version: number; message: string; preferred_contact: string;
  scenario_run_id: string | null; routing_reason: string | null; rules_hash: string;
  review_reason_codes: string[]; review_resolution_note: string | null;
  task: TaskRef | null; enrichment: Record<string, unknown> | null;
  draft: Draft | null;
  notification: { status: string; notification_id: string | null; last_error_code: string | null };
  local_prepared_at: string | null; required_contact_version: number;
  related_jobs: RelatedJob[];
}
export interface EnquiryListItem {
  id: string; display_no: string; received_at: string; service_type: string;
  processing_summary: string; attention: string; crm_status: string;
  next_attempt_at: string | null; owner: Owner | null; contact: Contact;
}
export interface EnquiryList { items: EnquiryListItem[]; next_cursor: string | null }
export interface EventItem {
  id: string; event_type: string; severity: string; actor_type: string;
  actor_id: string | null; details: Record<string, unknown>; created_at: string;
}
export interface EventList { items: EventItem[]; next_cursor: string | null }
export interface TaskListItem {
  id: string; kind: string; title: string; status: string; due_at: string;
  created_at: string; completed_at: string | null; cancelled_at: string | null;
  version: number; owner: Owner;
  enquiry: { id: string; display_no: string; service_type: string;
    contact: { id: string; email: string; first_name: string; last_name: string | null; phone_e164: string | null } };
}
export interface TaskList { items: TaskListItem[]; next_cursor: string | null }
export interface IntegrationItem {
  provider: string; status: string; incident_id: string | null;
  last_success_at: string | null; last_error_code: string | null;
  blocked_at: string | null; updated_at: string;
}
export interface WorkerHealth {
  workers: { worker_id: string; started_at: string; last_seen_at: string; active_job_count: number; status: "alive" | "stale" }[];
  stale_after_seconds: number;
}
export interface Draft {
  enquiry_id: string; subject: string; body: string;
  draft_version: number; updated_by: string | null; updated_at: string | null;
}
export interface JobOut {
  id: string; kind: string; state: string; cycle_no: number; attempt_count: number;
  cycle_attempt_count: number; next_attempt_at: string | null; updated_at: string;
}
