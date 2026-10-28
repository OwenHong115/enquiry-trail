import { render, screen, waitFor } from "@testing-library/react";
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
    await waitFor(() => expect(screen.getByText("demo-crash")).toBeTruthy());
    expect(screen.getByText(/inconclusive/i)).toBeTruthy();
  });
});
