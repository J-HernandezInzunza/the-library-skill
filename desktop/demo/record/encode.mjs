/**
 * Turn recorded frames into an mp4 and a gif.
 *
 * Driver-agnostic in principle, but only needed while the recorder emits frames: a
 * Playwright swap would produce webm directly and this step would go away.
 *
 *   node demo/record/encode.mjs install-preview
 *   node demo/record/encode.mjs install-preview --width 720 --fps 20 --only gif
 *
 * Frame timing comes from the recorder's manifest rather than from a fixed rate, via
 * ffmpeg's concat demuxer. A screencast emits frames on change, so a still moment produces
 * no frames at all; resampling at a constant rate would compress those pauses out and take
 * the beats between actions with them, which are most of what makes a demo readable.
 */
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEMO_DIR = resolve(HERE, "..");

const options = { width: 900, fps: 20, only: null, out: null };

const [storyboardName, ...flags] = process.argv.slice(2);
for (let index = 0; index < flags.length; index++) {
  const flag = flags[index];
  if (flag.startsWith("--")) options[flag.slice(2)] = flags[++index];
}
options.width = Number(options.width);
options.fps = Number(options.fps);

if (!storyboardName) {
  console.error("usage: node demo/record/encode.mjs <storyboard> [--width 900] [--fps 20] [--only gif|mp4]");
  process.exit(2);
}

function run(command, args) {
  return new Promise((ok, fail) => {
    const child = spawn(command, args, { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", fail);
    child.on("close", (code) =>
      code === 0 ? ok() : fail(new Error(`${command} exited ${code}\n${stderr.slice(-1600)}`)),
    );
  });
}

async function hasFfmpeg() {
  try {
    await run("ffmpeg", ["-version"]);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  if (!(await hasFfmpeg())) {
    throw new Error("ffmpeg is not on PATH. Install it with `brew install ffmpeg`.");
  }

  const outDir = resolve(options.out ?? resolve(DEMO_DIR, "out", storyboardName));
  const manifestPath = resolve(outDir, "frames.json");
  if (!existsSync(manifestPath)) {
    throw new Error(`no frames at ${manifestPath}. Run the recorder first.`);
  }

  // ffmpeg's concat demuxer resolves `file` paths relative to the list file, so the encode
  // runs from the output directory and the list stays free of absolute paths.
  process.chdir(outDir);

  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const frames = manifest.frames;
  if (frames.length < 2) throw new Error(`only ${frames.length} frame(s); nothing to encode`);

  /**
   * A frame's duration is the gap to the next one. The last has no next, so it runs to
   * `endedAt` — the moment recording stopped, which is later than the last frame whenever
   * the storyboard ends on a still beat. Falling back to the previous gap (for a manifest
   * written before `endedAt` existed) cuts that closing pause off entirely.
   *
   * ffmpeg's concat demuxer additionally wants the final file repeated with no duration of
   * its own, or it drops it.
   */
  const lines = [];
  for (const [index, frame] of frames.entries()) {
    const next = frames[index + 1];
    const until = next?.at ?? manifest.endedAt ?? frame.at + (frame.at - frames[index - 1].at);
    const seconds = (until - frame.at) / 1000;
    lines.push(`file 'frames/${frame.file}'`, `duration ${Math.max(seconds, 0.001).toFixed(4)}`);
  }
  lines.push(`file 'frames/${frames.at(-1).file}'`);

  const listPath = resolve(outDir, "frames.txt");
  await writeFile(listPath, lines.join("\n"));

  const input = ["-f", "concat", "-safe", "0", "-i", "frames.txt"];

  /**
   * The output's true length, as an output option immediately before the filename.
   *
   * The concat demuxer ignores the final `duration` unless the last file is repeated, and
   * then applies it to both the frame and the repeat — so the list above is deliberately
   * 'one hold too long' and gets trimmed back here. Placed anywhere earlier (notably before
   * the palette `-i`) `-t` silently becomes an *input* option for the next input and the
   * gif comes out long while the mp4 comes out right.
   */
  const trim = manifest.endedAt ? ["-t", (manifest.endedAt / 1000).toFixed(3)] : [];
  const only = options.only;
  const seconds = ((manifest.endedAt ?? frames.at(-1).at) / 1000).toFixed(1);
  console.log(`${frames.length} frames, ${seconds}s → ${options.width}px @${options.fps}fps`);

  if (only !== "gif") {
    const mp4 = resolve(outDir, `${storyboardName}.mp4`);
    await run("ffmpeg", [
      "-y", ...input,
      "-vf", `fps=${options.fps},scale=${options.width}:-2:flags=lanczos,format=yuv420p`,
      "-c:v", "libx264", "-crf", "22", "-preset", "slow", "-movflags", "+faststart",
      ...trim, mp4,
    ]);
    console.log(`✓ ${mp4}`);
  }

  if (only !== "mp4") {
    // Two passes: one palette built from the whole clip, then applied. A gif encoded
    // without it gets ffmpeg's default 256-colour web palette, which bands every gradient
    // and shifts the catalog badge hues the demo is there to show.
    const palette = resolve(outDir, "palette.png");
    const filters = `fps=${options.fps},scale=${options.width}:-1:flags=lanczos`;
    await run("ffmpeg", [
      "-y", ...input,
      "-vf", `${filters},palettegen=stats_mode=diff`,
      ...trim, palette,
    ]);
    const gif = resolve(outDir, `${storyboardName}.gif`);
    await run("ffmpeg", [
      "-y", ...input, "-i", palette,
      "-lavfi", `${filters}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle`,
      ...trim, gif,
    ]);
    console.log(`✓ ${gif}`);
  }
}

main().catch((failure) => {
  console.error(`✗ ${failure.message}`);
  process.exit(1);
});
