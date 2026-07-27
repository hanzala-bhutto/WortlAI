import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { MicButton } from "../components/talk/MicButton";
import { type ScenarioSummary, api } from "../lib/api";
import { realDeps, voiceUrl } from "../session/media";
import type { Phase, Turn } from "../session/reducer";
import { useVoiceSession } from "../session/useVoiceSession";

/** German chrome; the conversation itself is German, glosses land in PR2. */
const MIC_LABEL: Record<Phase, string> = {
  listening: "Halten zum Sprechen",
  recording: "Aufnahme... loslassen zum Senden",
  thinking: "Einen Moment...",
  replying: "Tutor spricht...",
};

export function Talk() {
  const scenarios = useQuery({
    queryKey: ["scenarios"],
    queryFn: api.scenarios,
    staleTime: Infinity,
  });
  // PR1 auto-starts the first scenario; the picker is PR2.
  const scenario = scenarios.data?.[0];

  const session = useVoiceSession({
    scenarioId: scenario?.id ?? "",
    level: scenario?.level,
    socketUrl: voiceUrl(),
    realDeps,
  });
  const { state, connect, hold, release, end } = session;

  // Open the socket once the scenario is known.
  useEffect(() => {
    if (scenario) connect();
  }, [scenario, connect]);

  // A turn ends on pointerup anywhere, and Spacebar mirrors hold-to-talk, so
  // the mic works even if the pointer drags off the button mid-utterance.
  useEffect(() => {
    const up = () => void release();
    const keyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && state.phase === "listening") {
        e.preventDefault();
        void hold();
      }
    };
    const keyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") void release();
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", keyDown);
    window.addEventListener("keyup", keyUp);
    return () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", keyDown);
      window.removeEventListener("keyup", keyUp);
    };
  }, [hold, release, state.phase]);

  if (scenarios.isError) {
    return (
      <Centered>
        <p className="font-medium text-head">Szenarien konnten nicht geladen werden.</p>
        <p className="text-sm text-soft">Laeuft das Backend? (uvicorn auf :8000)</p>
      </Centered>
    );
  }
  if (!scenario) {
    return (
      <Centered>
        <p className="text-soft">Szenario wird geladen...</p>
      </Centered>
    );
  }

  return (
    <main className="surface-warm mx-auto flex min-h-full w-full max-w-2xl flex-1 flex-col px-4 py-4 text-ink">
      <Rail scenario={scenario} connected={state.status === "ready"} />

      <div className="flex flex-1 flex-col justify-end gap-4 overflow-y-auto py-4">
        {state.turns.length === 0 && state.status === "ready" && (
          <p className="mx-auto rounded-2xl bg-sunk px-5 py-3 text-center text-sm font-medium text-soft shadow-clay-in">
            Halte die Taste und sprich. <span className="text-head">Hold the mic, speak German.</span>
          </p>
        )}
        {state.turns.map((turn) => (
          <Bubble key={turn.id} turn={turn} />
        ))}
      </div>

      {state.error && (
        <p
          role="status"
          className="mx-auto mb-3 rounded-xl bg-surface px-4 py-2 text-center text-sm font-semibold text-again shadow-clay-sm"
        >
          {errorLabel(state.error.stage)}
        </p>
      )}

      <div className="flex flex-col items-center gap-6 pt-2 pb-4">
        <div className="grid place-items-center">
          <MicButton
            recording={state.phase === "recording"}
            disabled={
              state.status !== "ready" ||
              (state.phase !== "listening" && state.phase !== "recording")
            }
            analyserRef={session.analyserRef}
            onHold={() => void hold()}
          />
          <span className="mt-3 font-mono text-xs font-bold tracking-wide text-soft uppercase">
            {state.status === "closed" ? "Session beendet" : MIC_LABEL[state.phase]}
          </span>
        </div>

        <button
          type="button"
          onClick={end}
          disabled={state.status !== "ready"}
          className="rounded-2xl bg-sunk px-5 py-3 text-sm font-extrabold text-soft shadow-clay-sm transition-shadow active:shadow-clay-in disabled:opacity-50"
        >
          Session beenden
        </button>
      </div>
    </main>
  );
}

function Rail({
  scenario,
  connected,
}: {
  scenario: ScenarioSummary;
  connected: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <span className="rounded-full bg-gradient-to-br from-brand to-pink px-4 py-2 text-sm font-bold text-white shadow-clay-sm">
        🥨 {scenario.title}
      </span>
      <span className="rounded-full bg-surface px-4 py-2 text-sm font-bold text-head shadow-clay-sm">
        Ziel <span className="font-mono">{scenario.level}</span>
      </span>
      <span className="ml-auto flex items-center gap-2 rounded-full bg-surface px-4 py-2 text-xs font-bold text-soft shadow-clay-sm">
        <span
          className={`size-2 rounded-full ${connected ? "bg-good" : "bg-hard"}`}
          aria-hidden
        />
        {connected ? "Verbunden" : "Verbinde..."}
      </span>
    </div>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  const isYou = turn.role === "user";
  return (
    <div className={`max-w-[82%] ${isYou ? "self-end" : "self-start"}`}>
      <div
        className={`mb-1 flex items-center gap-2 px-2 font-mono text-[10px] font-bold tracking-widest text-soft uppercase ${
          isYou ? "justify-end" : ""
        }`}
      >
        {!isYou && <span className="size-3.5 rounded-[5px] bg-brand shadow-clay-in" />}
        {isYou ? "You" : "Tutor"}
        {!isYou && turn.streaming && <Equaliser />}
        {isYou && <span className="size-3.5 rounded-[5px] bg-pink shadow-clay-in" />}
      </div>
      <div
        className={`rounded-clay px-4 py-3.5 text-xl leading-relaxed font-semibold shadow-clay ${
          isYou
            ? "bg-gradient-to-br from-pink to-[#ff9a7a] text-white"
            : "bg-surface text-head"
        }`}
      >
        {turn.text}
      </div>
    </div>
  );
}

function Equaliser() {
  return (
    <span className="ml-1 inline-flex h-[15px] items-end gap-[3px]" aria-hidden>
      {[0, 0.15, 0.3, 0.45].map((delay) => (
        <span
          key={delay}
          className="w-[3px] rounded-[2px] bg-brand"
          style={{ animation: `wortl-eq 0.8s ${delay}s infinite ease-in-out` }}
        />
      ))}
    </span>
  );
}

function errorLabel(stage: string): string {
  switch (stage) {
    case "stt":
      return "Nicht verstanden - bitte nochmal sprechen.";
    case "mic":
      return "Mikrofon nicht verfuegbar - Browser-Berechtigung pruefen.";
    case "socket":
      return "Verbindung verloren - neu laden.";
    case "limit":
      return "Session-Limit erreicht.";
    default:
      return "Etwas ging schief - bitte nochmal.";
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="surface-warm flex min-h-full flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
      {children}
    </main>
  );
}
