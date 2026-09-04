const KEY = "library.recentProjects";
const LIMIT = 3;

/**
 * Directories previously picked for a project install.
 *
 * A convenience list, deliberately not a "current project" setting: the directory is
 * still chosen per install, so a stale entry costs a click rather than putting files
 * in the wrong repo.
 */
export function recentProjects(): string[] {
  const stored = localStorage.getItem(KEY);
  if (!stored) return [];

  try {
    const parsed: unknown = JSON.parse(stored);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((path): path is string => typeof path === "string");
  } catch {
    // Someone else's key, or a half-written value. A broken recents list must not
    // take the install panel down with it.
    return [];
  }
}

export function rememberProject(dir: string): string[] {
  const updated = withMostRecent(recentProjects(), dir);
  localStorage.setItem(KEY, JSON.stringify(updated));
  return updated;
}

export function forgetProject(dir: string): string[] {
  const updated = recentProjects().filter((path) => path !== dir);
  localStorage.setItem(KEY, JSON.stringify(updated));
  return updated;
}

/** Most recent first, no duplicates, capped. */
export function withMostRecent(recents: string[], dir: string): string[] {
  return [dir, ...recents.filter((path) => path !== dir)].slice(0, LIMIT);
}
