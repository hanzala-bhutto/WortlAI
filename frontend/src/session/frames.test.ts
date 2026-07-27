import { describe, expect, it } from "vitest";

import { parseDownFrame } from "./frames";

describe("parseDownFrame", () => {
  it("parses each known frame the server sends", () => {
    expect(
      parseDownFrame('{"type":"ready","thread_id":"abc","scenario_id":"bakery"}'),
    ).toEqual({ type: "ready", thread_id: "abc", scenario_id: "bakery" });

    expect(
      parseDownFrame('{"type":"transcript","role":"user","text":"Hallo"}'),
    ).toEqual({ type: "transcript", role: "user", text: "Hallo" });

    expect(parseDownFrame('{"type":"reply_token","text":"Guten"}')).toEqual({
      type: "reply_token",
      text: "Guten",
    });

    expect(
      parseDownFrame('{"type":"audio","seq":0,"mimetype":"audio/mpeg","data":"AA"}'),
    ).toEqual({ type: "audio", seq: 0, mimetype: "audio/mpeg", data: "AA" });

    expect(parseDownFrame('{"type":"turn_done"}')).toEqual({ type: "turn_done" });
    expect(
      parseDownFrame('{"type":"session_closed","session_id":5}'),
    ).toEqual({ type: "session_closed", session_id: 5 });
    // A missing session_id (e.g. the session never reached setup) degrades to
    // null rather than dropping the frame - the session still closes.
    expect(parseDownFrame('{"type":"session_closed"}')).toEqual({
      type: "session_closed",
      session_id: null,
    });
    expect(
      parseDownFrame('{"type":"error","stage":"stt","message":"no speech"}'),
    ).toEqual({ type: "error", stage: "stt", message: "no speech" });
  });

  it("returns null for malformed JSON rather than throwing", () => {
    expect(parseDownFrame("not json")).toBeNull();
    expect(parseDownFrame("")).toBeNull();
  });

  it("returns null for unknown or misshapen frames", () => {
    expect(parseDownFrame('{"type":"nope"}')).toBeNull();
    expect(parseDownFrame('"a string"')).toBeNull();
    expect(parseDownFrame("42")).toBeNull();
    // right type, wrong/missing fields
    expect(parseDownFrame('{"type":"ready","thread_id":"abc"}')).toBeNull();
    expect(parseDownFrame('{"type":"audio","seq":"0","data":"AA"}')).toBeNull();
    expect(parseDownFrame('{"type":"transcript","role":"tutor","text":"x"}')).toBeNull();
  });
});
