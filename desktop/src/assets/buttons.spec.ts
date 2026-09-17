import { describe, expect, it } from "vitest";

/**
 * The guard that keeps button geometry on one scale.
 *
 * The same shape as `tokens.spec.ts`, and for the same reason: a scale on its own does not
 * stop drift. Nothing prevents the next component from writing `padding: 0.25rem 0.55rem`
 * because it looked right next to the thing beside it, which is exactly how this app
 * arrived at eleven sizes for what turned out to be three jobs — two of them a hair apart,
 * on one page, close enough to read as a rendering fault rather than as a distinction.
 *
 * The scale is `button`, `button.btn-sm` and `button.btn-xs` in `App.vue`'s global block:
 * a view's own action, an action on a row or card inside a view, an action inside a line of
 * running text. A size declared anywhere else fails here, named, with the class to use.
 */
const SOURCES = import.meta.glob("../**/*.vue", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** The properties that decide a button's size. `border-radius` is here because four values
    were in use on button-shaped buttons with nothing to tell them apart. */
const SIZING = /^\s*(padding|font-size|border-radius)\b/;

/**
 * Selectors allowed to size a button, by `path: selector`, each because the element is not
 * a button-shaped button and has no place on a scale meant for those.
 *
 * Keyed by path so an entry cannot quietly license the same class name in another file.
 */
const ALLOWED = new Set([
  // The scale itself.
  "App.vue: button",
  "App.vue: button.btn-sm",
  "App.vue: button.btn-xs",
  // A tab. Its padding and its square top corners are what join it to the panel below.
  "components/CatalogTabs.vue: .catalog-tabs__tab",
  // The log's own bar: height is locked to the strip it opens, so padding is horizontal only.
  "components/CommandLog.vue: .command-log__toggle",
  "components/CommandLog.vue: .command-log__more",
  // A card that happens to be a button — it is sized as one of the page's cards.
  "components/EntryDetail.vue: .entry-detail__dep",
  // The on/off switch and the invisible hit target stretched over a row.
  "components/EntryList.vue: .entry-list__switch",
  "components/EntryList.vue: .entry-list__switch::after",
  "components/EntryList.vue: .entry-list__open",
  // A monospace path in a list, and the ✕ beside it.
  "components/InstallPreview.vue: .install-preview__recent",
  "components/InstallPreview.vue: .install-preview__forget",
  // Link-styled: no box at all, so no box to size.
  "components/SecretPrompt.vue: .secret__link",
  "components/SetupReadiness.vue: .setup__toggle",
  // An icon button, sized by its icon.
  "components/Toasts.vue: .toasts__close",
  // Height comes from the composer row, which stretches it to the textarea beside it.
  "components/Walkthrough.vue: .walkthrough__send",
]);

/** Every `.vue` file as [path, contents], with the glob's `../` prefix trimmed off. */
function components(): [string, string][] {
  return Object.entries(SOURCES).map(([path, source]) => [path.replace("../", ""), source]);
}

/** Style blocks with comments stripped, so prose citing an old value is not a violation. */
function styles(source: string): string {
  return [...source.matchAll(/<style[^>]*>(.*?)<\/style>/gs)]
    .map(([, block]) => block.replace(/\/\*.*?\*\//gs, ""))
    .join("\n");
}

/**
 * The classes this file puts on a `<button>`, static and bound.
 *
 * Bound classes are read as the object syntax's keys (`:class="{ ghost: !x }"`), which is
 * the only form the app uses. A class the guard cannot see is one it will not check, so
 * this is deliberately generous: a name here that turns out not to be a button's costs a
 * false positive, which someone reads and allowlists, rather than a silent hole.
 */
function buttonClasses(source: string): Set<string> {
  const template = source.split("<style")[0];
  const found = new Set<string>();

  for (const [tag] of template.matchAll(/<button\b[^>]*>/gs)) {
    for (const [, value] of tag.matchAll(/(?<!:)\bclass="([^"]*)"/g)) {
      for (const name of value.split(/\s+/).filter(Boolean)) found.add(name);
    }
    for (const [, value] of tag.matchAll(/:class="([^"]*)"/gs)) {
      for (const [, name] of value.matchAll(/['"]?([\w-]+)['"]?\s*:/g)) found.add(name);
    }
  }

  return found;
}

/** Does this selector reach a button — as the bare element, or through one of its classes? */
function targetsButton(selector: string, classes: Set<string>): boolean {
  if (/(^|[\s>+~(])button\b/.test(selector)) return true;
  return [...classes].some((name) => new RegExp(`\\.${name}(?![\\w-])`).test(selector));
}

/**
 * Does this selector style the disabled state itself?
 *
 * `:not(:disabled)` is stripped first: `button:active:not(:disabled)` and
 * `.entry-list__switch:hover:not(:disabled)` are rules about the *enabled* state that
 * happen to name the word.
 */
function targetsDisabled(selector: string): boolean {
  return selector.replace(/:not\([^)]*\)/g, "").includes(":disabled");
}

/** How the disabled state is drawn. */
const DISABLED_STATE = /^\s*(opacity|cursor)\b/;

/**
 * Selectors allowed to draw a disabled button themselves, by `path: selector`.
 *
 * Both are here because the global rule says the wrong thing about them, not because they
 * wanted a different look. A rule that only restates `button:disabled` in slightly
 * different numbers belongs deleted — three components had one, and between them they used
 * two opacities and a cursor the rest of the app does not.
 */
const ALLOWED_DISABLED = new Set([
  "App.vue: button:disabled",
  // Deliberately lighter, with its reason in the rule: the switch has already moved to
  // where the user put it, and fading it to 0.45 hides the position that is the point.
  // `progress` over `not-allowed` for the same reason — the toggle is in flight, not refused.
  "components/EntryList.vue: .entry-list__switch:disabled",
  // The invisible button stretched over a card. `not-allowed` would put a refusal cursor
  // across the whole row during selection mode, where the row is simply not a link.
  "components/EntryList.vue: .entry-list__open:disabled",
]);

describe("button geometry lives on one scale", () => {
  it("is actually reading the app", () => {
    // Both checks below pass trivially on an empty corpus, and the glob has returned empty
    // before. A guard that silently stops guarding is worse than no guard, so it asserts it
    // can see its inputs — and the scale it measures against — before it judges anything.
    expect(components().length).toBeGreaterThan(20);
    expect(components().every(([, source]) => source.length > 0)).toBe(true);

    const app = components().find(([path]) => path === "App.vue")?.[1] ?? "";
    expect(styles(app)).toContain("button.btn-sm");
    expect(styles(app)).toContain("button.btn-xs");
  });

  it("finds no button sized outside App.vue's scale", () => {
    const offences: string[] = [];

    for (const [path, source] of components()) {
      const classes = buttonClasses(source);

      for (const [, selectors, body] of styles(source).matchAll(/([^{}]+)\{([^{}]*)\}/gs)) {
        // A rule can carry several selectors; each is judged on its own, so one allowlisted
        // selector in a list does not excuse the rest.
        for (const selector of selectors.split(",")) {
          const cleaned = selector.trim().replace(/\s+/g, " ");
          if (!cleaned || !targetsButton(cleaned, classes)) continue;
          if (ALLOWED.has(`${path}: ${cleaned}`)) continue;

          for (const declaration of body.split(";")) {
            if (!SIZING.test(declaration)) continue;
            offences.push(
              `${path}: ${cleaned} sets "${declaration.trim()}" — use .btn-sm or .btn-xs, ` +
                "or add the selector to ALLOWED with the reason it is not a button-shaped button",
            );
          }
        }
      }
    }

    // Named rather than counted: a count tells the next person a rule exists, the list tells
    // them which line broke it.
    expect(offences).toEqual([]);
  });

  it("finds no button drawing its own disabled state", () => {
    const offences: string[] = [];

    for (const [path, source] of components()) {
      const classes = buttonClasses(source);

      for (const [, selectors, body] of styles(source).matchAll(/([^{}]+)\{([^{}]*)\}/gs)) {
        for (const selector of selectors.split(",")) {
          const cleaned = selector.trim().replace(/\s+/g, " ");
          if (!cleaned || !targetsButton(cleaned, classes) || !targetsDisabled(cleaned)) continue;
          if (ALLOWED_DISABLED.has(`${path}: ${cleaned}`)) continue;

          for (const declaration of body.split(";")) {
            if (!DISABLED_STATE.test(declaration)) continue;
            offences.push(
              `${path}: ${cleaned} sets "${declaration.trim()}" — App.vue's button:disabled ` +
                "already draws this; add the selector to ALLOWED_DISABLED with the reason " +
                "that rule says the wrong thing here",
            );
          }
        }
      }
    }

    expect(offences).toEqual([]);
  });

  it("sees the classes the app puts on its buttons", () => {
    // `targetsButton` is the whole guard: if class extraction silently returned nothing, every
    // scoped rule would look like it targets no button and the suite above would pass empty.
    const detail = components().find(([path]) => path === "components/EntryDetail.vue")?.[1] ?? "";
    expect(buttonClasses(detail)).toContain("entry-detail__choose");
    expect(buttonClasses(detail)).toContain("btn-sm");

    // The bound-class form, which is the one a regex is most likely to miss.
    const preview = components().find(([path]) => path === "components/InstallPreview.vue")?.[1] ?? "";
    expect(buttonClasses(preview)).toContain("install-preview__recent--current");

    // `:not(:disabled)` is the trap in the disabled check: reading it as a disabled-state
    // rule would flag every hover and active rule in the app, and the quickest way out of
    // that is an allowlist entry per false positive, which retires the guard by filling it
    // with noise.
    expect(targetsDisabled("button:disabled")).toBe(true);
    expect(targetsDisabled("button:active:not(:disabled)")).toBe(false);
    expect(targetsDisabled(".entry-list__switch:hover:not(:disabled)")).toBe(false);
  });
});
