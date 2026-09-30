import { describe, expect, it } from "vitest";
import { catalogHue } from "../catalog";
import tokens from "./tokens.css?raw";

/**
 * The contrast every pairing in tokens.css promises, measured from the file itself.
 *
 * The file's comments quote ratios, and comments drift: two of them turned out to describe
 * values the app was no longer shipping. This computes each pairing the way it is actually
 * drawn, composited over the card it sits on, in both themes.
 */

type Rgb = [number, number, number];
type Theme = Record<string, string>;

const [lightSource, darkSource] = tokens.split("@media (prefers-color-scheme: dark)");

function declarations(source: string): Theme {
  return Object.fromEntries([...source.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, n, v]) => [n, v]));
}

const LIGHT = declarations(lightSource);
const DARK = { ...LIGHT, ...declarations(darkSource) };

/** A token's colour and alpha. Hex and `rgb()`/`rgba()` with commas, which is all the file uses. */
function parse(value: string): [Rgb, number] {
  const hex = value.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [[n >> 16, (n >> 8) & 255, n & 255], 1];
  }
  const rgb = value.match(/^rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)$/);
  if (!rgb) throw new Error(`cannot read colour "${value}"`);
  return [[+rgb[1], +rgb[2], +rgb[3]], rgb[4] === undefined ? 1 : +rgb[4]];
}

function over([fg, alpha]: [Rgb, number], ground: Rgb): Rgb {
  return fg.map((channel, i) => channel * alpha + ground[i] * (1 - alpha)) as Rgb;
}

function hsl(hue: number, saturation: number, lightness: number): Rgb {
  const a = saturation * Math.min(lightness, 1 - lightness);
  const f = (n: number) => {
    const k = (n + hue / 30) % 12;
    return 255 * (lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)));
  };
  return [f(0), f(8), f(4)];
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

/** A catalog token at one hue, with the saturation and lightness the theme declares. */
function catalog(theme: Theme, token: string, hue: number): Rgb {
  const match = theme[token].match(/,\s*(\d+)%,\s*(\d+)%/);
  if (!match) throw new Error(`cannot read ${token}`);
  return hsl(hue, +match[1] / 100, +match[2] / 100);
}

// Every hue a catalog can have, plus the fallback an element without one resolves to.
const HUES = [1, 2, 3, 4].map(catalogHue).concat(220);

describe.each([
  ["light", LIGHT],
  ["dark", DARK],
])("contrast in the %s theme", (_, theme) => {
  const color = (token: string) => parse(theme[token]);
  const page = color("--surface-page")[0];
  const card = over(color("--surface-raised"), page);
  const onAccent = color("--text-on-accent")[0];

  it("keeps every status ink readable on its own tint", () => {
    const pairs = ["ok", "absent", "attention", "disabled", "override", "danger"].map((status) => [
      `--status-${status}-ink`,
      `--status-${status}-tint`,
    ]);
    pairs.push(["--accent-ink", "--accent-tint"]);

    for (const [ink, tint] of pairs) {
      const ratio = contrast(color(ink)[0], over(color(tint), card));
      expect(ratio, `${ink} on ${tint}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps text readable on a filled accent", () => {
    expect(contrast(onAccent, color("--accent-bright")[0])).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps catalog chip text readable at every hue", () => {
    for (const hue of HUES) {
      const ratio = contrast(onAccent, catalog(theme, "--catalog-fill", hue));
      expect(ratio, `hue ${hue}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps controls and catalog edges visible against the card", () => {
    // 3:1 is the floor for non-text: a switch, a focus ring, a line that carries meaning.
    const on = color("--control-on")[0];
    expect(contrast(on, card), "switch on").toBeGreaterThanOrEqual(3);
    expect(contrast(color("--control-on-knob")[0], on), "knob on").toBeGreaterThanOrEqual(3);
    expect(contrast(color("--control-off")[0], card), "switch off").toBeGreaterThanOrEqual(3);
    expect(contrast(color("--accent-bright")[0], card), "accent").toBeGreaterThanOrEqual(3);

    for (const hue of HUES) {
      const ratio = contrast(catalog(theme, "--catalog-edge", hue), card);
      expect(ratio, `edge at hue ${hue}`).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("catalog identity", () => {
  it("declares the catalog tokens on every element, not only on :root", () => {
    // A `var()` inside a custom property is substituted where that property is declared, and
    // descendants inherit the result. Declared on :root, `var(--catalog-hue)` resolved there,
    // to the fallback, and every catalog in the app rendered the same blue.
    for (const source of [lightSource, darkSource]) {
      const uncommented = source.replace(/\/\*.*?\*\//gs, "");
      const selector = uncommented.match(/([^{}]+)\{[^}]*--catalog-fill:/)?.[1].trim();
      expect(selector).toBe("*");
    }
  });
});
