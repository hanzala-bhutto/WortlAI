import { describe, expect, it, vi } from "vitest";

import { type AudioItem, createAudioQueue } from "./audioQueue";

const item = (seq: number): AudioItem => ({
  seq,
  mimetype: "audio/mpeg",
  data: `d${seq}`,
});

describe("audio queue", () => {
  it("plays enqueued items strictly in order, one at a time", async () => {
    const played: number[] = [];
    let active = 0;
    const play = vi.fn(async (it: AudioItem) => {
      active += 1;
      // If two ever overlap, the queue is broken.
      expect(active).toBe(1);
      await Promise.resolve();
      played.push(it.seq);
      active -= 1;
    });

    const q = createAudioQueue(play);
    q.enqueue(item(0));
    q.enqueue(item(1));
    q.enqueue(item(2));

    await vi.waitFor(() => expect(played).toEqual([0, 1, 2]));
    expect(q.size).toBe(0);
    expect(q.draining).toBe(false);
  });

  it("skips a sentence that fails to play and keeps going", async () => {
    const played: number[] = [];
    const play = vi.fn(async (it: AudioItem) => {
      if (it.seq === 1) throw new Error("decode failed");
      played.push(it.seq);
    });

    const q = createAudioQueue(play);
    q.enqueue(item(0));
    q.enqueue(item(1));
    q.enqueue(item(2));

    await vi.waitFor(() => expect(played).toEqual([0, 2]));
    expect(q.draining).toBe(false);
  });

  it("clear() drops items not yet played", async () => {
    let resolveFirst: () => void = () => {};
    const play = vi.fn(
      (it: AudioItem) =>
        new Promise<void>((res) => {
          if (it.seq === 0) resolveFirst = res;
          else res();
        }),
    );

    const q = createAudioQueue(play);
    q.enqueue(item(0));
    q.enqueue(item(1));
    q.enqueue(item(2));
    q.clear();
    resolveFirst();

    await vi.waitFor(() => expect(q.draining).toBe(false));
    // Only the in-flight first item ran; 1 and 2 were cleared.
    expect(play).toHaveBeenCalledTimes(1);
  });
});
