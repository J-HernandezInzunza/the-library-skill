import { resolve } from "node:path";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

// @ts-expect-error process is a nodejs global
const demo = process.env.VITE_DEMO === "1";

/** The four modules that only exist inside Tauri's WebView. */
const TAURI_MODULES = [
  "@tauri-apps/api/core",
  "@tauri-apps/api/event",
  "@tauri-apps/plugin-dialog",
  "@tauri-apps/plugin-opener",
];

/**
 * Point the IPC at fixtures so `VITE_DEMO=1 npm run dev` runs the app in a plain browser.
 *
 * The same substitution `test.alias` makes below, at a different target, and gated so a
 * normal dev run and every build are untouched. It is two lines here and everything else
 * lives in demo/, which is outside `src/` and outside the app's tsconfig: the recording
 * tooling can be swapped or deleted whole without the app noticing.
 */
const demoAlias = demo
  ? Object.fromEntries(
      TAURI_MODULES.map((specifier) => [
        specifier,
        resolve(import.meta.dirname, "demo/ipc/index.ts"),
      ]),
    )
  : {};

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [vue()],

  resolve: { alias: demoAlias },

  test: {
    // src-tauri is Rust; cargo owns its tests.
    include: ["src/**/*.spec.ts"],

    // Vitest stubs CSS imports to empty by default, `?raw` included, which would make
    // the token guard in src/assets/tokens.spec.ts read an empty stylesheet and pass
    // without checking anything.
    css: true,

    /**
     * There is no Tauri runtime under vitest, so the IPC is replaced at the module
     * boundary rather than mocked per file.
     *
     * `test.alias` and not `vi.mock`: the substitution is the same in every spec, and
     * four hoisted `vi.mock` calls repeated across a dozen files is a place for one of
     * them to be forgotten — which fails as `invoke is not a function` deep inside a
     * component, not as a missing mock. Declared once, it cannot be half-applied.
     */
    alias: Object.fromEntries(
      TAURI_MODULES.map((specifier) => [
        specifier,
        resolve(import.meta.dirname, "src/testing/tauri.ts"),
      ]),
    ),
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    // Demo mode takes its own port so a recording can run while `tauri dev` holds 1420.
    port: demo ? 1430 : 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
