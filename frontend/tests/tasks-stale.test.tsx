import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TasksPage from "../src/pages/TasksPage";
import { api } from "../src/lib/api";
import type { TaskList, TaskListItem } from "../src/lib/types";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, api: vi.fn() };
});

const mockApi = vi.mocked(api);

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function taskItem(id: string, enquiryId: string, displayNo: string, status: string, title: string): TaskListItem {
  return {
    id,
    kind: "follow_up",
    title,
    status,
    due_at: "2026-01-02T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    completed_at: status === "completed" ? "2026-01-02T00:00:00Z" : null,
    cancelled_at: null,
    version: 1,
    owner: { id: "o1", code: "alice", display_name: "Alice" },
    enquiry: {
      id: enquiryId,
      display_no: displayNo,
      service_type: "bathroom",
      contact: { id: "c1", email: "a@b.co", first_name: "Ada", last_name: null, phone_e164: null },
    },
  };
}

const openItem = taskItem("t-open", "e-open", "ENQ-000101", "open", "Open task title");
const completedItem = taskItem("t-done", "e-done", "ENQ-000102", "completed", "Completed task title");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TasksPage stale load guard", () => {
  it("keeps the newer completed list when the slower open response resolves last", async () => {
    const openDeferred = deferred<TaskList>();
    const completedDeferred = deferred<TaskList>();

    mockApi.mockImplementation((path: string) => {
      if (path.includes("status=completed")) return completedDeferred.promise;
      return openDeferred.promise;
    });

    render(<TasksPage />);

    // Initial load is for the default Open filter and stays pending.
    expect(mockApi).toHaveBeenCalledTimes(1);
    expect(String(mockApi.mock.calls[0][0])).toContain("status=open");

    // Operator switches to Completed before the open response returns.
    fireEvent.change(screen.getByLabelText(/task status filter/i), { target: { value: "completed" } });
    expect(mockApi).toHaveBeenCalledTimes(2);
    expect(String(mockApi.mock.calls[1][0])).toContain("status=completed");

    // The newer completed response resolves first.
    await act(async () => {
      completedDeferred.resolve({ items: [completedItem], next_cursor: null });
    });
    expect(screen.getByTestId("task-row-e-done")).toBeInTheDocument();
    expect(screen.getByText("Completed task title")).toBeInTheDocument();

    // The slower stale open response resolves last and must not overwrite it.
    await act(async () => {
      openDeferred.resolve({ items: [openItem], next_cursor: null });
    });
    expect(screen.getByTestId("task-row-e-done")).toBeInTheDocument();
    expect(screen.getByText("Completed task title")).toBeInTheDocument();
    expect(screen.queryByTestId("task-row-e-open")).not.toBeInTheDocument();
    expect(screen.queryByText("Open task title")).not.toBeInTheDocument();
  });
});
