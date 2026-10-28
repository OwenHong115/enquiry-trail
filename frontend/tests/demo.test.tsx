import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DemoPage from "../src/pages/DemoPage";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Demo page", () => {
  it("lists scenario runs and shows inconclusive state", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).includes("/api/v1/scenarios")) {
        return new Response(JSON.stringify({ items: [
          { id: "r1", scenario_id: "demo-crash", status: "inconclusive", fault_triggered: true, assertion_results: [], created_at: "2026-10-21T00:00:00Z", finished_at: null },
        ] }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));
    render(<DemoPage />);
    await waitFor(() => expect(screen.getAllByText("demo-crash").length).toBeGreaterThan(0));
    expect(screen.getAllByText(/inconclusive/i).length).toBeGreaterThan(0);
  });

  it("Arm creates a run and arms a fault on it", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push(`${method} ${String(url)}`);
      if (method === "POST" && String(url).endsWith("/api/v1/scenarios")) {
        return new Response(JSON.stringify({ id: "run-new", scenario_id: "demo-provider-503", status: "pending", fault_triggered: false, assertion_results: [], created_at: "2026-10-21T00:00:00Z", finished_at: null, faults: [] }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      if (method === "POST" && String(url).includes("/faults")) {
        return new Response(JSON.stringify({ id: "f1", scenario_run_id: "run-new", provider: "hubspot", mode: "http_503_before_write", status: "armed", remaining_uses: 1, expires_at: "2026-10-21T01:00:00Z", created_at: "2026-10-21T00:00:00Z" }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<DemoPage />);
    fireEvent.click(screen.getByRole("button", { name: "Arm demo-provider-503" }));
    await waitFor(() => expect(calls.some((c) => c.includes("/api/v1/scenarios/run-new/faults"))).toBe(true));
  });
});
