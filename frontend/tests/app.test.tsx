import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { parseApiError } from "../src/lib/api";
import { shouldPoll } from "../src/lib/polling";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("error envelope", () => {
  it("parses code, message and field errors", async () => {
    const err = await parseApiError(
      new Response(
        JSON.stringify({
          error: {
            code: "validation_failed",
            message: "Request validation failed",
            field_errors: [{ path: "phone", message: "invalid phone" }],
            request_id: "r1",
            retry_after_seconds: null,
          },
        }),
        { status: 422 },
      ),
    );
    expect(err.status).toBe(422);
    expect(err.code).toBe("validation_failed");
    expect(err.fieldErrors).toEqual([{ path: "phone", message: "invalid phone" }]);
  });
});

describe("polling visibility (A28)", () => {
  it("pauses while hidden and resumes when visible", () => {
    expect(shouldPoll("visible")).toBe(true);
    expect(shouldPoll("hidden")).toBe(false);
  });
});

describe("login page", () => {
  it("renders labelled fields and a synthetic-data badge", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText(/username/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByText(/synthetic demo data/i)).toBeInTheDocument();
  });

  it("shows the server error message on failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "invalid_credentials",
              message: "Invalid username or password",
              field_errors: [],
              request_id: "r2",
              retry_after_seconds: null,
            },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText(/username/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: "demo-operator" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "wrong-password-1" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid username or password"));
  });
});

describe("quote form input retention (A28)", () => {
  it("keeps typed input after a 409 conflict", async () => {
    const session = {
      operator_id: "11111111-1111-1111-1111-111111111111",
      username: "demo-operator",
      csrf_token: "csrf-1",
      expires_at: "2099-01-01T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/auth/session")) {
          return new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return new Response(
          JSON.stringify({
            error: {
              code: "idempotency_conflict",
              message: "Idempotency key conflict",
              field_errors: [],
              request_id: "r3",
              retry_after_seconds: null,
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    render(<App />);
    await waitFor(() => expect(screen.getByRole("link", { name: /new quote/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("link", { name: /new quote/i }));
    await waitFor(() => expect(screen.getByLabelText(/project details/i)).toBeInTheDocument());
    const message = screen.getByLabelText(/project details/i) as HTMLTextAreaElement;
    fireEvent.change(message, { target: { value: "Please renovate our small bathroom fully." } });
    fireEvent.change(screen.getByLabelText(/first name/i), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "ada@example.com" } });
    fireEvent.click(screen.getByLabelText(/synthetic demo/i));
    fireEvent.click(screen.getByRole("button", { name: /submit enquiry/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Idempotency key conflict"));
    expect((screen.getByLabelText(/project details/i) as HTMLTextAreaElement).value).toContain("renovate our small bathroom");
  });
});

describe("untrusted text escaping (A22)", () => {
  it("renders enquiry text as text, never as HTML", async () => {
    const session = {
      operator_id: "11111111-1111-1111-1111-111111111111",
      username: "demo-operator",
      csrf_token: "csrf-1",
      expires_at: "2099-01-01T00:00:00Z",
    };
    const detail = {
      id: "22222222-2222-2222-2222-222222222222",
      display_no: "ENQ-000001",
      contact: { id: "33333333-3333-3333-3333-333333333333", email: "a@b.co", first_name: "<img src=x onerror=alert(1)>", last_name: null, phone_e164: null, version: 1 },
      service_type: "bathroom",
      postcode: null,
      owner: null,
      processing_summary: "ready",
      attention: "none",
      requires_review: false,
      crm: { status: "synced", remote_id: null, remote_url: null, synced_contact_version: 1, next_attempt_at: null, last_error_code: null },
      received_at: "2026-01-01T00:00:00Z",
      version: 1,
      message: "<script>alert(1)</script> hello bathroom project",
      preferred_contact: "email",
      scenario_run_id: null,
      routing_reason: null,
      rules_hash: "abc",
      review_reason_codes: [],
      review_resolution_note: null,
      task: null,
      enrichment: null,
      draft: null,
      notification: { status: "disabled", notification_id: null, last_error_code: null },
      local_prepared_at: null,
      required_contact_version: 1,
      related_jobs: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url);
        if (u.includes("/auth/session")) return new Response(JSON.stringify(session), { status: 200 });
        if (u.includes("/events")) return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
        if (u.includes("/enquiries/")) return new Response(JSON.stringify(detail), { status: 200 });
        return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      }),
    );
    window.location.hash = `#/enquiries/${detail.id}`;
    render(<App />);
    await waitFor(() => expect(screen.getByText(/hello bathroom project/)).toBeInTheDocument());
    expect(document.querySelector("script[data-xss]")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });
});
