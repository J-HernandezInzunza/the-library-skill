<script setup lang="ts">
import { ref, computed, defineAsyncComponent, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { catalogRows, winningRows, type Row } from "./catalog";
import { describeAppError, isAppError, type Catalog, type Entry } from "./types";
import CatalogSummary from "./components/CatalogSummary.vue";
import CatalogTabs from "./components/CatalogTabs.vue";
import EntryList from "./components/EntryList.vue";

// Shown only on a machine that has never run the tool, so it stays out of the
// initial bundle everyone else loads.
const FirstRun = defineAsyncComponent(() => import("./components/FirstRun.vue"));

const entries = ref<Entry[]>([]);
const catalogs = ref<Catalog[]>([]);
/** The catalog being browsed; `null` browses every catalog's winning entries. */
const activeCatalog = ref<string | null>(null);
const query = ref("");
const loading = ref(false);
/** Kept typed rather than stringified: a first-run state is recoverable, not an error. */
const failure = ref<unknown>(null);

/** Load the catalog and the registry once; search and tabs work off that payload. */
async function load() {
  loading.value = true;
  failure.value = null;
  try {
    const [loadedEntries, loadedCatalogs] = await Promise.all([
      invoke<Entry[]>("library_list"),
      invoke<Catalog[]>("registry_list"),
    ]);
    entries.value = loadedEntries;
    catalogs.value = loadedCatalogs;
  } catch (e) {
    failure.value = e;
    entries.value = [];
    catalogs.value = [];
  } finally {
    loading.value = false;
  }
}

/**
 * The setup step the machine is missing, if that is why loading failed.
 *
 * Both states are recoverable and have a specific next action, so neither belongs in
 * the red error box beside genuine failures.
 */
const setupNeeded = computed(() => {
  const caught = failure.value;
  if (!isAppError(caught)) return null;
  if (caught.kind === "not_bootstrapped") {
    return { state: "not_bootstrapped" as const, path: caught.tool_dir };
  }
  if (caught.kind === "not_configured") {
    return { state: "not_configured" as const, path: caught.config_path };
  }
  return null;
});

const errorMessage = computed(() => {
  if (failure.value === null || setupNeeded.value !== null) return "";
  return describeAppError(failure.value);
});

const multiCatalog = computed(() => catalogs.value.length > 1);

const selectedCatalog = computed(() => {
  const found = catalogs.value.find((catalog) => catalog.id === activeCatalog.value);
  return found ?? null;
});

const rows = computed<Row[]>(() => {
  const catalogId = activeCatalog.value;
  if (catalogId === null) return winningRows(entries.value);
  return catalogRows(entries.value, catalogId);
});

/** Case-insensitive filter over name + description, computed client-side. */
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter(
    ({ entry }) =>
      entry.name.toLowerCase().includes(q) || entry.description.toLowerCase().includes(q),
  );
});

const summary = computed(() => {
  const installed = filtered.value.filter(({ tone }) => tone === "installed").length;
  const overridden = filtered.value.filter(({ tone }) => tone === "overridden").length;

  const parts = [`${filtered.value.length} of ${rows.value.length} entries`, `${installed} installed`];
  if (overridden) parts.push(`${overridden} overridden`);
  return parts.join(" · ");
});

onMounted(load);
</script>

<template>
  <main class="app">
    <FirstRun
      v-if="setupNeeded"
      :state="setupNeeded.state"
      :path="setupNeeded.path"
      @ready="load()"
    />

    <template v-else>
    <header class="topbar">
      <h1>The Library</h1>
      <form class="searchbar" @submit.prevent>
        <input
          v-model="query"
          type="search"
          placeholder="Search skills, agents, prompts…"
        />
        <button type="button" class="ghost" @click="load()">Refresh</button>
      </form>
    </header>

    <CatalogTabs v-if="multiCatalog" v-model="activeCatalog" :catalogs="catalogs" />
    <CatalogSummary v-if="selectedCatalog" :catalog="selectedCatalog" />

    <p v-if="!loading && !errorMessage" class="summary">{{ summary }}</p>

    <p v-if="loading" class="state">Loading…</p>
    <pre v-else-if="errorMessage" class="state error">{{ errorMessage }}</pre>
    <p v-else-if="!filtered.length" class="state">No matching entries.</p>
    <EntryList
      v-else
      :rows="filtered"
      :catalogs="catalogs"
      :show-origin="multiCatalog"
    />
    </template>
  </main>
</template>

<style>
:root {
  color-scheme: light dark;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, sans-serif;
  --app-bg: #f6f6f7;
  /* The sticky header composites over scrolling content, so it needs a surface of
     its own; a bare backdrop-filter leaves the text to overlap the list. */
  --app-bg-sticky: rgba(246, 246, 247, 0.88);
}
body {
  margin: 0;
  background: var(--app-bg);
  color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --app-bg: #1e1e20;
    --app-bg-sticky: rgba(30, 30, 32, 0.88);
  }
  body {
    color: #e6e6e6;
  }
}
</style>

<style scoped>
.app {
  max-width: 860px;
  margin: 0 auto;
  /* No top padding: the sticky header carries its own, so the gap above the title
     stays part of the opaque surface instead of scrolling away from under it. */
  padding: 0 1.25rem 3rem;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 1.5rem 0 0.75rem;
  background: var(--app-bg-sticky);
  backdrop-filter: blur(8px);
}
h1 {
  margin: 0 0 0.75rem;
  font-size: 1.5rem;
}
.searchbar {
  display: flex;
  gap: 0.5rem;
}
.searchbar input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.95rem;
}
button {
  padding: 0.5rem 0.9rem;
  border-radius: 8px;
  border: 1px solid transparent;
  background: #3b82f6;
  color: #fff;
  font-weight: 500;
  cursor: pointer;
}
button.ghost {
  background: transparent;
  color: inherit;
  border-color: rgba(128, 128, 128, 0.4);
}
.summary {
  font-size: 0.85rem;
  opacity: 0.7;
  margin: 0.5rem 0 1rem;
}
.state {
  padding: 2rem 0;
  text-align: center;
  opacity: 0.8;
}
.state.error {
  text-align: left;
  color: #dc2626;
  white-space: pre-wrap;
  background: rgba(220, 38, 38, 0.08);
  padding: 1rem;
  border-radius: 8px;
}
</style>
