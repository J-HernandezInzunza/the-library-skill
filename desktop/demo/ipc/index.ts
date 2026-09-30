/**
 * The Tauri IPC, answered from fixtures so the app runs in an ordinary browser.
 *
 * Vite aliases `@tauri-apps/api/app`, `/core`, `/event`, `plugin-dialog`, and `plugin-opener` onto
 * this module when `VITE_DEMO=1` (see vite.config.ts). Nothing in `src/` imports it, and
 * it is never part of a real build.
 *
 * Separate from `src/testing/tauri.ts` on purpose, though they stand in for the same
 * modules. The test double is programmed per spec and errors on a command nobody thought
 * about, which is right for a test and wrong for a recording: a demo has to answer every
 * command the user's click path touches, on the first try, with something that looks like a
 * real catalog. One module doing both would need a mode flag, and the mode that matters
 * would be whichever the last edit favoured.
 *
 * Driver-agnostic: this is the app's backend during a demo regardless of whether the
 * clicking comes from the CDP recorder, Playwright, or a hand on the mouse.
 */
import * as fixtures from "./fixtures";

/** Roughly what the real CLI costs, so the activity bar and spinners actually appear. */
const LATENCY_MS: Record<string, number> = {
  library_list: 340,
  registry_list: 120,
  entry_show: 260,
  entry_use_preview: 520,
  entry_use: 900,
  entry_disable: 480,
  entry_enable: 480,
  catalog_doctor: 700,
  catalog_sync: 1400,
  entry_setup: 400,
};

const DEFAULT_LATENCY_MS = 200;

const listeners = new Map<string, Array<(event: { payload: unknown }) => void>>();

function emit(event: string, payload: unknown): void {
  for (const handler of listeners.get(event) ?? []) handler({ payload });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let nextCommandId = 1;

/**
 * The argv the real backend would have logged for a command.
 *
 * The command log is on screen in most demos, so an empty or invented argv is a visible
 * lie. Only the commands a storyboard actually reaches need an entry; anything else logs
 * under its own name, which is still true, just terse.
 */
function argvFor(command: string, args: Record<string, unknown>): string[] {
  const names = Array.isArray(args.names) ? (args.names as string[]) : [];
  const name = typeof args.name === "string" ? args.name : "";

  switch (command) {
    case "library_list":
      return ["library", "list", "--json"];
    case "registry_list":
      return ["library", "registry", "list", "--json"];
    case "entry_show":
      return ["library", "show", name, "--json"];
    case "entry_use_preview":
      return ["library", "use", ...names, "--dry-run", "--json"];
    case "entry_use":
      return ["library", "use", ...names, "--json"];
    case "entry_disable":
      return ["library", "disable", ...names, "--json"];
    case "entry_enable":
      return ["library", "enable", ...names, "--json"];
    default:
      return ["library", command.replace(/_/g, " "), "--json"];
  }
}

function answer(command: string, args: Record<string, unknown>): unknown {
  const names = Array.isArray(args.names) ? (args.names as string[]) : [];
  const name = typeof args.name === "string" ? args.name : "";

  switch (command) {
    case "library_list":
      return fixtures.listEntries();
    case "registry_list":
      return fixtures.catalogs;
    case "entry_show":
      return fixtures.showEntry(name);
    case "entry_use_preview":
      return fixtures.previewInstall(names);
    case "entry_use":
      return fixtures.install(names);
    case "entry_disable":
      return fixtures.toggle(names, "disabled");
    case "entry_enable":
      return fixtures.toggle(names, "installed");
    case "entry_setup":
      return fixtures.setupFor(name);
    case "agent_available":
      return true;
    default:
      throw new Error(
        `the demo backend has no answer for "${command}" — add one in demo/ipc/index.ts`,
      );
  }
}

/** `@tauri-apps/api/core`. */
export function isTauri(): boolean {
  // True, or `App.vue` takes its browser-only branch and never loads the catalog at all.
  return true;
}

/** `@tauri-apps/api/core`. */
export async function invoke<T>(
  command: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const id = nextCommandId++;
  const took = LATENCY_MS[command] ?? DEFAULT_LATENCY_MS;

  emit("command://started", { id, argv: argvFor(command, args), cwd: "/Users/dev/library" });
  await sleep(took);

  try {
    const reply = answer(command, args) as T;
    emit("command://finished", { id, code: 0, duration_ms: took });
    return reply;
  } catch (failure) {
    emit("command://finished", { id, code: 1, duration_ms: took });
    throw failure;
  }
}

/** `@tauri-apps/api/event`. */
export async function listen<T>(
  event: string,
  handler: (event: { payload: T }) => void,
): Promise<() => void> {
  const forEvent = listeners.get(event) ?? [];
  forEvent.push(handler as (event: { payload: unknown }) => void);
  listeners.set(event, forEvent);

  return () => {
    listeners.set(
      event,
      (listeners.get(event) ?? []).filter((registered) => registered !== handler),
    );
  };
}

/**
 * `@tauri-apps/plugin-dialog`. Always cancelled.
 *
 * A demo that needs a directory picked should seed it in the fixtures instead: the picker
 * is native chrome that no browser recording can show, so a storyboard routed through it
 * would record a pause in front of nothing.
 */
export async function open(): Promise<string | null> {
  return null;
}

/** `@tauri-apps/plugin-opener`. Swallowed: a demo must not navigate away mid-take. */
export async function openUrl(_url: string): Promise<void> {}

/** `@tauri-apps/plugin-opener`. Swallowed, same reason. */
export async function revealItemInDir(_path: string): Promise<void> {}

/** `@tauri-apps/api/app`. A browser has no window appearance to set. */
export async function setTheme(_theme?: "light" | "dark" | null): Promise<void> {}

/** Put the fixture catalog back to its seed. The recorder calls this before each take. */
export function resetDemo(): void {
  fixtures.reset();
}

// The recorder resets fixture state between takes without importing anything, and a
// storyboard author can poke at the backend from the devtools console.
(window as unknown as { __demoBackend: unknown }).__demoBackend = {
  reset: resetDemo,
  entries: fixtures.listEntries,
};
