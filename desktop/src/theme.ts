import { setTheme } from "@tauri-apps/api/app";
import { isTauri } from "@tauri-apps/api/core";

export type Appearance = "system" | "light" | "dark";

const KEY = "library.appearance";

/** Toggle order, which starts from the default so one full cycle lands back on it. */
export const APPEARANCES: Appearance[] = ["system", "light", "dark"];

export function storedAppearance(): Appearance {
  const stored = localStorage.getItem(KEY);
  const known = APPEARANCES.find((appearance) => appearance === stored);
  return known ?? "system";
}

export function nextAppearance(current: Appearance): Appearance {
  const next = (APPEARANCES.indexOf(current) + 1) % APPEARANCES.length;
  return APPEARANCES[next];
}

/**
 * Set the window's native appearance, and remember it for the next launch.
 *
 * Native rather than a `data-theme` attribute on the page: the WebView reports the window's
 * appearance as `prefers-color-scheme`, so tokens.css keeps one dark block and the title bar
 * switches along with the content.
 */
export async function applyAppearance(appearance: Appearance): Promise<void> {
  localStorage.setItem(KEY, appearance);
  // A plain browser has no window to set, and the call would reject.
  if (!isTauri()) return;

  const theme = appearance === "system" ? null : appearance;
  await setTheme(theme);
}
