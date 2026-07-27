import { Link } from "@tanstack/react-router";

import { SystemStatus } from "../components/SystemStatus";

const MODES = [
  { name: "Talk", note: "Voice conversation loop - Phase 1", to: "/talk" },
  { name: "Review", note: "Chunk-based FSRS deck - Phase 2", to: undefined },
  { name: "Dashboard", note: "Immersion hours - Phase 3", to: undefined },
] as const;

export function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-10 px-6 py-16">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">WortlAI</h1>
        <p className="mt-2 opacity-70">
          Voice-first German fluency trainer. Speak, get corrected quietly,
          review what you actually failed at.
        </p>
      </div>

      <SystemStatus />

      <section>
        <h2 className="mb-3 font-mono text-sm tracking-wide uppercase opacity-60">
          Modes
        </h2>
        <ul className="space-y-2 text-sm">
          {MODES.map((mode) => {
            const row = (
              <>
                <span className="font-medium">{mode.name}</span>
                <span className="ml-auto text-xs opacity-60">{mode.note}</span>
              </>
            );
            return (
              <li key={mode.name}>
                {mode.to ? (
                  <Link
                    to={mode.to}
                    className="flex items-baseline gap-3 rounded-lg border border-dashed border-black/10 px-4 py-3 transition-colors hover:border-solid hover:border-black/25 dark:border-white/15 dark:hover:border-white/30"
                  >
                    {row}
                  </Link>
                ) : (
                  <div className="flex items-baseline gap-3 rounded-lg border border-dashed border-black/10 px-4 py-3 opacity-60 dark:border-white/15">
                    {row}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}
