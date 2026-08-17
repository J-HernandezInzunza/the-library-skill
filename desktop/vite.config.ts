import { resolve } from "node:path";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [vue()],

  test: {
    // src-tauri is Rust; cargo owns its tests.
    include: ["src/**/*.spec.ts"],

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
      [
        "@tauri-apps/api/core",
        "@tauri-apps/api/event",
        "@tauri-apps/plugin-dialog",
        "@tauri-apps/plugin-opener",
      ].map((specifier) => [specifier, resolve(import.meta.dirname, "src/testing/tauri.ts")]),
    ),
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
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
