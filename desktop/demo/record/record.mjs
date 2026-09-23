/**
 * Record a storyboard against BrowserOS neo (BrowserClaw) over the Chrome DevTools Protocol.
 *
 * This is the only file in demo/ that knows what is driving the browser. The fixtures, the
 * player, and the storyboards are all driver-agnostic; swapping to Playwright means
 * replacing this one file with a script that calls `browser.newContext({ recordVideo })`
 * and `page.addInitScript(player.js)`, and deleting encode.mjs (Playwright emits webm
 * directly). Nothing else in demo/ changes, and nothing in src/ ever did.
 *
 * Why CDP rather than neo's MCP tools: neo has no video capability, and its `run` calls are
 * capped at 30s of wall time. `Page.startScreencast` is the recording surface underneath,
 * and it is unbounded. The clicking is done by the player inside the page, so the cap never
 * applies to the take.
 *
 *   node demo/record/record.mjs install-preview
 *   node demo/record/record.mjs disable-preview --scale 2 --fps 30
 *
 * Requires `VITE_DEMO=1 npm run dev` to be running, and BrowserClaw to be open and not
 * minimized: a background tab composites nothing and the screencast yields zero frames.
 */
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEMO_DIR = resolve(HERE, "..");

const options = {
  port: 9110,
  url: "http://localhost:1430/",
  fps: 25,
  // Null so a storyboard's own `scale` can supply the default and `--scale` still wins.
  scale: null,
  keep: false,
  out: null,
};

const [storyboardName, ...flags] = process.argv.slice(2);
for (let index = 0; index < flags.length; index++) {
  const flag = flags[index];
  if (flag === "--keep") options.keep = true;
  else if (flag.startsWith("--")) options[flag.slice(2)] = flags[++index];
}
options.port = Number(options.port);
options.fps = Number(options.fps);
if (options.scale !== null) options.scale = Number(options.scale);

if (!storyboardName) {
  console.error("usage: node demo/record/record.mjs <storyboard> [--scale 2] [--fps 30] [--keep]");
  process.exit(2);
}

/** A CDP connection: request/response by id, plus event subscription. */
class Cdp {
  #socket;
  #nextId = 1;
  #pending = new Map();
  #handlers = new Map();

  static async open(wsUrl) {
    const cdp = new Cdp();
    cdp.#socket = new WebSocket(wsUrl);
    cdp.#socket.onmessage = (raw) => cdp.#receive(JSON.parse(raw.data));
    await new Promise((ok, fail) => {
      cdp.#socket.onopen = ok;
      cdp.#socket.onerror = () => fail(new Error(`could not connect to ${wsUrl}`));
    });
    return cdp;
  }

  #receive(message) {
    if (message.id && this.#pending.has(message.id)) {
      const { ok, fail } = this.#pending.get(message.id);
      this.#pending.delete(message.id);
      if (message.error) fail(new Error(`${message.error.message} (${message.error.code})`));
      else ok(message.result);
      return;
    }
    for (const handler of this.#handlers.get(message.method) ?? []) handler(message.params);
  }

  send(method, params = {}) {
    const id = this.#nextId++;
    this.#socket.send(JSON.stringify({ id, method, params }));
    return new Promise((ok, fail) => this.#pending.set(id, { ok, fail }));
  }

  on(method, handler) {
    this.#handlers.set(method, [...(this.#handlers.get(method) ?? []), handler]);
  }

  close() {
    this.#socket.close();
  }

  /** Evaluate in the page and return the value, surfacing a page-side throw as one here. */
  async eval(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description ?? "page threw");
    }
    return result.result.value;
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function browserEndpoint(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`);
    return (await response.json()).webSocketDebuggerUrl;
  } catch {
    throw new Error(
      `no CDP on 127.0.0.1:${port}. Open BrowserClaw, or pass --port for a different browser.`,
    );
  }
}

async function targetSocket(port, targetId) {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const found = list.find((target) => target.id === targetId);
  if (!found) throw new Error(`target ${targetId} vanished before it could be attached`);
  return found.webSocketDebuggerUrl;
}

async function main() {
  const storyboardPath = resolve(DEMO_DIR, "storyboards", `${storyboardName}.json`);
  if (!existsSync(storyboardPath)) throw new Error(`no storyboard at ${storyboardPath}`);
  const storyboard = JSON.parse(await readFile(storyboardPath, "utf8"));

  const width = storyboard.size?.width ?? 900;
  const height = storyboard.size?.height ?? 800;
  // A still is cheap to render at 2x and is the one output judged frame by frame, so a
  // storyboard may raise its own default. `--scale` still overrides it.
  const scale = options.scale ?? storyboard.scale ?? 1;
  const outDir = resolve(options.out ?? resolve(DEMO_DIR, "out", storyboardName));
  const framesDir = resolve(outDir, "frames");

  await rm(outDir, { recursive: true, force: true });
  await mkdir(storyboard.shot ? outDir : framesDir, { recursive: true });

  const browser = await Cdp.open(await browserEndpoint(options.port));
  const { targetId } = await browser.send("Target.createTarget", { url: "about:blank" });

  let page;
  try {
    page = await Cdp.open(await targetSocket(options.port, targetId));

    await page.send("Page.enable");
    await page.send("Runtime.enable");
    // Match the real Tauri window, so the recording is the app's own geometry rather than
    // whatever size the browser window happens to be.
    await page.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: scale,
      mobile: false,
    });
    await page.send("Page.bringToFront");
    /**
     * Keep the page believing it is focused for the whole take.
     *
     * Chromium throttles `setTimeout` and stops compositing a tab it considers occluded —
     * another window in front of BrowserClaw is enough. Both the player's pacing and the
     * fixture backend's latency are timers, so a take that loses focus does not fail: it
     * silently stretches. One run of the 10.5s disable storyboard came back as 49.7s, with
     * a single 41.8s gap and no frames in it.
     */
    await page.send("Emulation.setFocusEmulationEnabled", { enabled: true });

    console.log(`→ ${options.url} at ${width}x${height} @${scale}x`);
    await page.send("Page.navigate", { url: options.url });

    const ready = storyboard.ready ?? "body";
    const deadline = Date.now() + 20000;
    for (;;) {
      const there = await page.eval(`!!document.querySelector(${JSON.stringify(ready)})`);
      if (there) break;
      if (Date.now() > deadline) {
        throw new Error(
          `"${ready}" never appeared. Is \`VITE_DEMO=1 npm run dev\` running on ${options.url}?`,
        );
      }
      await sleep(200);
    }

    await page.eval(await readFile(resolve(DEMO_DIR, "player", "player.js"), "utf8"));
    // Fresh fixture state, so take two starts from the frame take one did.
    await page.eval("window.__demoBackend?.reset?.()");
    // A still has nothing for a cursor to explain, so a storyboard can leave it off.
    if (storyboard.cursor !== false) {
      await page.eval(`window.__demo.arm(${Math.round(width * 0.5)}, ${Math.round(height * 0.62)})`);
    }
    await sleep(400);

    if (storyboard.shot) {
      console.log(`● shooting ${storyboard.steps.length} steps`);
      const still = await page.eval(`window.__demo.play(${JSON.stringify(storyboard)})`);
      if (still.state === "failed") throw new Error(`storyboard failed: ${still.error}`);
      await sleep(storyboard.outro ?? 400);

      const { data } = await page.send("Page.captureScreenshot", { format: "png" });
      const shot = resolve(outDir, `${storyboardName}.png`);
      await writeFile(shot, Buffer.from(data, "base64"));
      console.log(`✓ ${width * scale}x${height * scale} → ${shot}`);
      return;
    }

    const frames = [];
    const minGapMs = 1000 / options.fps;
    let lastKeptAt = -Infinity;

    page.on("Page.screencastFrame", async ({ data, sessionId, metadata }) => {
      // Ack every frame, keep only the ones the target rate has room for: an unacked frame
      // stops the stream, so dropping is a decision made after acknowledging, never instead.
      page.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
      const at = metadata.timestamp * 1000;
      if (at - lastKeptAt < minGapMs) return;
      lastKeptAt = at;
      frames.push({ at, data });
    });

    await page.send("Page.startScreencast", {
      format: "png",
      everyNthFrame: 1,
      maxWidth: width * scale,
      maxHeight: height * scale,
    });

    console.log(`● recording ${storyboard.steps.length} steps`);
    const outcome = await page.eval(
      `window.__demo.play(${JSON.stringify(storyboard)})`,
    );

    // A beat of stillness at the end, so the last state is on screen long enough to read.
    await sleep(storyboard.outro ?? 900);
    // Before stopping, on the same clock as the frame timestamps: a screencast emits only
    // on change, so a still tail produces no frames at all and the recording would
    // otherwise end at the last thing that moved. `endedAt` is how long the final frame
    // was actually on screen, and without it a closing `pause` is silently discarded.
    const stoppedAt = Date.now();
    await page.send("Page.stopScreencast");

    if (outcome.state === "failed") throw new Error(`storyboard failed: ${outcome.error}`);
    if (!frames.length) {
      throw new Error(
        "no frames captured. BrowserClaw must be open and not minimized; a background tab composites nothing.",
      );
    }

    const first = frames[0].at;
    const manifest = [];
    for (const [index, frame] of frames.entries()) {
      const file = `${String(index).padStart(5, "0")}.png`;
      await writeFile(resolve(framesDir, file), Buffer.from(frame.data, "base64"));
      manifest.push({ file, at: Math.round(frame.at - first) });
    }

    const endedAt = Math.round(stoppedAt - first);

    await writeFile(
      resolve(outDir, "frames.json"),
      JSON.stringify(
        { storyboard: storyboardName, width, height, scale, endedAt, frames: manifest },
        null,
        2,
      ),
    );

    const seconds = (endedAt / 1000).toFixed(1);
    const held = ((endedAt - manifest.at(-1).at) / 1000).toFixed(1);
    console.log(`✓ ${manifest.length} frames over ${seconds}s (last frame held ${held}s) → ${framesDir}`);

    /**
     * A stall loud enough to be worth naming, in case focus emulation was not enough.
     *
     * Only gaps *between* frames count; the closing hold is a gap by design and is already
     * reported above. Five seconds is well past any pause a storyboard would plausibly ask
     * for and well short of the tens of seconds a throttled tab produces.
     */
    const stall = manifest.reduce(
      (worst, frame, index) =>
        index === 0 ? worst : Math.max(worst, frame.at - manifest[index - 1].at),
      0,
    );
    if (stall > 5000) {
      console.warn(
        `! a ${(stall / 1000).toFixed(1)}s gap appeared mid-take — the tab was probably backgrounded.\n` +
          `  Leave BrowserClaw's window visible and unobstructed, and re-record.`,
      );
    }
    console.log(`  next: node demo/record/encode.mjs ${storyboardName}`);
  } finally {
    page?.close();
    if (!options.keep) await browser.send("Target.closeTarget", { targetId }).catch(() => {});
    browser.close();
  }
}

main().catch((failure) => {
  console.error(`✗ ${failure.message}`);
  process.exit(1);
});
