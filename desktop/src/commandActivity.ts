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
    busy: computed(() => running.value.length > 0),
    /** What to show while something is in flight, or `""` when nothing is. */
    label: computed(() => {
      const current = running.value[0];
      return current ? describeArgv(current.argv) : "";
    }),
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
