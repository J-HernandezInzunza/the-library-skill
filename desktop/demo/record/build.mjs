/**
 * Regenerate every demo asset in one command, and optionally publish them to the docs.
 *
 * This is the whole loop — serve, record, encode, copy — so adjusting a `pause` and seeing
 * the result is one command rather than four with a server to remember.
 *
 *   npm run demo:build                       # everything, into demo/out/
 *   npm run demo:build -- --publish          # …and copy into images/desktop/
 *   npm run demo:build -- hero --publish     # just one
 *   npm run demo:build -- --scale 2 --fps 30 # flags pass through to record/encode
 *
 * The dev server is started only if :1430 is not already serving, and stopped only if this
 * script is what started it — so it composes with a `npm run demo` you already have open.
 */
import { spawn } from "node:child_process";
import { copyFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEMO_DIR = resolve(HERE, "..");
const APP_DIR = resolve(DEMO_DIR, "..");
/** Where the repo README and docs expect to find these. */
const PUBLISH_DIR = resolve(APP_DIR, "..", "images", "desktop");

const PORT = 1430;
const ORIGIN = `http://localhost:${PORT}/`;

const argv = process.argv.slice(2);
const publish = argv.includes("--publish");
// Anything that is not a flag or a flag's value is a storyboard name.
const names = [];
const passThrough = [];
for (let index = 0; index < argv.length; index++) {
  const argument = argv[index];
  if (argument === "--publish") continue;
  if (argument.startsWith("--")) {
    passThrough.push(argument, argv[++index]);
    continue;
  }
  names.push(argument);
}

function run(command, args, cwd = APP_DIR) {
  return new Promise((ok, fail) => {
    const child = spawn(command, args, { cwd, stdio: "inherit" });
    child.on("error", fail);
    child.on("close", (code) =>
      code === 0 ? ok() : fail(new Error(`${args[0]} exited ${code}`)),
    );
  });
}

async function serving() {
  try {
    const response = await fetch(ORIGIN, { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function startServer() {
  const child = spawn("npx", ["vite", "--port", String(PORT), "--strictPort"], {
    cwd: APP_DIR,
    env: { ...process.env, VITE_DEMO: "1" },
    stdio: "ignore",
    detached: false,
  });
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (await serving()) return child;
    await sleep(300);
  }
  child.kill();
  throw new Error(`the demo server never came up on ${ORIGIN}`);
}

async function main() {
  const storyboards = names.length
    ? names
    : (await readdir(resolve(DEMO_DIR, "storyboards")))
        .filter((file) => file.endsWith(".json"))
        .map((file) => file.replace(/\.json$/, ""))
        .sort();

  for (const name of storyboards) {
    if (!existsSync(resolve(DEMO_DIR, "storyboards", `${name}.json`))) {
      throw new Error(`no storyboard named "${name}"`);
    }
  }

  let server = null;
  if (await serving()) {
    console.log(`· using the demo server already on ${ORIGIN}`);
  } else {
    console.log(`· starting the demo server on ${ORIGIN}`);
    server = await startServer();
  }

  const published = [];
  try {
    for (const name of storyboards) {
      console.log(`\n── ${name} ${"─".repeat(Math.max(0, 56 - name.length))}`);
      await run("node", ["demo/record/record.mjs", name, ...passThrough]);

      // A still is finished the moment it is captured; only a recording needs encoding.
      const still = resolve(DEMO_DIR, "out", name, `${name}.png`);
      const shot = existsSync(still);
      if (!shot) await run("node", ["demo/record/encode.mjs", name, ...passThrough]);

      if (!publish) continue;
      const from = shot ? still : resolve(DEMO_DIR, "out", name, `${name}.gif`);
      const to = resolve(PUBLISH_DIR, shot ? `${name}.png` : `${name}.gif`);
      await copyFile(from, to);
      published.push(to);
    }
  } finally {
    // Only what this script started; a server the user was already running is theirs.
    server?.kill();
  }

  if (published.length) {
    console.log(`\npublished ${published.length} file(s) to ${PUBLISH_DIR}:`);
    for (const file of published) console.log(`  ${file.replace(`${PUBLISH_DIR}/`, "")}`);
    console.log("\nReview with `git diff --stat` before committing; these are tracked files.");
  }
}

main().catch((failure) => {
  console.error(`✗ ${failure.message}`);
  process.exit(1);
});
