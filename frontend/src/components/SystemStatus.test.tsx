import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Readiness } from "../lib/api";
import { SystemStatus } from "./SystemStatus";

const READY: Readiness = {
  status: "ok",
  qdrant: { url: "http://localhost:6333", ready: true, error: null },
  keys_configured: { groq: true, nim: true, langfuse: true },
};

function wrap(ui: ReactNode) {
  // No retries: a test for the failure path should not wait out a backoff.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockFetch(response: Response | Error) {
  const fetchMock = vi.fn(() =>
    response instanceof Error
      ? Promise.reject(response)
      : Promise.resolve(response),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("SystemStatus", () => {
  it("reports every dependency once readiness resolves", async () => {
    mockFetch(jsonResponse(READY));

    wrap(<SystemStatus />);

    expect(await screen.findByText("http://localhost:6333")).toBeInTheDocument();
    // One row per key, plus the backend and Qdrant rows.
    expect(screen.getAllByText("configured")).toHaveLength(3);
    expect(screen.getByRole("list")).toBeInTheDocument();
  });

  it("names the unconfigured keys instead of just failing", async () => {
    mockFetch(
      jsonResponse({
        ...READY,
        status: "degraded",
        keys_configured: { groq: false, nim: true, langfuse: false },
      }),
    );

    wrap(<SystemStatus />);

    expect(await screen.findByText(/groq key/i)).toBeInTheDocument();
    expect(screen.getAllByText("missing from .env")).toHaveLength(2);
  });

  it("says Qdrant is not running rather than hiding it", async () => {
    mockFetch(
      jsonResponse({
        ...READY,
        qdrant: {
          url: "http://localhost:6333",
          ready: false,
          error: "ConnectError: connection refused",
        },
      }),
    );

    wrap(<SystemStatus />);

    expect(await screen.findByText("not running")).toBeInTheDocument();
  });

  it("shows an actionable message when the backend is unreachable", async () => {
    mockFetch(new TypeError("Failed to fetch"));

    wrap(<SystemStatus />);

    // The point of the panel: tell you what to do, not print a TypeError.
    expect(await screen.findByText(/is uvicorn running/i)).toBeInTheDocument();
  });

  it("re-checks on demand", async () => {
    const fetchMock = mockFetch(jsonResponse(READY));

    wrap(<SystemStatus />);
    await screen.findByText("http://localhost:6333");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: /re-check/i }));

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
