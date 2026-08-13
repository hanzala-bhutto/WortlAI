/**
 * Capture the README screenshots of the WortlAI frontend.
 *
 * The whole point: no backend, no mic, no Groq. The frontend talks to the server
 * over a small, well-typed contract (frontend/src/lib/api.ts + the voice frame
 * contract in frontend/src/session/frames.ts), so Playwright intercepts all of it
 * in-browser. The Vite dev proxy never fires because page.route / routeWebSocket
 * answer the requests before they leave the page. That makes these shots
 * deterministic and reproducible on any machine.
 *
 * Run with: npm run screenshots (from frontend/). Boots an in-process Vite dev
 * server, drives headless Chromium, writes PNGs to ../.github/assets/.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type BrowserContext, type Page, type WebSocketRoute } from "playwright";
import { createServer } from "vite";

// The real client-facing contracts. Typing the fixtures against these makes the
// mocks fail `tsc` the moment an endpoint or frame shape drifts, instead of
// silently rendering a screenshot that no longer matches what ships.
import type { Readiness, ScenarioSummary, SessionDebrief } from "../src/lib/api.ts";
import type { DownFrame } from "../src/session/frames.ts";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url))); // frontend/
const outDir = path.resolve(root, "..", ".github", "assets");
const PORT = 5199;
const base = `http://localhost:${PORT}`;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const out = (name: string) => path.join(outDir, name);

// --- Fixtures: shapes mirror the real endpoints, content lifted from the real
// scenario registry (backend/app/agents/scenarios.py) so it reads authentic. ---

const READINESS: Readiness = {
  status: "ok",
  qdrant: { url: "http://localhost:6333", ready: true, error: null },
  keys_configured: { groq: true, nim: true, langfuse: true },
};

const SCENARIOS: ScenarioSummary[] = [
  { id: "vorstellen", title: "Sich vorstellen", level: "A1",
    redemittel: ["Ich heiße …", "Ich komme aus …", "Ich wohne in Dresden."] },
  { id: "supermarkt", title: "Im Supermarkt", level: "A1",
    redemittel: ["Wie viel kostet das?", "Ich bezahle mit Karte.", "Eine Tüte, bitte."] },
  { id: "uhrzeit", title: "Nach der Uhrzeit fragen", level: "A1",
    redemittel: ["Wie spät ist es?", "Es ist halb drei.", "Vielen Dank!"] },
  { id: "baeckerei", title: "In der Bäckerei", level: "A2",
    redemittel: ["Ich hätte gern …", "Was kostet …?", "Zwei Brötchen, bitte."] },
  { id: "cafe", title: "Im Café bestellen", level: "A2",
    redemittel: ["Ich nehme …", "Einen Kaffee, bitte.", "Könnte ich die Karte haben?"] },
  { id: "nachbar", title: "Small Talk mit dem Nachbarn", level: "A2",
    redemittel: ["Wie geht es Ihnen?", "Ja, wirklich schön.", "Bis bald!"] },
  { id: "arzttermin", title: "Termin beim Arzt", level: "B1",
    redemittel: ["Ich hätte gern einen Termin.", "Mir tut … weh.", "Passt Ihnen Dienstag?",
      "Bringen Sie Ihre Versichertenkarte mit."] },
  { id: "wohnung", title: "Wohnungsbesichtigung", level: "B1",
    redemittel: ["Wie hoch ist die Miete?", "Ist die Wohnung möbliert?", "Ab wann ist sie frei?"] },
];

// scenario is the stored scenario *id* (backend/app/agents/persistence.py:57
// Session(scenario=scenario_id)), not the display title. error_type is the
// dotted taxonomy the Corrector emits (backend/app/agents/corrector.py:87), which
// ErrorCard renders verbatim in an uppercase mono badge - so this is what the real
// debrief screen shows. severity is "critical" because only threshold-clearing
// errors are persisted and returned (corrector.py meets_threshold).
const DEBRIEF: SessionDebrief = {
  id: 42,
  scenario: "arzttermin",
  started_at: "2026-08-13T09:12:00Z",
  ended_at: "2026-08-13T09:18:12Z",
  duration_seconds: 372,
  errors: [
    { id: 1, error_type: "grammar.case.dative", severity: "critical",
      utterance: "Mir tut der Hals weh seit zwei Tag.",
      correction: "Mir tut der Hals seit zwei Tagen weh.",
      explanation: "Dativ Plural: nach „seit“ steht der Dativ, und „Tag“ wird zu „Tagen“.",
      created_at: "2026-08-13T09:14:03Z" },
    { id: 2, error_type: "syntax.word-order", severity: "critical",
      utterance: "Ich kann kommen am Dienstag um zehn.",
      correction: "Ich kann am Dienstag um zehn kommen.",
      explanation: "Das Verb „kommen“ steht im Nebensatz-losen Hauptsatz am Ende.",
      created_at: "2026-08-13T09:16:20Z" },
  ],
};

// --- The scripted voice session, driven the moment the mocked socket gets the
// client's `start` frame. Populates the transcript without a mic or real STT. ---

const OPENING = "Praxis Dr. Wagner, guten Tag. Was kann ich für Sie tun?";
const EXCHANGES = [
  { user: "Guten Tag, ich hätte gern einen Termin. Mir tut der Hals weh.",
    tutor: "Das tut mir leid. Haben Sie auch Fieber? Ich hätte am Dienstag um zehn Uhr etwas frei." },
  { user: "Dienstag um zehn passt gut. Muss ich etwas mitbringen?",
    tutor: "Ja, bringen Sie bitte Ihre Versichertenkarte mit. Dann bis Dienstag!" },
];

/** Send one server->client frame, type-checked against the real DownFrame union. */
const sendFrame = (ws: WebSocketRoute, frame: DownFrame) => ws.send(JSON.stringify(frame));

/** Split into word-plus-trailing-space tokens so reply_token streaming looks real. */
const tokenize = (s: string): string[] => s.match(/\S+\s*/g) ?? [s];

async function streamReply(ws: WebSocketRoute, text: string) {
  for (const t of tokenize(text)) {
    sendFrame(ws, { type: "reply_token", text: t });
    await sleep(25);
  }
}

async function playConversation(ws: WebSocketRoute) {
  sendFrame(ws, { type: "ready", thread_id: "demo-thread", scenario_id: "arzttermin" });
  await sleep(120);
  // The scenario opening line arrives as a tutor turn with no preceding transcript.
  await streamReply(ws, OPENING);
  sendFrame(ws, { type: "turn_done" });
  await sleep(160);
  for (const ex of EXCHANGES) {
    sendFrame(ws, { type: "transcript", role: "user", text: ex.user });
    await sleep(160);
    await streamReply(ws, ex.tutor);
    sendFrame(ws, { type: "turn_done" });
    await sleep(160);
  }
}

async function routeHttp(page: Page) {
  await page.route("**/readyz", (r) => r.fulfill({ json: READINESS }));
  await page.route("**/api/v1/scenarios", (r) => r.fulfill({ json: SCENARIOS }));
  await page.route("**/api/v1/sessions/*", (r) => r.fulfill({ json: DEBRIEF }));
}

async function routeVoice(page: Page) {
  await page.routeWebSocket("**/api/v1/voice/stream", (ws) => {
    ws.onMessage((msg) => {
      const text = typeof msg === "string" ? msg : "";
      let frame: { type?: string };
      try {
        frame = JSON.parse(text);
      } catch {
        return; // binary audio never occurs here; we drive the turns ourselves
      }
      if (frame.type === "start") void playConversation(ws);
      else if (frame.type === "end") sendFrame(ws, { type: "session_closed", session_id: 42 });
    });
  });
}

type Scheme = "light" | "dark";

async function newPage(ctx: BrowserContext, scheme: Scheme): Promise<Page> {
  const page = await ctx.newPage();
  await page.emulateMedia({ colorScheme: scheme });
  await routeHttp(page);
  return page;
}

async function shotHome(ctx: BrowserContext, scheme: Scheme) {
  const page = await newPage(ctx, scheme);
  await page.goto(base + "/");
  await page.getByText("System").waitFor();
  await page.getByText("configured").first().waitFor(); // readiness landed
  await sleep(400);
  await page.screenshot({ path: out(`home-${scheme}.png`) });
  await page.close();
}

async function shotScenarios(ctx: BrowserContext, scheme: Scheme) {
  const page = await newPage(ctx, scheme);
  await page.goto(base + "/talk");
  await page.getByText("Termin beim Arzt").first().waitFor();
  await sleep(400);
  await page.screenshot({ path: out("talk-scenarios.png") });
  await page.close();
}

async function driveConversation(page: Page) {
  await page.goto(base + "/talk");
  await page.getByText("Termin beim Arzt").first().click();
  // Wait for the very last tutor sentence to finish streaming. This phrase is
  // unique to the conversation (not in the side-panel Redemittel), so the shot
  // only fires once the whole scripted session has landed.
  await page.getByText("Dann bis Dienstag", { exact: false }).waitFor({ timeout: 15000 });
  await sleep(400);
}

async function shotConversation(ctx: BrowserContext, scheme: Scheme) {
  const page = await newPage(ctx, scheme);
  await routeVoice(page);
  await driveConversation(page);
  await page.screenshot({ path: out(`talk-conversation-${scheme}.png`) });
  await page.close();
}

async function shotDebrief(ctx: BrowserContext, scheme: Scheme) {
  const page = await newPage(ctx, scheme);
  await routeVoice(page);
  await driveConversation(page);
  await page.getByRole("button", { name: "Session beenden" }).click();
  await page.getByText("Dauer:", { exact: false }).waitFor({ timeout: 15000 });
  await sleep(500);
  await page.screenshot({ path: out("debrief.png") });
  await page.close();
}

async function main() {
  await fs.mkdir(outDir, { recursive: true });

  const server = await createServer({
    configFile: path.resolve(root, "vite.config.ts"),
    root,
    server: { port: PORT, strictPort: true },
    logLevel: "warn",
  });
  await server.listen();
  console.log(`vite dev server on ${base}`);

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

  try {
    await shotHome(ctx, "light");
    await shotHome(ctx, "dark");
    await shotScenarios(ctx, "light");
    await shotConversation(ctx, "light");
    await shotConversation(ctx, "dark");
    await shotDebrief(ctx, "light");
    console.log(`\nWrote screenshots to ${outDir}`);
  } finally {
    await ctx.close();
    await browser.close();
    await server.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
