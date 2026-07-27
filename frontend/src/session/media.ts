/**
 * The browser wiring for the voice loop, kept behind one file so useVoiceSession
 * stays pure and testable (CLAUDE.md: external deps behind one file). Everything
 * here touches a real WebSocket, getUserMedia / MediaRecorder, or MediaSource;
 * the hook only ever sees the VoiceSocket / Recorder / AudioSink interfaces.
 */

import type { AudioSink, Recorder, VoiceDeps, VoiceSocket } from "./useVoiceSession";

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
  // Echo cancellation is the important one: without it the mic captures the
  // Tutor's TTS coming out of the speakers and Whisper transcribes the mush.
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  // An analyser tapped off the same stream feeds the live waveform. Reusing the
  // stream means the visual and the recording are the exact same audio.
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  audioCtx.createMediaStreamSource(stream).connect(analyser);

  // Pin Opus in a webm container at a decent bitrate; the server labels the blob
  // utterance.webm for Groq Whisper, so the container has to match.
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "";
  const recorder = new MediaRecorder(
    stream,
    mimeType ? { mimeType, audioBitsPerSecond: 128000 } : undefined,
  );
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
          () =>
            resolve(
              new Blob(chunks, { type: recorder.mimeType || "audio/webm" }).arrayBuffer(),
            ),
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

/**
 * Streams the per-sentence TTS into one continuous audio element via MediaSource.
 *
 * The server sends a reply's audio as many small MP3 chunks (edge-tts streams
 * them; see _speak in session.py). Those chunks are fragments of one MP3, not
 * standalone clips - playing each as its own <audio> is exactly what made the
 * voice stutter. Here every chunk is appended to a single SourceBuffer so the
 * whole reply decodes and plays gaplessly. A fresh MediaSource is opened per
 * turn; endTurn() closes the stream so playback ends cleanly.
 */
function createMseAudioPlayer(): AudioSink {
  let mediaSource: MediaSource | null = null;
  let sourceBuffer: SourceBuffer | null = null;
  let audio: HTMLAudioElement | null = null;
  let objectUrl: string | null = null;
  const pending: Uint8Array<ArrayBuffer>[] = [];
  let ended = false;

  const supported =
    typeof MediaSource !== "undefined" && MediaSource.isTypeSupported("audio/mpeg");

  function teardown() {
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    mediaSource = null;
    sourceBuffer = null;
    audio = null;
    objectUrl = null;
    pending.length = 0;
    ended = false;
  }

  function pump() {
    if (!sourceBuffer || sourceBuffer.updating) return;
    if (pending.length > 0) {
      try {
        sourceBuffer.appendBuffer(pending.shift()!);
      } catch {
        // QuotaExceeded or an invalid state: drop the chunk, keep the turn alive.
      }
    } else if (ended && mediaSource && mediaSource.readyState === "open") {
      try {
        mediaSource.endOfStream();
      } catch {
        // Already ended or closed; nothing to do.
      }
    }
  }

  function begin() {
    teardown();
    mediaSource = new MediaSource();
    audio = new Audio();
    objectUrl = URL.createObjectURL(mediaSource);
    audio.src = objectUrl;
    mediaSource.addEventListener("sourceopen", () => {
      if (!mediaSource) return;
      try {
        sourceBuffer = mediaSource.addSourceBuffer("audio/mpeg");
        sourceBuffer.addEventListener("updateend", pump);
        pump();
      } catch {
        // Source buffer unsupported: give up silently, the learner keeps the text.
      }
    });
    // Allowed after the user's mic-press gesture (SPA nav preserves the gesture).
    void audio.play().catch(() => {});
  }

  return {
    push(dataB64: string) {
      if (!supported) return;
      if (!mediaSource || ended) begin(); // first chunk of a new turn
      pending.push(base64ToBytes(dataB64));
      pump();
    },
    endTurn() {
      ended = true;
      pump();
    },
    reset() {
      teardown();
    },
  };
}

export const realDeps: VoiceDeps = {
  createSocket: createRealSocket,
  createRecorder: createRealRecorder,
  createAudioSink: createMseAudioPlayer,
};
