export interface FieldError { path: string; message: string }

export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: FieldError[];
  constructor(status: number, code: string, message: string, fieldErrors: FieldError[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

export async function parseApiError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `Request failed (${res.status})`;
  let fieldErrors: FieldError[] = [];
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string; field_errors?: FieldError[] };
    };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      fieldErrors = body.error.field_errors ?? [];
    }
  } catch {
    // Keep the generic message for non-JSON error bodies.
  }
  return new ApiError(res.status, code, message, fieldErrors);
}

let csrfToken: string | null = null;
let onUnauthenticated: (() => void) | null = null;
export function setCsrfToken(token: string | null): void { csrfToken = token; }
export function setOnUnauthenticated(fn: (() => void) | null): void { onUnauthenticated = fn; }

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (res.status === 401) {
    onUnauthenticated?.();
    throw await parseApiError(res.clone());
  }
  if (!res.ok) throw await parseApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function fieldError(err: unknown, path: string): string | undefined {
  if (err instanceof ApiError) return err.fieldErrors.find((f) => f.path === path)?.message;
  return undefined;
}
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong. Please try again.";
}
