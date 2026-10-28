import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { parseApiError } from "../src/lib/api";
import { shouldPoll, usePolling } from "../src/lib/polling";
import TasksPage from "../src/pages/TasksPage";
import IntegrationsPage from "../src/pages/IntegrationsPage";
import EnquiryDetailPage from "../src/pages/EnquiryDetailPage";

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

  it("usePolling stops ticking while hidden and resumes when visible", () => {
    vi.useFakeTimers();
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    try {
      const fn = vi.fn();
      const { unmount } = renderHook(() => usePolling(fn, 1000));
      act(() => { vi.advanceTimersByTime(3000); });
      expect(fn).toHaveBeenCalledTimes(3);

      visibility = "hidden";
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      act(() => { vi.advanceTimersByTime(3000); });
      expect(fn).toHaveBeenCalledTimes(3);

      visibility = "visible";
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      // Becoming visible ticks immediately, then the interval resumes.
      expect(fn).toHaveBeenCalledTimes(4);
      act(() => { vi.advanceTimersByTime(2000); });
      expect(fn).toHaveBeenCalledTimes(6);
      unmount();
    } finally {
      vi.useRealTimers();
      // @ts-expect-error restore the jsdom default descriptor
      delete document.visibilityState;
    }
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
    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByText(/hello bathroom project/)).toBeInTheDocument());
    // The payload survives verbatim as text content...
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(container.textContent).toContain("<script>alert(1)</script> hello bathroom project");
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    // ...and no element is ever created from the untrusted strings.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});

const err422 = (path: string, message: string) =>
  new Response(
    JSON.stringify({
      error: {
        code: "validation_failed",
        message: "Request validation failed",
        field_errors: [{ path, message }],
        request_id: "r422",
        retry_after_seconds: null,
      },
    }),
    { status: 422, headers: { "Content-Type": "application/json" } },
  );

const taskItem = {
  id: "t1", kind: "follow_up", title: "Call the customer", status: "open",
  due_at: "2026-01-02T00:00:00Z", created_at: "2026-01-01T00:00:00Z",
  completed_at: null, cancelled_at: null, version: 3,
  owner: { id: "o1", code: "alice", display_name: "Alice" },
  enquiry: {
    id: "e1", display_no: "ENQ-000001", service_type: "bathroom",
    contact: { id: "c1", email: "a@b.co", first_name: "Ada", last_name: null, phone_e164: null },
  },
};

describe("task reason length (min 10)", () => {
  it("cannot submit a short reason and shows the 422 field error by the field", async () => {
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") !== "GET") {
        posts += 1;
        return err422("reason", "String should have at least 10 characters");
      }
      return new Response(JSON.stringify({ items: [taskItem], next_cursor: null }), { status: 200 });
    }));
    render(<TasksPage />);
    const input = await screen.findByLabelText(/reason/i);
    const complete = screen.getByRole("button", { name: /complete task/i });
    fireEvent.change(input, { target: { value: "short" } });
    expect(input).toHaveAttribute("minLength", "10");
    expect(complete).toBeDisabled();
    expect(screen.getByRole("button", { name: /cancel task/i })).toBeDisabled();
    expect(posts).toBe(0);

    fireEvent.change(input, { target: { value: "called the customer today" } });
    expect(complete).not.toBeDisabled();
    fireEvent.click(complete);
    const fieldErr = await screen.findByText(/at least 10 characters/i);
    expect(fieldErr).toHaveClass("field-error");
    expect(input.getAttribute("aria-describedby")).toBeTruthy();
    expect(document.getElementById(input.getAttribute("aria-describedby") ?? "")).toBe(fieldErr);
  });
});

describe("integration resume reason length (min 10)", () => {
  it("cannot submit a short reason and shows the 422 field error by the field", async () => {
    const blocked = {
      provider: "hubspot", status: "blocked", incident_id: null,
      last_success_at: null, last_error_code: "auth_failed",
      blocked_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") !== "GET") {
        posts += 1;
        return err422("reason", "String should have at least 10 characters");
      }
      if (String(url).includes("/health/workers")) {
        return new Response(JSON.stringify({ workers: [], stale_after_seconds: 90 }), { status: 200 });
      }
      return new Response(JSON.stringify({ items: [blocked] }), { status: 200 });
    }));
    render(<IntegrationsPage />);
    const input = await screen.findByLabelText(/resume reason/i);
    const resume = screen.getByRole("button", { name: /resume integration/i });
    fireEvent.change(input, { target: { value: "short" } });
    expect(input).toHaveAttribute("minLength", "10");
    expect(resume).toBeDisabled();
    expect(posts).toBe(0);

    fireEvent.change(input, { target: { value: "provider credentials rotated" } });
    expect(resume).not.toBeDisabled();
    fireEvent.click(resume);
    const fieldErr = await screen.findByText(/at least 10 characters/i);
    expect(fieldErr).toHaveClass("field-error");
    expect(document.getElementById(input.getAttribute("aria-describedby") ?? "")).toBe(fieldErr);
  });
});

const detailWithJob = {
  id: "e1", display_no: "ENQ-000001",
  contact: { id: "c1", email: "a@b.co", first_name: "Ada", last_name: null, phone_e164: null, version: 1 },
  service_type: "bathroom", postcode: null,
  owner: { id: "o1", code: "alice", display_name: "Alice" },
  processing_summary: "ready", attention: "none", requires_review: false,
  crm: { status: "synced", remote_id: null, remote_url: null, synced_contact_version: 1, next_attempt_at: null, last_error_code: null },
  received_at: "2026-01-01T00:00:00Z", version: 5,
  message: "Please renovate our small bathroom fully.",
  preferred_contact: "email", scenario_run_id: null, routing_reason: null,
  rules_hash: "abc", review_reason_codes: [], review_resolution_note: null,
  task: null, enrichment: null, draft: null,
  notification: { status: "disabled", notification_id: null, last_error_code: null },
  local_prepared_at: null, required_contact_version: 1,
  related_jobs: [{ id: "j1", kind: "sync_contact", state: "dead", updated_at: "2026-01-01T00:00:00Z" }],
};

const templateDraft = {
  enquiry_id: "e1",
  subject: "Following up on your bathroom enquiry",
  body: "Hi Ada,\n\nThank you for getting in touch.",
  draft_version: 0, updated_by: null, updated_at: null,
};

function stubDetailFetch(overrides: {
  onPost?: (url: string, body: string) => Response;
} = {}) {
  const calls: { url: string; method: string; body: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : "";
    if (method !== "GET") {
      calls.push({ url: u, method, body });
      if (overrides.onPost) return overrides.onPost(u, body);
      return new Response(JSON.stringify(detailWithJob), { status: 200 });
    }
    if (u.includes("/draft")) return new Response(JSON.stringify(templateDraft), { status: 200 });
    if (u.includes("/events")) return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
    return new Response(JSON.stringify(detailWithJob), { status: 200 });
  }));
  return calls;
}

describe("job retry reason length (min 10)", () => {
  it("cannot submit a short reason and shows the 422 field error by the field", async () => {
    let posts = 0;
    stubDetailFetch({
      onPost: () => {
        posts += 1;
        return err422("reason", "String should have at least 10 characters");
      },
    });
    render(<EnquiryDetailPage id="e1" />);
    const input = await screen.findByLabelText(/retry reason/i);
    const retry = screen.getByRole("button", { name: /retry job/i });
    fireEvent.change(input, { target: { value: "short" } });
    expect(input).toHaveAttribute("minLength", "10");
    expect(retry).toBeDisabled();
    expect(posts).toBe(0);

    fireEvent.change(input, { target: { value: "transient provider outage" } });
    expect(retry).not.toBeDisabled();
    fireEvent.click(retry);
    const fieldErr = await screen.findByText(/at least 10 characters/i);
    expect(fieldErr).toHaveClass("field-error");
    expect(document.getElementById(input.getAttribute("aria-describedby") ?? "")).toBe(fieldErr);
  });
});

describe("owner change and draft controls", () => {
  it("changes the owner with owner_code and expected_version", async () => {
    const updated = {
      ...detailWithJob, version: 6,
      owner: { id: "o2", code: "bob", display_name: "Bob" },
    };
    const calls = stubDetailFetch({
      onPost: (url) => url.includes("/owner/change")
        ? new Response(JSON.stringify(updated), { status: 200 })
        : new Response(JSON.stringify(detailWithJob), { status: 200 }),
    });
    render(<EnquiryDetailPage id="e1" />);
    const input = await screen.findByLabelText(/new owner code/i);
    fireEvent.change(input, { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: /change owner/i }));
    await waitFor(() => expect(screen.getByText(/Bob/)).toBeInTheDocument());
    const post = calls.find((c) => c.url.includes("/owner/change"));
    expect(post).toBeTruthy();
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ owner_code: "bob", expected_version: 5 });
  });

  it("loads the draft, saves it, and keeps the text on a 409 conflict", async () => {
    stubDetailFetch({
      onPost: (_url, _body) =>
        new Response(
          JSON.stringify({
            error: {
              code: "version_conflict", message: "Draft version conflict",
              field_errors: [], request_id: "r409", retry_after_seconds: null,
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
    });
    render(<EnquiryDetailPage id="e1" />);
    const body = (await screen.findByLabelText(/draft body/i)) as HTMLTextAreaElement;
    expect(body.value).toContain("Thank you for getting in touch");
    fireEvent.change(body, { target: { value: "My edited draft body text" } });
    fireEvent.click(screen.getByRole("button", { name: /save draft/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/draft version conflict/i));
    expect((screen.getByLabelText(/draft body/i) as HTMLTextAreaElement).value).toBe("My edited draft body text");
  });
});
