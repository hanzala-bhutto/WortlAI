import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AudioItem } from "./audioQueue";
import { type Recorder, type VoiceSocket, useVoiceSession } from "./useVoiceSession";

function fakeSocket() {
  const h = {
    open: [] as Array<() => void>,
    message: [] as Array<(d: string) => void>,
    close: [] as Array<() => void>,
    error: [] as Array<(e: unknown) => void>,
  };
  const sent: Array<string | ArrayBuffer> = [];
  const socket: VoiceSocket = {
    send: (d) => sent.push(d),
    close: vi.fn(),
    onOpen: (cb) => h.open.push(cb),
    onMessage: (cb) => h.message.push(cb),
    onClose: (cb) => h.close.push(cb),
    onError: (cb) => h.error.push(cb),
  };
  return {
    socket,
    sent,
    fireOpen: () => h.open.forEach((f) => f()),
    fireMessage: (d: string) => h.message.forEach((f) => f(d)),
    fireError: () => h.error.forEach((f) => f({})),
  };
}

function fakeRecorder(bytes = new Uint8Array([1, 2, 3])): Recorder {
  return {
    analyser: null,
    start: vi.fn(),
    stop: vi.fn(async () => bytes.buffer),
    dispose: vi.fn(),
  };
}

function setup(overrides?: {
  socket?: ReturnType<typeof fakeSocket>;
  recorder?: Recorder;
  createRecorder?: () => Promise<Recorder>;
  playAudio?: (item: AudioItem) => Promise<void>;
}) {
  const sock = overrides?.socket ?? fakeSocket();
  const recorder = overrides?.recorder ?? fakeRecorder();
  const playAudio = overrides?.playAudio ?? vi.fn(async () => {});
  const hook = renderHook(() =>
    useVoiceSession({
      scenarioId: "bakery",
      socketUrl: "ws://test",
      deps: {
        createSocket: () => sock.socket,
        createRecorder: overrides?.createRecorder ?? (async () => recorder),
        playAudio,
      },
    }),
  );
  return { hook, sock, recorder, playAudio };
}

describe("useVoiceSession", () => {
  it("sends a start frame once the socket opens", () => {
    const { hook, sock } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());

    expect(sock.sent).toHaveLength(1);
    expect(JSON.parse(sock.sent[0] as string)).toEqual({
      type: "start",
      scenario_id: "bakery",
    });
  });

  it("goes ready and assembles a full turn from server frames", () => {
    const { hook, sock } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    act(() =>
      sock.fireMessage(
        '{"type":"ready","thread_id":"t1","scenario_id":"bakery"}',
      ),
    );
    expect(hook.result.current.state.status).toBe("ready");

    act(() => sock.fireMessage('{"type":"transcript","role":"user","text":"Hallo"}'));
    act(() => sock.fireMessage('{"type":"reply_token","text":"Guten "}'));
    act(() => sock.fireMessage('{"type":"reply_token","text":"Tag"}'));
    act(() => sock.fireMessage('{"type":"turn_done"}'));

    const { turns } = hook.result.current.state;
    expect(turns.map((t) => [t.role, t.text])).toEqual([
      ["user", "Hallo"],
      ["tutor", "Guten Tag"],
    ]);
    expect(turns[1].streaming).toBe(false);
  });

  it("captures a held turn and sends the recorded audio up", async () => {
    const { hook, sock, recorder } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    sock.sent.length = 0; // ignore the start frame

    await act(async () => {
      await hook.result.current.hold();
    });
    expect(recorder.start).toHaveBeenCalledOnce();
    expect(hook.result.current.state.phase).toBe("recording");

    await act(async () => {
      await hook.result.current.release();
    });
    expect(recorder.stop).toHaveBeenCalledOnce();
    expect(sock.sent).toHaveLength(1);
    expect(sock.sent[0]).toBeInstanceOf(ArrayBuffer);
  });

  it("does not send an empty capture as a turn", async () => {
    const { hook, sock } = setup({ recorder: fakeRecorder(new Uint8Array(0)) });
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    sock.sent.length = 0;

    await act(async () => {
      await hook.result.current.hold();
    });
    await act(async () => {
      await hook.result.current.release();
    });
    expect(sock.sent).toHaveLength(0);
    // An empty capture returns to listening, not stuck on "thinking".
    expect(hook.result.current.state.phase).toBe("listening");
  });

  it("cancels cleanly if released before the mic finishes initialising", async () => {
    const recorder = fakeRecorder();
    let resolveRecorder: (r: Recorder) => void = () => {};
    const { hook, sock } = setup({
      recorder,
      createRecorder: () =>
        new Promise<Recorder>((res) => {
          resolveRecorder = res;
        }),
    });
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    sock.sent.length = 0;

    // Press then release before getUserMedia resolves, then let it resolve.
    await act(async () => {
      const held = hook.result.current.hold();
      await hook.result.current.release();
      resolveRecorder(recorder);
      await held;
    });

    // The recorder that arrived late is thrown away, never started, nothing sent.
    expect(recorder.start).not.toHaveBeenCalled();
    expect(recorder.dispose).toHaveBeenCalledOnce();
    expect(sock.sent).toHaveLength(0);
    expect(hook.result.current.state.phase).not.toBe("recording");
  });

  it("plays audio frames instead of putting them in transcript state", async () => {
    const playAudio = vi.fn(async () => {});
    const { hook, sock } = setup({ playAudio });
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());

    act(() =>
      sock.fireMessage(
        '{"type":"audio","seq":0,"mimetype":"audio/mpeg","data":"AAAA"}',
      ),
    );

    await vi.waitFor(() =>
      expect(playAudio).toHaveBeenCalledWith(
        expect.objectContaining({ seq: 0, data: "AAAA" }),
      ),
    );
    expect(hook.result.current.state.turns).toHaveLength(0);
  });

  it("surfaces an error frame without closing the session", () => {
    const { hook, sock } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    act(() =>
      sock.fireMessage('{"type":"error","stage":"stt","message":"no speech"}'),
    );

    expect(hook.result.current.state.error).toEqual({
      stage: "stt",
      message: "no speech",
    });
    expect(hook.result.current.state.status).not.toBe("closed");
  });

  it("ignores a malformed frame", () => {
    const { hook, sock } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireOpen());
    expect(() => act(() => sock.fireMessage("garbage{"))).not.toThrow();
    expect(hook.result.current.state.turns).toHaveLength(0);
  });

  it("reports a dropped connection as a degraded error", () => {
    const { hook, sock } = setup();
    act(() => hook.result.current.connect());
    act(() => sock.fireError());
    expect(hook.result.current.state.error?.stage).toBe("socket");
  });
});
