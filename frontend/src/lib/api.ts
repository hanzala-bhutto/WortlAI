/**
 * Client for the WortlAI backend.
 *
 * Requests are same-origin: Vite proxies /api, /health, /readyz and /ws to
 * uvicorn in development, and FastAPI serves this app in production. So there is
 * no base URL to configure and no CORS in either environment.
 */

export type KeysConfigured = {
  groq: boolean;
  nim: boolean;
  langfuse: boolean;
};

export type Health = {
  status: "ok";
  service: string;
  keys_configured: KeysConfigured;
};

export type Readiness = {
  status: "ok" | "degraded";
  qdrant: { url: string; ready: boolean; error: string | null };
  keys_configured: KeysConfigured;
};

export type ScenarioSummary = {
  id: string;
  title: string;
  level: string;
  redemittel: string[];
};

export type ErrorEntry = {
  id: number;
  error_type: string;
  severity: string;
  utterance: string;
  correction: string;
  explanation: string | null;
  created_at: string;
};

export type SessionDebrief = {
  id: number;
  scenario: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  errors: ErrorEntry[];
};

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    // A stopped backend is a normal state while developing, so it becomes a
    // message you can act on rather than an opaque TypeError.
    throw new ApiError("Cannot reach the backend - is uvicorn running?");
  }

  if (!response.ok) {
    throw new ApiError(
      `${init?.method ?? "GET"} ${path} failed: ${response.status}`,
      response.status,
    );
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  readiness: () => request<Readiness>("/readyz"),
  scenarios: () => request<ScenarioSummary[]>("/api/v1/scenarios"),
  sessionDebrief: (id: number) =>
    request<SessionDebrief>(`/api/v1/sessions/${id}`),
};
