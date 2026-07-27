/**
 * The one stateful hook that owns a Talk session: the WebSocket, the mic
 * (MediaRecorder), and the FIFO audio queue. Components stay dumb and read
 * `state` (the pure reducer) plus call hold / release / end.
 *
 * All three browser dependencies are injectable so the hook is tested against
 * fakes in the same event loop, with no real socket or mic (mirroring how the
 * backend tests run_voice_session against a scripted fake). The real
 * implementations live in media.ts.
 */

import { type MutableRefObject, useCallback, useEffect, useReducer, useRef } from "react";

import { type AudioItem, type AudioQueue, createAudioQueue } from "./audioQueue";
import { parseDownFrame } from "./frames";
import { type SessionState, initialState, reducer } from "./reducer";

/** The slice of a WebSocket the hook uses; a real socket or a fake satisfies it. */
export interface VoiceSocket {
  send(data: string | ArrayBuffer): void;
  close(): void;
  onOpen(cb: () => void): void;
  onMessage(cb: (data: string) => void): void;
  onClose(cb: () => void): void;
  onError(cb: (err: unknown) => void): void;
}

/** One held-to-talk recording. `analyser` feeds the waveform, null in tests. */
export interface Recorder {
  start(): void;
  stop(): Promise<ArrayBuffer>;
  readonly analyser: AnalyserNode | null;
  dispose(): void;
}

export interface VoiceDeps {
  createSocket(url: string): VoiceSocket;
  createRecorder(): Promise<Recorder>;
  playAudio(item: AudioItem): Promise<void>;
}

export interface UseVoiceSession {
  state: SessionState;
  /** Open the socket and start the scenario. Idempotent. */
  connect(): void;
  /** Begin capturing a spoken turn (mic held / spacebar down). */
  hold(): Promise<void>;
  /** Finish the turn: stop capture and send the audio up. */
  release(): Promise<void>;
  /** Ask the server to close and produce the debrief. */
  end(): void;
  /** Live mic analyser for the waveform; null when not recording. */
  analyserRef: MutableRefObject<AnalyserNode | null>;
}

export interface VoiceSessionConfig {
  scenarioId: string;
  level?: string;
  /** Override any browser dependency; unset ones use the real implementation. */
  deps?: Partial<VoiceDeps>;
  /** Injected in media.ts by default; overridable for tests. */
  socketUrl?: string;
  realDeps?: VoiceDeps;
}

export function useVoiceSession(config: VoiceSessionConfig): UseVoiceSession {
  const [state, dispatch] = useReducer(reducer, initialState);

  // Config can change per render; read the latest at call time via a ref.
  const configRef = useRef(config);
  configRef.current = config;

  const depsRef = useRef<VoiceDeps>({
    ...(config.realDeps as VoiceDeps),
    ...config.deps,
  });
  const socketRef = useRef<VoiceSocket | null>(null);
  const recorderRef = useRef<Recorder | null>(null);
  // True from the moment the mic is pressed until it is released, so a release
  // that lands while getUserMedia is still resolving can cancel the turn instead
  // of leaving recording stuck on forever.
  const holdingRef = useRef(false);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const queueRef = useRef<AudioQueue | null>(null);
  if (queueRef.current === null) {
    queueRef.current = createAudioQueue((item) => depsRef.current.playAudio(item));
  }

  const disconnect = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    recorderRef.current?.dispose();
    recorderRef.current = null;
    analyserRef.current = null;
    queueRef.current?.clear();
  }, []);

  const connect = useCallback(() => {
    if (socketRef.current) return; // idempotent: one socket per session
    const { scenarioId, level, socketUrl } = configRef.current;
    dispatch({ type: "connecting" });

    const socket = depsRef.current.createSocket(socketUrl ?? "");
    socketRef.current = socket;

    socket.onOpen(() => {
      const start = level
        ? { type: "start", scenario_id: scenarioId, level }
        : { type: "start", scenario_id: scenarioId };
      socket.send(JSON.stringify(start));
    });

    socket.onMessage((data) => {
      const frame = parseDownFrame(data);
      if (!frame) return; // a bad frame never tears down the session
      if (frame.type === "audio") {
        queueRef.current?.enqueue(frame);
        return;
      }
      dispatch({ type: "frame", frame });
      if (frame.type === "session_closed") disconnect();
    });

    socket.onClose(() => {
      socketRef.current = null;
    });
    socket.onError(() => {
      dispatch({
        type: "frame",
        frame: { type: "error", stage: "socket", message: "connection lost" },
      });
    });
  }, [disconnect]);

  const hold = useCallback(async () => {
    if (recorderRef.current || holdingRef.current) return; // already recording
    holdingRef.current = true;
    try {
      const recorder = await depsRef.current.createRecorder();
      if (!holdingRef.current) {
        // Released during mic init: throw the recorder away, never start.
        recorder.dispose();
        return;
      }
      recorderRef.current = recorder;
      analyserRef.current = recorder.analyser;
      recorder.start();
      dispatch({ type: "recordStart" });
    } catch {
      // Mic denied or unavailable is a degraded state, not a crash.
      holdingRef.current = false;
      dispatch({
        type: "frame",
        frame: {
          type: "error",
          stage: "mic",
          message: "microphone unavailable - check browser permissions",
        },
      });
    }
  }, []);

  const release = useCallback(async () => {
    holdingRef.current = false;
    const recorder = recorderRef.current;
    if (!recorder) return; // not recording, or released before mic init finished
    recorderRef.current = null;
    analyserRef.current = null;
    const audio = await recorder.stop();
    recorder.dispose();
    if (audio.byteLength > 0) {
      dispatch({ type: "recordStop" });
      socketRef.current?.send(audio);
    } else {
      // An empty capture never becomes a turn; return to listening.
      dispatch({ type: "recordCancel" });
    }
  }, []);

  const end = useCallback(() => {
    socketRef.current?.send(JSON.stringify({ type: "end" }));
    queueRef.current?.clear();
  }, []);

  // Tear everything down when the Talk screen unmounts.
  useEffect(() => disconnect, [disconnect]);

  return { state, connect, hold, release, end, analyserRef };
}
