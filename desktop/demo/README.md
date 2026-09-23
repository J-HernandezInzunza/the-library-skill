# Demo recordings

Scripted, repeatable recordings of the app for the README and the docs. The app runs in an
ordinary browser against a fixture backend, a storyboard clicks through it with a visible
cursor, and the frames become a gif and an mp4.

Nothing here is part of a build. `npm run build`, `npm run check`, and `tauri build` do not
see this directory, and nothing in `src/` imports from it.

## Regenerating everything

```sh
npm run demo:build -- --publish
```

That serves, records, encodes, and copies the results into `images/desktop/`, which is where
the repo README expects them. Without `--publish` it stops at `demo/out/` so you can look
first. Name storyboards to do fewer: `npm run demo:build -- hero --publish`.

The dev server is started only if `:1430` is not already serving, and stopped only if the
script is what started it — so it composes with an `npm run demo` you already have open.
Port 1430 rather than 1420, so a recording can run while `tauri dev` is up.

`--publish` overwrites tracked files. Check `git diff --stat` before committing.

The individual steps are still there when you want them:

```sh
npm run demo                          # fixtures on :1430
npm run demo:record install-preview
npm run demo:encode install-preview
```

Requirements: BrowserClaw (BrowserOS neo) open, **not minimized, and not covered by another
window**, and `ffmpeg` on PATH for the encode step.

Useful flags (`demo:build` passes them through):

| Flag | Where | Default | Notes |
| --- | --- | --- | --- |
| `--publish` | build | off | Copy results into `images/desktop/`. |
| `--scale 2` | record | storyboard's, else `1` | Retina frames. Four times the disk. |
| `--fps 30` | record | `25` | Ceiling on kept frames; the screencast only emits on change. |
| `--keep` | record | off | Leave the tab open to inspect where a storyboard died. |
| `--port 9222` | record | `9110` | A different CDP browser. |
| `--width 720` | encode | `900` | Output width. |
| `--only gif` | encode | both | Skip the mp4, or `--only mp4`. |

## Stills

A storyboard with `"shot": true` produces one PNG instead of a recording: the steps run, the
shutter fires, and there is no encode step. `hero.json` is the example.

```json
{ "shot": true, "cursor": false, "scale": 2, "steps": [ … ] }
```

`cursor: false` leaves the pointer off, which a still usually wants. `scale: 2` is the
storyboard's own default — a still costs nothing to render at retina and is the one output
people look at frame by frame. Steps work exactly as they do in a recording, so a still can
navigate somewhere first.

## The pieces

| Path | Depends on | What it is |
| --- | --- | --- |
| `ipc/` | nothing | The fixture backend. Answers `invoke`, mutates state, emits command events. |
| `player/player.js` | nothing | Page-side cursor and step runner. Plain browser JS. |
| `storyboards/*.json` | nothing | What to click, and how long to hold each beat. |
| `record/record.mjs` | **BrowserClaw, CDP** | Drives the take, captures frames or a still. |
| `record/encode.mjs` | **ffmpeg** | Frames to gif and mp4. |
| `record/build.mjs` | the two above | Serve, record, encode, publish, in one command. |

Only the bottom three rows know what is driving the browser. See *Swapping the driver*.

## How it fits together

`vite.config.ts` aliases the four `@tauri-apps/*` modules onto `demo/ipc/index.ts` when
`VITE_DEMO=1` — the same substitution `test.alias` already makes for vitest, at a different
target. The app is otherwise untouched: it still calls `invoke`, still gates on `isTauri()`,
still renders from whatever the backend returns.

The fixture backend is a real little state machine, not a lookup table. Clicking Install
mutates the catalog so the next `library_list` reports the entry installed; clicking the
switch moves it to `disabled` so the badge turns violet and the `disabled` tab appears. The
demos are *about* those transitions, so faking the end state would record the wrong thing.

The recorder injects `player.js`, starts a CDP screencast, and asks the page to play the
storyboard. All the clicking happens inside the page. That is what keeps pacing repeatable
(a 400ms beat is 400ms every take, not one network round trip) and what sidesteps the 30s
ceiling on a BrowserOS neo `run` call — the page plays the whole thing while the recorder
only watches.

A click is dispatched to whatever `document.elementFromPoint` reports under the cursor, not
to the element the step looked up. A step aimed at something covered by an overlay fails
here exactly as it would for a hand on the mouse.

Frame timing is preserved rather than resampled. The screencast emits frames on change, so
a still moment produces no frames at all; the manifest records each frame's real timestamp
and `encode.mjs` feeds ffmpeg per-frame durations through the concat demuxer. Resampling at
a constant rate would compress the pauses out, and the pauses are most of what makes a demo
readable.

## Writing a storyboard

```json
{
  "size": { "width": 900, "height": 800 },
  "ready": ".entry-list",
  "outro": 1600,
  "steps": [
    { "do": "click", "sel": "[aria-label=\"Open pr-and-notify\"]" },
    { "do": "wait", "text": "Install and set up" },
    { "do": "pause", "ms": 1100 }
  ]
}
```

Steps are `click`, `hover`, `type`, `wait`, and `pause`. Targets are either `sel` (a CSS
selector) or `text` (the smallest visible element whose own text matches; add
`"exact": false` to match a substring). `note` is ignored by the player and is there so the
storyboard reads as a shot list rather than a pile of selectors — JSON has no comments.

### Timing

To change how long the viewer looks at something, edit a `pause`. Everything else is
mechanical:

| Knob | On | Default | What it controls |
| --- | --- | --- | --- |
| `ms` | `pause` | 600 | **How long the screen holds still. This is the one to reach for.** |
| `ms` | `click` `hover` `type` | 520 | How long the cursor takes to glide to its target. |
| `before` | `click` | 180 | The beat between the cursor arriving and the press. |
| `settle` | `click` `hover` `type` | 260 | Hold after the click, before the next step starts. |
| `outro` | the storyboard | 900 | Hold on the last frame after the final step. |
| `timeout` | `wait` | 8000 | How long to keep trying before failing. Not pacing. |

A `wait` step takes however long the fixture backend takes to answer, which is set per
command in `ipc/index.ts` (`LATENCY_MS`). Raising a `wait`'s `timeout` does not slow
anything down; it only changes when the storyboard gives up. If a command feels too fast to
read, change its latency there, not the storyboard.

The recorder prints the real total and how long the last frame is held, so a change can be
checked without encoding:

```
✓ 47 frames over 10.6s (last frame held 3.7s)
```

A screencast emits frames only when something changes, so a still beat produces no frames
at all — its length lives in the gap between two frame timestamps, and the closing hold
lives in the manifest's `endedAt`. `encode.mjs` reconstructs both. This is also why the
frame count barely moves when you lengthen a pause.

If a recording comes back far longer than the storyboard asks for, the tab lost focus
mid-take: Chromium throttles `setTimeout` and stops compositing a window it considers
occluded, and both the player's pacing and the fixture backend's latency are timers. The
recorder turns on CDP focus emulation to prevent it and warns if a mid-take gap exceeds
five seconds anyway.

`size` should match the window in `src-tauri/tauri.conf.json` unless a demo needs otherwise.
Prefer `aria-label` and role selectors over class names — they are the app's contract with
assistive tech, so they change less often than styling hooks.

When a storyboard fails, the error names the step index and the step itself. Re-run with
`--keep` and look at the tab it left open.

## Swapping the driver

The fixtures, the player, and the storyboards do not know what is driving them. Moving to
Playwright means:

1. Replace `record/record.mjs` with a script that opens a context with `recordVideo`, calls
   `page.addInitScript` with `player/player.js`, then `page.evaluate` to play the storyboard.
2. Delete `record/encode.mjs`. Playwright writes webm directly.
3. Leave `ipc/`, `player/`, and `storyboards/` alone.

Worth knowing before you do: Playwright's `video.show` draws its own cursor and action-title
overlays, which would make `player.js`'s cursor redundant, and `recordVideo` is a
context-creation option — it is not supported on a context you attached to with
`connectOverCDP`, so a Playwright run would launch its own Chromium rather than reuse
BrowserClaw.
