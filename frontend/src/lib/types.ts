// API response/request types are generated from the FastAPI
// OpenAPI schema (portfolio D07): see src/lib/api-types.ts and
// `npm run generate:api`. Do not hand-edit api-types.ts, and do
// not redeclare API shapes here; alias the generated components
// under the names the UI already uses.
//
// Form state and view-only types that do not exist in the API
// stay hand-written below.
import type { components } from "./api-types";

type Schemas = components["schemas"];

export type SessionInfo = Schemas["SessionOut"];
export type IntakeReceipt = Schemas["IntakeReceiptOut"];
export type Owner = Schemas["OwnerOut"];
export type Contact = Schemas["ContactOut"];
export type Crm = Schemas["CrmOut"];
export type TaskRef = Schemas["TaskOut"];
export type RelatedJob = Schemas["RelatedJobOut"];
export type EnquiryDetail = Schemas["EnquiryDetailOut"];
export type EnquiryListItem = Schemas["EnquiryListItemOut"];
export type EnquiryList = Schemas["EnquiryListOut"];
export type EventItem = Schemas["EventItemOut"];
export type EventList = Schemas["EventListOut"];
export type TaskListItem = Schemas["TaskListItemOut"];
export type TaskList = Schemas["TaskListOut"];
export type IntegrationItem = Schemas["IntegrationItemOut"];
export type WorkerHealth = Schemas["WorkerHealthOut"];
export type Draft = Schemas["DraftOut"];
export type JobOut = Schemas["JobOut"];
export type ScenarioRun = Schemas["ScenarioRunOut"];
export type ScenarioRunSummary = Schemas["ScenarioRunSummaryOut"];
export type ScenarioList = Schemas["ScenarioListOut"];

// Form state for the public quote form: a view-only shape with no
// API component of its own (the submit payload is IntakeDataIn).
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
