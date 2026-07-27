/**
 * The voice WebSocket frame contract, mirrored from the server in
 * backend/app/voice/session.py. JSON goes down (server -> client) and JSON
 * control goes up; a spoken turn goes up as a binary audio blob, never JSON.
 *
 * The server is ours, but a frame is still parsed defensively: a malformed or
 * unknown frame becomes `null` here rather than throwing deep in the reducer,
 * so a single bad message can never tear down a live session.
 */

/** Control messages the client sends up. Audio blobs are sent as raw binary. */
export type UpFrame =
  | {
      type: "start";
      scenario_id: string;
      level?: string;
      thread_id?: string;
      rate?: number;
    }
  | { type: "set_rate"; rate: number }
  | { type: "end" };

/** Frames the server sends down. */
export type DownFrame =
  | { type: "ready"; thread_id: string; scenario_id: string }
  | { type: "transcript"; role: "user"; text: string }
  | { type: "reply_token"; text: string }
  | { type: "audio"; seq: number; mimetype: string; data: string }
  | { type: "turn_done" }
  | { type: "error"; stage: string; message: string }
  | { type: "session_closed"; session_id: number | null };

function isString(v: unknown): v is string {
  return typeof v === "string";
}

/**
 * Parse one text frame from the socket into a typed DownFrame. Returns null for
 * anything that is not valid JSON, not an object, or not a frame shape we know -
 * the caller logs and ignores it instead of crashing the session.
 */
export function parseDownFrame(raw: string): DownFrame | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const f = value as Record<string, unknown>;

  switch (f.type) {
    case "ready":
      return isString(f.thread_id) && isString(f.scenario_id)
        ? { type: "ready", thread_id: f.thread_id, scenario_id: f.scenario_id }
        : null;
    case "transcript":
      return f.role === "user" && isString(f.text)
        ? { type: "transcript", role: "user", text: f.text }
        : null;
    case "reply_token":
      return isString(f.text) ? { type: "reply_token", text: f.text } : null;
    case "audio":
      return typeof f.seq === "number" &&
        isString(f.mimetype) &&
        isString(f.data)
        ? { type: "audio", seq: f.seq, mimetype: f.mimetype, data: f.data }
        : null;
    case "turn_done":
      return { type: "turn_done" };
    case "error":
      return isString(f.stage) && isString(f.message)
        ? { type: "error", stage: f.stage, message: f.message }
        : null;
    case "session_closed":
      return {
        type: "session_closed",
        session_id: typeof f.session_id === "number" ? f.session_id : null,
      };
    default:
      return null;
  }
}
