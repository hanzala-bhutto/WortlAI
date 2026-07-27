/**
 * A strict FIFO player for the per-sentence TTS audio frames. The server sends
 * `audio` frames in order as each sentence is synthesised (see _speak in
 * session.py); this queue guarantees they *play* in that order and never
 * overlap, even though playback is async and frames can arrive faster than they
 * play.
 *
 * `play` is injected so the queue logic is testable without real audio: the
 * browser wiring (base64 -> Blob -> HTMLAudioElement) lives in media.ts.
 */

export interface AudioItem {
  seq: number;
  mimetype: string;
  data: string;
}

export interface AudioQueue {
  enqueue(item: AudioItem): void;
  /** Drop anything not yet played, e.g. when the session ends. */
  clear(): void;
  readonly size: number;
  readonly draining: boolean;
}

export function createAudioQueue(
  play: (item: AudioItem) => Promise<void>,
): AudioQueue {
  const items: AudioItem[] = [];
  let draining = false;

  async function drain(): Promise<void> {
    draining = true;
    try {
      while (items.length > 0) {
        const item = items.shift()!;
        try {
          await play(item);
        } catch {
          // A single sentence that fails to play is skipped (guardrail #4):
          // the learner keeps the text and the rest of the reply still plays,
          // rather than the queue wedging on one bad frame.
        }
      }
    } finally {
      draining = false;
    }
  }

  return {
    enqueue(item: AudioItem): void {
      items.push(item);
      if (!draining) void drain();
    },
    clear(): void {
      items.length = 0;
    },
    get size(): number {
      return items.length;
    },
    get draining(): boolean {
      return draining;
    },
  };
}
