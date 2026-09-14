import { readonly, ref } from "vue";

/** A notice on screen, with the timer that will take it away. */
export interface Toast {
  id: number;
  kind: "success" | "error";
  message: string;
  /** Command output or a path — shown small and monospaced under the message. */
  detail?: string;
}

/**
 * Notices the app is showing, oldest first.
 *
 * Module-level for the same reason `commandActivity` is: the code that raises a notice is
 * deep in the list and the code that draws it is at the app root, and a chain of props and
 * emits between them would be a second copy of the thing the store already is.
 *
 * These used to be rendered inline, as an item at the top of the entry list. That put them
 * in the layout: flipping one switch inserted a two-line banner and pushed all 42 rows
 * down, so the list moved under the pointer that had just acted on it.
 */
const toasts = ref<Toast[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let nextId = 1;

/**
 * How long a notice stays, by how much there is to read and how much it costs to miss it.
 *
 * A success is one sentence the user already expects, so it goes quickly. A failure is
 * unexpected, carries stderr, and is the only place the reason appears — so it stays long
 * enough to read twice, and `hold` stops the clock while the pointer is on it.
 */
export const LINGER = { success: 4_000, error: 6_000 } as const;

/** Show a notice, and return its id so a caller can take it back early. */
export function notify(toast: Omit<Toast, "id">): number {
  const id = nextId++;
  toasts.value.push({ ...toast, id });
  release(id);
  return id;
}

/** Take a notice away now: the close button, or a caller replacing its own. */
export function dismiss(id: number) {
  clearTimeout(timers.get(id));
  timers.delete(id);
  toasts.value = toasts.value.filter((toast) => toast.id !== id);
}

/**
 * Stop a notice's clock — the pointer is on it, so someone is reading.
 *
 * Without this a long stderr dump can vanish mid-sentence, and the only copy of why a
 * command failed goes with it.
 */
export function hold(id: number) {
  clearTimeout(timers.get(id));
  timers.delete(id);
}

/** Start or restart a notice's clock: raised, or the pointer left it. */
export function release(id: number) {
  const toast = toasts.value.find((candidate) => candidate.id === id);
  if (!toast) return;
  clearTimeout(timers.get(id));
  timers.set(
    id,
    setTimeout(() => dismiss(id), LINGER[toast.kind]),
  );
}

/** Readonly so the only ways in are `notify` and `dismiss`, which own the timers. */
export const activeToasts = readonly(toasts);

/** Drop everything, timers included. For tests, which otherwise leak state between them. */
export function resetToasts() {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
  toasts.value = [];
  nextId = 1;
}
