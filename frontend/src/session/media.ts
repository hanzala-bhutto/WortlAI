/**
 * The browser wiring for the voice loop, kept behind one file so useVoiceSession
 * stays pure and testable (CLAUDE.md: external deps behind one file). Everything
 * here touches a real WebSocket, getUserMedia / MediaRecorder, or HTMLAudioElement;
 * the hook only ever sees the VoiceSocket / Recorder / playAudio interfaces.
 */

import type { AudioItem } from "./audioQueue";
import type { Recorder, VoiceDeps, VoiceSocket } from "./useVoiceSession";

/** Same-origin socket URL. In dev, Vite proxies /api (ws:true) to uvicorn. */
export function voiceUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/v1/voice/stream`;
}

function createRealSocket(url: string): VoiceSocket {
  const ws = new WebSocket(url);
  return {
    send: (data) => ws.send(data),
    close: () => ws.close(),
    onOpen: (cb) => ws.addEventListener("open", () => cb()),
    onMessage: (cb) =>
      ws.addEventListener("message", (e) => {
        // The server only sends text frames; ignore any stray binary.
        if (typeof e.data === "string") cb(e.data);
      }),
    onClose: (cb) => ws.addEventListener("close", () => cb()),
    onError: (cb) => ws.addEventListener("error", (e) => cb(e)),
  };
}

async function createRealRecorder(): Promise<Recorder> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  // An analyser tapped off the same stream feeds the live waveform. Reusing the
  // stream means the visual and the recording are the exact same audio.
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  audioCtx.createMediaStreamSource(stream).connect(analyser);

  const recorder = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.addEventListener("dataavailable", (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  });

  const dispose = () => {
    stream.getTracks().forEach((t) => t.stop());
    void audioCtx.close();
  };

  return {
    analyser,
    start: () => recorder.start(),
    stop: () =>
      new Promise<ArrayBuffer>((resolve) => {
        recorder.addEventListener(
          "stop",
          () => resolve(new Blob(chunks, { type: recorder.mimeType }).arrayBuffer()),
          { once: true },
        );
        recorder.stop();
      }),
    dispose,
  };
}

function base64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(b64);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Play one TTS sentence and resolve when it finishes, so the queue can advance. */
function playBase64Audio(item: AudioItem): Promise<void> {
  const blob = new Blob([base64ToBytes(item.data)], { type: item.mimetype });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  return new Promise<void>((resolve, reject) => {
    audio.addEventListener("ended", () => {
      URL.revokeObjectURL(url);
      resolve();
    });
    audio.addEventListener("error", () => {
      URL.revokeObjectURL(url);
      reject(new Error("audio playback failed"));
    });
    void audio.play().catch(reject);
  });
}

export const realDeps: VoiceDeps = {
  createSocket: createRealSocket,
  createRecorder: createRealRecorder,
  playAudio: playBase64Audio,
};
