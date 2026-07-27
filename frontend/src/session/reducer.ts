/**
 * The live Talk-session state as a pure reducer, so the whole conversation
 * lifecycle is testable without a socket, a mic, or the DOM (CLAUDE.md: reducer
 * or small store for live session state). useVoiceSession is a thin React shell
 * over this.
 *
 * The turn boundary is deliberately its own action (`recordStop`), decoupled
 * from what triggered it. Today that is the user releasing the mic; a later
 * VAD / hands-free turn (#8 follow-up) dispatches the same action, so nothing
 * downstream of "the user finished a turn" has to change.
 */

import type { DownFrame } from "./frames";

export type Role = "user" | "tutor";

export interface Turn {
  id: string;
  role: Role;
  text: string;
  /** A tutor turn is `streaming` while reply_tokens are still arriving. */
  streaming: boolean;
}

/** Where the session is in its connection lifecycle. */
export type Status = "idle" | "connecting" | "ready" | "closed";

/** What the session is doing inside a live turn - drives the mic UI. */
export type Phase = "listening" | "recording" | "thinking" | "replying";

export interface SessionState {
  status: Status;
  phase: Phase;
  threadId: string | null;
  scenarioId: string | null;
  turns: Turn[];
  /** Last degraded step (guardrail #4): shown, never fatal. Cleared on next turn. */
  error: { stage: string; message: string } | null;
  /** Monotonic id source, kept in state so the reducer stays deterministic. */
  nextId: number;
  /** The learner-store session id, set when the server closes the session; the
   * Talk screen fetches the debrief with it. */
  debriefSessionId: number | null;
}

export const initialState: SessionState = {
  status: "idle",
  phase: "listening",
  threadId: null,
  scenarioId: null,
  turns: [],
  error: null,
  nextId: 0,
  debriefSessionId: null,
};

export type Action =
  | { type: "connecting" }
  | { type: "recordStart" }
  | { type: "recordStop" }
  | { type: "recordCancel" }
  | { type: "reset" }
  | { type: "frame"; frame: DownFrame };

function lastTurn(turns: Turn[]): Turn | undefined {
  return turns[turns.length - 1];
}

function appendTurn(state: SessionState, role: Role, text: string): SessionState {
  const turn: Turn = {
    id: `t${state.nextId}`,
    role,
    text,
    streaming: role === "tutor",
  };
  return { ...state, turns: [...state.turns, turn], nextId: state.nextId + 1 };
}

/** Fold a server frame into the session. Kept beside the reducer so both the
 * frame path and the local mic path share one state machine. */
function applyFrame(state: SessionState, frame: DownFrame): SessionState {
  switch (frame.type) {
    case "ready":
      return {
        ...state,
        status: "ready",
        phase: "listening",
        threadId: frame.thread_id,
        scenarioId: frame.scenario_id,
      };

    case "transcript":
      // What STT heard from the learner. The reply is still pending.
      return { ...appendTurn(state, "user", frame.text), phase: "thinking" };

    case "reply_token": {
      const last = lastTurn(state.turns);
      if (last && last.role === "tutor" && last.streaming) {
        const turns = state.turns.slice(0, -1);
        turns.push({ ...last, text: last.text + frame.text });
        return { ...state, turns, phase: "replying" };
      }
      // First token of a reply (or the scenario opening line, which arrives
      // with no preceding transcript): start a fresh streaming tutor turn.
      return { ...appendTurn(state, "tutor", frame.text), phase: "replying" };
    }

    case "turn_done": {
      const last = lastTurn(state.turns);
      const turns =
        last && last.role === "tutor" && last.streaming
          ? [...state.turns.slice(0, -1), { ...last, streaming: false }]
          : state.turns;
      return { ...state, turns, phase: "listening" };
    }

    case "error":
      // A degraded step ends the current turn but keeps the session alive.
      return {
        ...state,
        error: { stage: frame.stage, message: frame.message },
        phase: "listening",
      };

    case "session_closed":
      return {
        ...state,
        status: "closed",
        phase: "listening",
        debriefSessionId: frame.session_id,
      };

    case "audio":
      // Audio is played by the queue side-effect, not held in reducer state.
      return state;
  }
}

export function reducer(state: SessionState, action: Action): SessionState {
  switch (action.type) {
    case "connecting":
      return { ...state, status: "connecting", error: null };
    case "recordStart":
      // Clear the last error the moment the learner starts a new turn.
      return { ...state, phase: "recording", error: null };
    case "recordStop":
      return { ...state, phase: "thinking" };
    case "recordCancel":
      // An empty capture (released before speaking) never became a turn, so go
      // straight back to listening instead of waiting on a reply that won't come.
      return { ...state, phase: "listening" };
    case "reset":
      return initialState;
    case "frame":
      return applyFrame(state, action.frame);
  }
}
