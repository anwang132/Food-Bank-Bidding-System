// Fetch wrapper: same-origin cookies, CSRF header on unsafe methods, and errors in
// the backend's {error: {code, message, details, request_id}} shape.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
    readonly requestId?: string,
  ) {
    super(message);
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

let csrfToken: string | null = null;
const sessionListeners = new Set<() => void>();

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Notified when any request returns 401, i.e. the session expired or was revoked. */
export function onSessionExpired(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

interface Options {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Sent as-is (e.g. a File) instead of JSON. */
  rawBody?: Blob;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.rawBody) headers["Content-Type"] = opts.rawBody.type || "application/octet-stream";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: opts.rawBody ?? (opts.body === undefined ? undefined : JSON.stringify(opts.body)),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "NETWORK", "Could not reach the server. Check your connection.");
  }
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const e = (data as { error?: Record<string, unknown> } | null)?.error ?? {};
    const err = new ApiError(
      response.status,
      String(e.code ?? "HTTP_" + response.status),
      String(e.message ?? response.statusText ?? "Request failed"),
      e.details,
      (e.request_id as string | undefined) ?? response.headers.get("x-request-id") ?? undefined,
    );
    // /me reports "signed out" itself; login failures are not session expiry.
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      sessionListeners.forEach((fn) => fn());
    }
    throw err;
  }
  return data as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const ref = err.requestId ? ` (reference ${err.requestId.slice(0, 8)})` : "";
    if (err.code === "VALIDATION_FAILED" && Array.isArray(err.details)) {
      const fields = (err.details as { field: string; message: string }[])
        .map((d) => `${d.field}: ${d.message}`)
        .join("; ");
      return `${err.message}: ${fields}`;
    }
    return err.status >= 500 ? `${err.message}${ref}` : err.message;
  }
  return "Something went wrong.";
}
