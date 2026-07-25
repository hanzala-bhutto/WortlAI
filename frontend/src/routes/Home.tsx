import { SystemStatus } from "../components/SystemStatus";

const MODES = [
  { name: "Talk", note: "Voice conversation loop - Phase 1" },
  { name: "Review", note: "Chunk-based FSRS deck - Phase 2" },
  { name: "Dashboard", note: "Immersion hours - Phase 3" },
];

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
          {MODES.map((mode) => (
            <li
              key={mode.name}
              className="flex items-baseline gap-3 rounded-lg border border-dashed border-black/10 px-4 py-3 dark:border-white/15"
            >
              <span className="font-medium">{mode.name}</span>
              <span className="ml-auto text-xs opacity-60">{mode.note}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
