import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { MicButton } from "../components/talk/MicButton";
import { type ErrorEntry, type ScenarioSummary, api } from "../lib/api";
import { realDeps, voiceUrl } from "../session/media";
import type { Phase, Turn } from "../session/reducer";
import { useVoiceSession } from "../session/useVoiceSession";

/** German chrome; the conversation itself is German. Tap-to-gloss needs the
 * lexical graph (Phase 2), so it is deferred rather than stubbed. */
const MIC_LABEL: Record<Phase, string> = {
  listening: "Halten zum Sprechen",
  recording: "Aufnahme laeuft ... loslassen zum Senden",
  thinking: "Einen Moment ...",
  replying: "Tutor spricht ...",
};

export function Talk() {
  const scenarios = useQuery({
    queryKey: ["scenarios"],
    queryFn: api.scenarios,
    staleTime: Infinity,
  });
  // Scenario is fixed for the WS lifetime (level/persona pin the session), so the
  // picker runs once before connect, not a switch mid-conversation.
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const scenario = scenarios.data?.find((s) => s.id === selectedScenarioId);

  const session = useVoiceSession({
    scenarioId: scenario?.id ?? "",
    level: scenario?.level,
    socketUrl: voiceUrl(),
    realDeps,
  });
  const { state, connect, hold, release, end, setRate, replayLast } = session;
  const hasReply = state.turns.some((t) => t.role === "tutor");

  useEffect(() => {
    if (scenario) connect();
  }, [scenario, connect]);

  // Keep the newest message in view as turns arrive and the reply streams in.
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastText = state.turns.at(-1)?.text ?? "";
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [state.turns.length, lastText]);

  // A turn ends on pointerup anywhere, and Spacebar mirrors hold-to-talk, so the
  // mic works even if the pointer drags off the button mid-utterance.
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
      <Shell>
        <div className="grid flex-1 place-items-center px-6 text-center">
          <div>
            <p className="text-lg font-semibold text-head">
              Szenarien konnten nicht geladen werden.
            </p>
            <p className="mt-1 text-sm text-soft">
              Laeuft das Backend? (uvicorn auf :8000)
            </p>
          </div>
        </div>
      </Shell>
    );
  }

  if (!scenario) {
    return (
      <Shell>
        <ScenarioPicker
          scenarios={scenarios.data}
          isLoading={scenarios.isLoading}
          onSelect={setSelectedScenarioId}
        />
      </Shell>
    );
  }

  return (
    <Shell scenario={scenario} connected={state.status === "ready"}>
      <div className="flex min-h-0 flex-1">
        {/* Conversation column + control dock */}
        <section className="flex min-w-0 flex-1 flex-col">
          {/* Scroll lives on this wrapper; justify-end sits on the inner min-h-full
              child so overflow scrolls from the top instead of clipping it. */}
          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col justify-end gap-5 px-6 py-8">
              {!scenario && <p className="m-auto text-soft">Szenario wird geladen ...</p>}
              {scenario && state.turns.length === 0 && (
                <p className="m-auto max-w-md rounded-2xl bg-sunk px-6 py-4 text-center text-base font-medium text-soft shadow-clay-in">
                  Halte die Taste und sprich.
                  <br />
                  <span className="text-head">Hold the mic and speak German.</span>
                </p>
              )}
              {state.turns.map((turn) => (
                <Bubble key={turn.id} turn={turn} />
              ))}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="border-t border-line/70 bg-surface/50 px-6 py-6 backdrop-blur-sm">
            {state.error && (
              <p
                role="status"
                className="mx-auto mb-4 w-fit rounded-xl bg-surface px-4 py-2 text-center text-sm font-semibold text-again shadow-clay-sm"
              >
                {errorLabel(state.error.stage)}
              </p>
            )}
            <div className="relative mx-auto flex max-w-3xl items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <MicButton
                  recording={state.phase === "recording"}
                  disabled={
                    state.status !== "ready" ||
                    (state.phase !== "listening" && state.phase !== "recording")
                  }
                  analyserRef={session.analyserRef}
                  onHold={() => void hold()}
                />
                <span className="font-mono text-xs font-bold tracking-wide text-soft uppercase">
                  {state.status === "closed"
                    ? "Session beendet"
                    : MIC_LABEL[state.phase]}
                </span>
                <button
                  type="button"
                  onClick={replayLast}
                  disabled={!hasReply || state.status !== "ready"}
                  className="cursor-pointer rounded-xl bg-surface px-3.5 py-1.5 text-xs font-bold text-soft shadow-clay-sm transition-[transform,box-shadow] duration-100 enabled:hover:-translate-y-0.5 enabled:hover:text-head enabled:active:translate-y-0.5 enabled:active:shadow-clay-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Wie bitte? (0.7x)
                </button>
              </div>

              <RateSlider
                disabled={state.status !== "ready"}
                onChange={setRate}
                className="absolute left-0"
              />

              <button
                type="button"
                onClick={end}
                disabled={state.status !== "ready"}
                className="absolute right-0 cursor-pointer rounded-2xl bg-sunk px-5 py-3 text-sm font-extrabold text-soft shadow-clay-sm transition-[transform,box-shadow] duration-100 enabled:hover:-translate-y-0.5 enabled:hover:text-head enabled:active:translate-y-0.5 enabled:active:shadow-clay-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
              >
                Session beenden
              </button>
            </div>
          </div>
        </section>

        {/* Desktop-only info panel: what this scenario practises */}
        {scenario && <SidePanel scenario={scenario} />}
      </div>

      {state.status === "closed" && state.debriefSessionId !== null && (
        <DebriefSheet sessionId={state.debriefSessionId} />
      )}
    </Shell>
  );
}

function DebriefSheet({ sessionId }: { sessionId: number }) {
  const debrief = useQuery({
    queryKey: ["session-debrief", sessionId],
    queryFn: () => api.sessionDebrief(sessionId),
  });

  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-ink/40 p-6 backdrop-blur-sm">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col gap-5 rounded-clay bg-surface p-7 shadow-clay">
        <div>
          <p className="font-mono text-[11px] font-bold tracking-widest text-soft uppercase">
            Debrief
          </p>
          <h2 className="mt-1 text-2xl font-extrabold text-head">Session beendet</h2>
        </div>

        {debrief.isLoading && <p className="text-soft">Debrief wird geladen ...</p>}
        {debrief.isError && (
          <p className="text-again">Debrief konnte nicht geladen werden.</p>
        )}

        {debrief.data && (
          <>
            <p className="text-sm font-semibold text-soft">
              Dauer: {formatDuration(debrief.data.duration_seconds)}
            </p>

            {debrief.data.errors.length === 0 ? (
              <p className="rounded-xl bg-sunk px-4 py-3 text-sm font-medium text-soft shadow-clay-in">
                Keine Fehler erkannt. Gut gemacht!
              </p>
            ) : (
              <ul className="flex flex-col gap-3 overflow-y-auto">
                {debrief.data.errors.map((error) => (
                  <ErrorCard key={error.id} error={error} />
                ))}
              </ul>
            )}
          </>
        )}

        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-auto cursor-pointer rounded-2xl bg-gradient-to-br from-brand to-pink px-5 py-3 text-sm font-extrabold text-white shadow-clay-sm transition-[transform,box-shadow] duration-100 hover:-translate-y-0.5 active:translate-y-0.5 active:shadow-clay-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        >
          Neue Session starten
        </button>
      </div>
    </div>
  );
}

function ErrorCard({ error }: { error: ErrorEntry }) {
  return (
    <li className="rounded-xl bg-sunk px-4 py-3 shadow-clay-in">
      <p className="font-mono text-[10px] font-bold tracking-widest text-soft uppercase">
        {error.error_type}
      </p>
      <p className="mt-1.5 text-sm text-again line-through decoration-2">
        {error.utterance}
      </p>
      <p className="mt-1 text-sm font-semibold text-head">{error.correction}</p>
      {error.explanation && (
        <p className="mt-1.5 text-xs text-soft">{error.explanation}</p>
      )}
    </li>
  );
}

/** mm:ss, or a placeholder while the session is still open (should not happen
 * here - the sheet only renders once session_closed has fired). */
function formatDuration(seconds: number | null): string {
  if (seconds === null) return "-";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

/** Mirrors backend/app/config.py: voice_rate_min/default/max. */
const RATE_MIN = 0.7;
const RATE_MAX = 1.2;
const RATE_DEFAULT = 1.0;

/** Sprechtempo slider. Sends set_rate on every change; cheap enough to not
 * debounce, and the backend applies it from the next spoken sentence on. */
function RateSlider({
  disabled,
  onChange,
  className = "",
}: {
  disabled: boolean;
  onChange: (rate: number) => void;
  className?: string;
}) {
  const [rate, setRate] = useState(RATE_DEFAULT);

  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <span className="font-mono text-xs font-bold text-soft" aria-hidden>
        🐢
      </span>
      <input
        type="range"
        aria-label="Sprechtempo"
        min={RATE_MIN}
        max={RATE_MAX}
        step={0.05}
        value={rate}
        disabled={disabled}
        onChange={(e) => {
          const value = Number(e.target.value);
          setRate(value);
          onChange(value);
        }}
        className="h-1.5 w-24 cursor-pointer appearance-none rounded-full bg-sunk shadow-clay-in accent-brand disabled:cursor-not-allowed disabled:opacity-40"
      />
      <span className="font-mono text-xs font-bold text-soft" aria-hidden>
        🐇
      </span>
    </div>
  );
}

function ScenarioPicker({
  scenarios,
  isLoading,
  onSelect,
}: {
  scenarios: ScenarioSummary[] | undefined;
  isLoading: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="mx-auto max-w-5xl">
        <p className="font-mono text-[11px] font-bold tracking-widest text-soft uppercase">
          Szenario waehlen
        </p>
        <h1 className="mt-1 text-2xl font-extrabold text-head">
          Womit moechtest du ueben?
        </h1>

        {isLoading && <p className="mt-6 text-soft">Szenarien werden geladen ...</p>}

        <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {scenarios?.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onSelect(s.id)}
              className="cursor-pointer rounded-clay border border-line bg-surface p-5 text-left shadow-clay transition-[transform,box-shadow] duration-100 hover:-translate-y-0.5 hover:brightness-105 active:translate-y-0.5 active:shadow-clay-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            >
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-extrabold text-head">{s.title}</h2>
                <span className="rounded-full bg-brand/15 px-3 py-1 font-mono text-xs font-bold text-brand">
                  {s.level}
                </span>
              </div>
              <ul className="mt-3.5 flex flex-wrap gap-1.5">
                {s.redemittel.slice(0, 3).map((chunk) => (
                  <li
                    key={chunk}
                    className="rounded-full bg-line px-2.5 py-1 text-xs font-semibold text-ink"
                  >
                    {chunk}
                  </li>
                ))}
              </ul>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Shell({
  children,
  scenario,
  connected,
}: {
  children: React.ReactNode;
  scenario?: ScenarioSummary;
  connected?: boolean;
}) {
  return (
    <div className="surface-warm flex h-dvh flex-col text-ink">
      <header className="flex items-center gap-4 border-b border-line/70 px-6 py-4">
        <div className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-brand to-pink text-lg shadow-clay-sm">
            🥨
          </span>
          <span className="text-lg font-extrabold tracking-tight text-head">
            WortlAI
          </span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {scenario && (
            <>
              <span className="rounded-full bg-surface px-4 py-2 text-sm font-bold text-head shadow-clay-sm">
                {scenario.title}
              </span>
              <span className="rounded-full bg-surface px-4 py-2 font-mono text-sm font-bold text-brand shadow-clay-sm">
                {scenario.level}
              </span>
              <span className="flex items-center gap-2 rounded-full bg-surface px-4 py-2 text-xs font-bold text-soft shadow-clay-sm">
                <span
                  className={`size-2 rounded-full ${connected ? "bg-good" : "bg-hard"}`}
                  aria-hidden
                />
                {connected ? "Verbunden" : "Verbinde ..."}
              </span>
            </>
          )}
          <ThemeToggle />
        </div>
      </header>
      {children}
    </div>
  );
}

/** Manual light/dark, persisted. The tokens key off data-theme; unset falls
 * back to the OS preference (prefers-color-scheme). */
function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(
    () => (localStorage.getItem("wortl-theme") as "light" | "dark" | null) ?? null,
  );

  useEffect(() => {
    if (!theme) return;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("wortl-theme", theme);
  }, [theme]);

  const toggle = () =>
    setTheme((prev) => {
      const current =
        prev ??
        (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      return current === "dark" ? "light" : "dark";
    });

  const isDark =
    theme === "dark" ||
    (theme === null && window.matchMedia("(prefers-color-scheme: dark)").matches);

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? "Zu hellem Modus wechseln" : "Zu dunklem Modus wechseln"}
      className="grid size-10 cursor-pointer place-items-center rounded-xl bg-surface text-lg shadow-clay-sm transition-[transform,box-shadow] duration-100 hover:-translate-y-0.5 hover:brightness-105 active:translate-y-0.5 active:shadow-clay-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
    >
      {isDark ? "☀️" : "🌙"}
    </button>
  );
}

function SidePanel({ scenario }: { scenario: ScenarioSummary }) {
  return (
    <aside className="hidden w-80 shrink-0 flex-col gap-6 border-l border-line/70 bg-surface/40 p-6 lg:flex">
      <div>
        <p className="font-mono text-[11px] font-bold tracking-widest text-soft uppercase">
          Szenario
        </p>
        <h2 className="mt-1 text-xl font-extrabold text-head">{scenario.title}</h2>
        <p className="mt-1 text-sm font-semibold text-soft">
          Ziel-Niveau <span className="font-mono text-brand">{scenario.level}</span>
        </p>
      </div>

      <div>
        <p className="font-mono text-[11px] font-bold tracking-widest text-soft uppercase">
          Redemittel · nutze diese
        </p>
        <ul className="mt-3 flex flex-col gap-2">
          {scenario.redemittel.map((chunk) => (
            <li
              key={chunk}
              className="rounded-xl bg-surface px-3.5 py-2.5 text-sm font-semibold text-head shadow-clay-sm transition-transform duration-100 hover:-translate-y-0.5 hover:brightness-105"
            >
              {chunk}
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-auto rounded-xl bg-sunk px-4 py-3 text-xs font-medium text-soft shadow-clay-in">
        Fehler kommen erst im Debrief, nie mitten im Gespraech. Sprich einfach los.
      </p>
    </aside>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  const isYou = turn.role === "user";
  return (
    <div className={`max-w-[76%] ${isYou ? "self-end" : "self-start"}`}>
      <div
        className={`mb-1.5 flex items-center gap-2 px-2 font-mono text-[11px] font-bold tracking-widest text-soft uppercase ${
          isYou ? "justify-end" : ""
        }`}
      >
        {!isYou && <span className="size-3.5 rounded-[5px] bg-brand shadow-clay-in" />}
        {isYou ? "Du" : "Tutor"}
        {!isYou && turn.streaming && <Equaliser />}
        {isYou && <span className="size-3.5 rounded-[5px] bg-pink shadow-clay-in" />}
      </div>
      <div
        className={`rounded-clay px-5 py-4 text-[22px] leading-relaxed font-semibold shadow-clay ${
          isYou
            ? "bg-gradient-to-br from-pink to-[#ff9a7a] text-white"
            : "bg-surface text-head"
        }`}
      >
        {turn.text || (turn.streaming ? "…" : "")}
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
      return "Verbindung verloren - Seite neu laden.";
    case "limit":
      return "Session-Limit erreicht.";
    default:
      return "Etwas ging schief - bitte nochmal.";
  }
}
