import { computed, ref, type Ref } from "vue";
import { listen } from "@tauri-apps/api/event";
import type { CommandFinished, CommandStarted } from "./types";

/** A logged command, with how it ended once it has. */
export interface LoggedCommand extends CommandStarted {
  code: number | null;
  durationMs: number | null;
}

/**
 * Every command the backend has run this session, newest first.
 *
 * Module-level, not per-component: the command log and the activity bar are two views
 * of the same events, and two subscriptions maintaining two copies would drift. Also
 * means the history survives a view being unmounted, which is most of the app.
 */
const commands = ref<LoggedCommand[]>([]);
let listening: Promise<unknown> | null = null;

/**
 * Work the UI has committed to but the backend has not confirmed yet.
 *
 * `command://started` is a round trip away, so a bar driven by it alone appears some
 * milliseconds *after* the click that caused it — which reads as the button not having
 * worked. An intent is registered synchronously in the click handler, so the app commits
 * to the operation in the same frame and the real command event takes over when it lands.
 */
const intents = ref(new Map<number, string>());
let nextIntentId = 1;

/**
 * Declare that an operation is starting, before anything is sent.
 *
 * Returns the function that ends it, which reports whether the intent was still
 * pending. Call it in a `finally`: an intent that outlives its work leaves the bar
 * running forever, which is worse than the lag it fixes. Disposing twice is harmless.
 */
export function beginIntent(label: string): () => boolean {
  const id = nextIntentId++;
  intents.value.set(id, label);
  return () => intents.value.delete(id);
}

/** `beginIntent` around one awaited call, which is every real use of it. */
export async function withActivity<T>(label: string, work: () => Promise<T>): Promise<T> {
  const done = beginIntent(label);
  try {
    return await work();
  } finally {
    done();
  }
}

/**
 * The shared command stream. Subscribes on first use and never unsubscribes: the
 * listener has to outlive every view, since commands run while views are swapping.
 */
export function useCommandActivity() {
  listening ??= attach();

  const running = computed(() => commands.value.filter((run) => run.code === null));

  return {
    /**
     * Resolves once both listeners are registered.
     *
     * `listen` is an IPC round trip, so a command fired in the same tick as the first
     * render can finish before the subscription exists — and the log is the only
     * safeguard the app has, so missing the first command is not acceptable. Callers
     * that kick off work on mount await this first.
     */
    listening: listening as Promise<unknown>,
    commands: commands as Readonly<Ref<LoggedCommand[]>>,
    running,
    /** Intents included, so the bar is up before the backend has been reached. */
    busy: computed(() => intents.value.size > 0 || running.value.length > 0),
    /**
     * What to show while something is in flight, or `""` when nothing is.
     *
     * The real argv wins once it exists — it is precise, and naming the command is the
     * transparency the app trades for having no approval gate. The intent label only
     * covers the gap before it arrives.
     */
    label: computed(() =>
      activityLabel(running.value[0]?.argv, [...intents.value.values()]),
    ),
  };
}

function attach(): Promise<unknown> {
  const started = listen<CommandStarted>("command://started", ({ payload }) => {
    commands.value.unshift({ ...payload, code: null, durationMs: null });
  });

  const finished = listen<CommandFinished>("command://finished", ({ payload }) => {
    const run = commands.value.find((c) => c.id === payload.id);
    if (!run) return;
    run.code = payload.code;
    run.durationMs = payload.duration_ms;
  });

  return Promise.all([started, finished]);
}

/**
 * What the activity bar says: the running command if there is one, else the intent.
 *
 * The real argv wins because it is precise, and naming the command verbatim is the
 * transparency the app trades for having no approval gate (D5). The intent label exists
 * only to fill the round trip before that argv is known, so it loses as soon as it can.
 */
export function activityLabel(runningArgv: string[] | undefined, intents: string[]): string {
  if (runningArgv) return describeArgv(runningArgv);
  // Newest last: a nested operation is more specific than the one that triggered it.
  return intents.at(-1) ?? "";
}

/**
 * An argv as a short phrase: `bootstrap.py`, `library catalog list`, `library use x`.
 *
 * The full argv already has a home in the command log, so the bar names the operation
 * rather than repeating absolute paths and `--json`.
 */
export function describeArgv(argv: string[]): string {
  const [program, ...args] = argv;
  const tool = program.split("/").pop() ?? program;
  const meaningful = args.filter((arg) => !arg.startsWith("--")).slice(0, 2);

  if (tool === "python3") return "bootstrapping";
  return [tool, ...meaningful].join(" ");
}
