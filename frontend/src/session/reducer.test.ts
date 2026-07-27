import { describe, expect, it } from "vitest";

import type { DownFrame } from "./frames";
import { type Action, type SessionState, initialState, reducer } from "./reducer";

/** Fold a sequence of actions from the initial state. */
function run(...actions: Action[]): SessionState {
  return actions.reduce(reducer, initialState);
}

const frame = (f: DownFrame): Action => ({ type: "frame", frame: f });

describe("session reducer", () => {
  it("goes ready and records the thread + scenario", () => {
    const state = run(
      { type: "connecting" },
      frame({ type: "ready", thread_id: "th1", scenario_id: "bakery" }),
    );
    expect(state.status).toBe("ready");
    expect(state.threadId).toBe("th1");
    expect(state.scenarioId).toBe("bakery");
    expect(state.phase).toBe("listening");
  });

  it("drives one full turn: record -> transcript -> streamed reply -> done", () => {
    const state = run(
      frame({ type: "ready", thread_id: "th1", scenario_id: "bakery" }),
      { type: "recordStart" },
      { type: "recordStop" },
      frame({ type: "transcript", role: "user", text: "Zwei Broetchen bitte" }),
      frame({ type: "reply_token", text: "Sehr " }),
      frame({ type: "reply_token", text: "gern." }),
      frame({ type: "turn_done" }),
    );

    expect(state.turns).toHaveLength(2);
    expect(state.turns[0]).toMatchObject({
      role: "user",
      text: "Zwei Broetchen bitte",
      streaming: false,
    });
    expect(state.turns[1]).toMatchObject({
      role: "tutor",
      text: "Sehr gern.",
      streaming: false,
    });
    expect(state.phase).toBe("listening");
  });

  it("marks the tutor turn streaming until turn_done", () => {
    const state = run(
      frame({ type: "reply_token", text: "Hallo" }),
    );
    expect(state.turns[0]).toMatchObject({ role: "tutor", streaming: true });
    expect(state.phase).toBe("replying");
  });

  it("renders the scenario opening line (reply with no preceding transcript)", () => {
    const state = run(
      frame({ type: "ready", thread_id: "th1", scenario_id: "bakery" }),
      frame({ type: "reply_token", text: "Guten Morgen!" }),
      frame({ type: "turn_done" }),
    );
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]).toMatchObject({
      role: "tutor",
      text: "Guten Morgen!",
      streaming: false,
    });
  });

  it("keeps the session alive on an error frame and ends the turn", () => {
    const state = run(
      { type: "recordStart" },
      { type: "recordStop" },
      frame({ type: "error", stage: "stt", message: "no speech detected" }),
    );
    expect(state.error).toEqual({ stage: "stt", message: "no speech detected" });
    expect(state.phase).toBe("listening");
    expect(state.status).not.toBe("closed");
  });

  it("clears a prior error when the next turn starts", () => {
    const state = run(
      frame({ type: "error", stage: "tutor", message: "reply failed" }),
      { type: "recordStart" },
    );
    expect(state.error).toBeNull();
    expect(state.phase).toBe("recording");
  });

  it("returns to listening on a cancelled (empty) capture", () => {
    const state = run({ type: "recordStart" }, { type: "recordCancel" });
    expect(state.phase).toBe("listening");
  });

  it("closes on session_closed and records the debrief session id", () => {
    const state = run(frame({ type: "session_closed", session_id: 7 }));
    expect(state.status).toBe("closed");
    expect(state.debriefSessionId).toBe(7);
  });

  it("gives every turn a distinct id", () => {
    const state = run(
      frame({ type: "transcript", role: "user", text: "a" }),
      frame({ type: "reply_token", text: "b" }),
      frame({ type: "turn_done" }),
      frame({ type: "transcript", role: "user", text: "c" }),
    );
    const ids = state.turns.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
