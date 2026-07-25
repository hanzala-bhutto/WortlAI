import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";

/**
 * Backend and dependency status. Re-checkable on demand so you can start uvicorn
 * or a container and confirm it without reloading.
 */
export function SystemStatus() {
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: ["readiness"],
    queryFn: api.readiness,
  });

  return (
    <section className="rounded-xl border border-black/10 p-5 dark:border-white/15">
      <header className="mb-4 flex items-center justify-between gap-4">
        <h2 className="font-mono text-sm tracking-wide uppercase opacity-60">
          System
        </h2>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="rounded-md border border-black/15 px-3 py-1 text-sm transition hover:bg-black/5 disabled:opacity-40 dark:border-white/20 dark:hover:bg-white/10"
        >
          {isFetching ? "Checking..." : "Re-check"}
        </button>
      </header>

      {isPending ? (
        <p className="text-sm opacity-60">Checking...</p>
      ) : error ? (
        <p className="text-sm text-red-600 dark:text-red-400">{error.message}</p>
      ) : (
        <ul className="space-y-2 text-sm">
          <StatusRow label="Backend" ok detail={data.status} />
          <StatusRow
            label="Qdrant"
            ok={data.qdrant.ready}
            detail={data.qdrant.ready ? data.qdrant.url : "not running"}
          />
          {Object.entries(data.keys_configured).map(([name, present]) => (
            <StatusRow
              key={name}
              label={`${name} key`}
              ok={present}
              detail={present ? "configured" : "missing from .env"}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function StatusRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <li className="flex items-center gap-3">
      <span
        aria-hidden
        className={`size-2 shrink-0 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`}
      />
      <span className="capitalize">{label}</span>
      <span className="ml-auto font-mono text-xs opacity-60">{detail}</span>
      <span className="sr-only">{ok ? "ready" : "not ready"}</span>
    </li>
  );
}
