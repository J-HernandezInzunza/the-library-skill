/**
 * The Tauri IPC, stood in for so components can be mounted without a backend.
 *
 * Aliased over `@tauri-apps/api/core`, `@tauri-apps/api/event`, and the two plugins by
 * `test.alias` in vite.config.ts. One module for all four because they share the one
 * thing the tests care about: an ordered record of what the UI asked the backend to do.
 *
 * Nothing here is imported by the app. It exists on the `src/` side of the tree rather
 * than beside the Rust fixtures because `vue-tsc` type-checks it, which is the point —
 * a double that has drifted from the real signature should fail the gate.
 */

/** What a command was called with, in the order the component sent them. */
export interface RecordedCall {
  command: string;
  args: Record<string, unknown>;
}

type Reply = unknown | ((args: Record<string, unknown>) => unknown);

const replies = new Map<string, Reply>();
const listeners = new Map<string, Array<(event: { payload: unknown }) => void>>();

/**
 * Every call the mounted component has made, `invoke` and plugin alike, oldest first.
 *
 * Argument *names* are worth asserting on and not just values: `invoke` takes an untyped
 * payload, so a renamed command argument is invisible to both compilers and shows up only
 * as a runtime rejection. `InstallPreview` sent `name` to a command that had been changed
 * to take `names` and every check in the gate passed.
 */
export const calls: RecordedCall[] = [];

/**
 * Program what a command returns. Throw from `reply` to exercise the failure path.
 *
 * A plain value covers the common case; a function is for the specs that need to answer
 * the same command differently across a sequence of calls, or to assert on what they were
 * asked before answering.
 */
export function answer(command: string, reply: Reply): void {
  replies.set(command, reply);
}

/**
 * Forget every programmed reply and recorded call.
 *
 * Listeners deliberately survive. `useCommandActivity` subscribes once per module
 * lifetime and never unsubscribes — by design, since commands run while views are
 * swapping — so a reset that dropped the handlers would leave every test after the first
 * one in a file emitting into nothing, and passing for the wrong reason.
 */
export function resetTauri(): void {
  replies.clear();
  calls.length = 0;
}

/** Deliver a backend event to whatever subscribed via `listen`. */
export function emitEvent(event: string, payload: unknown): void {
  for (const handler of listeners.get(event) ?? []) handler({ payload });
}

/**
 * `@tauri-apps/api/core`.
 *
 * An unprogrammed command is an error rather than `undefined`, and it names itself: a
 * spec that mounts a component reaching for a command it did not think about should say
 * which command, not fail later on a missing property of nothing.
 */
export async function invoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  calls.push({ command, args });

  if (!replies.has(command)) {
    throw new Error(`no reply programmed for invoke("${command}") — call answer("${command}", …)`);
  }
  const reply = replies.get(command);
  return (typeof reply === "function" ? reply(args) : reply) as T;
}

/** `@tauri-apps/api/event`. Returns the real unlisten shape. */
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
 * The plugins, which default to benign rather than to an error.
 *
 * Opposite of `invoke` on purpose: reaching the backend is the behaviour under test, but
 * opening a file picker or a browser is a side trip most specs neither drive nor care
 * about, and forcing each one to program a dialog it never opens would be noise.
 */

/** `@tauri-apps/plugin-dialog`. Cancelled unless a spec says otherwise. */
export async function open(options: Record<string, unknown> = {}): Promise<string | null> {
  calls.push({ command: "dialog.open", args: options });
  const reply = replies.get("dialog.open") ?? null;
  return (typeof reply === "function" ? reply(options) : reply) as string | null;
}

/** `@tauri-apps/plugin-opener`. */
export async function openUrl(url: string): Promise<void> {
  calls.push({ command: "opener.openUrl", args: { url } });
}

/** `@tauri-apps/plugin-opener`. */
export async function revealItemInDir(path: string): Promise<void> {
  calls.push({ command: "opener.revealItemInDir", args: { path } });
}

/** The commands sent, in order. For asserting that something ran, or did not. */
export function commandsCalled(): string[] {
  return calls.map((call) => call.command);
}

/** The first call to `command`, or undefined if it was never sent. */
export function callTo(command: string): RecordedCall | undefined {
  return calls.find((call) => call.command === command);
}
