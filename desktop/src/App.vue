<script setup lang="ts">
import { ref, computed, defineAsyncComponent, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { allRows, catalogRows, winningRows, type Row } from "./catalog";
import { useCommandActivity } from "./commandActivity";
import { describeAppError, isAppError, type Catalog, type Entry } from "./types";
import ActivityBar from "./components/ActivityBar.vue";
import Busy from "./components/Busy.vue";
import CatalogSummary from "./components/CatalogSummary.vue";
import CatalogTabs from "./components/CatalogTabs.vue";
import CommandLog from "./components/CommandLog.vue";
import EntryList from "./components/EntryList.vue";

// Shown only on a machine that has never run the tool, so it stays out of the
// initial bundle everyone else loads.
const FirstRun = defineAsyncComponent(() => import("./components/FirstRun.vue"));

// Only reached by clicking into an entry, so it stays out of the initial bundle.
const EntryDetail = defineAsyncComponent(() => import("./components/EntryDetail.vue"));
const Doctor = defineAsyncComponent(() => import("./components/Doctor.vue"));
const Sync = defineAsyncComponent(() => import("./components/Sync.vue"));

// Attached here, at the earliest point in the app, so the command log and the activity
// bar are subscribed before anything can run.
const { listening } = useCommandActivity();

const entries = ref<Entry[]>([]);
const catalogs = ref<Catalog[]>([]);
/** The catalog being browsed; `null` browses every catalog's winning entries. */
const activeCatalog = ref<string | null>(null);
const query = ref("");
/**
 * The entries clicked into, most recent last.
 *
 * A trail rather than a single name so Back returns to where you came from: opening a
 * dependency from a detail view and landing back on the full catalog loses your place
 * exactly when you are walking a dependency chain.
 */
const trail = ref<string[]>([]);
const openEntry = computed(() => trail.value.at(-1) ?? null);
/** The entry Back returns to, or null when that is the catalog. */
const previousEntry = computed(() => trail.value.at(-2) ?? null);
const showDoctor = ref(false);
const showSync = ref(false);
/** Collapse the catalog to just the copies that would actually install. */
const hideOverridden = ref(false);
// True from the start: the app always loads on mount, and defaulting to false shows an
// empty catalog for a frame before the first command has even been sent.
const loading = ref(true);
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
  if (catalogId !== null) return catalogRows(entries.value, catalogId);
  if (hideOverridden.value) return winningRows(entries.value);
  return allRows(entries.value);
});

/** Only worth offering once something is actually being overridden. */
const overriddenCount = computed(
  () => entries.value.filter((entry) => entry.overridden_by).length,
);

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

onMounted(async () => {
  // The first command must appear in the log like every other one, so it waits for the
  // subscription rather than racing it.
  await listening;
  await load();
});
</script>

<template>
  <main class="app">
    <FirstRun
      v-if="setupNeeded"
      :state="setupNeeded.state"
      :path="setupNeeded.path"
      @ready="load()"
    />

    <Doctor v-else-if="showDoctor" @close="showDoctor = false" />

    <Sync v-else-if="showSync" @close="showSync = false" @synced="load()" />

    <EntryDetail
      v-else-if="openEntry"
      :name="openEntry"
      :back-to="previousEntry"
      :catalogs="catalogs"
      :entries="entries"
      @close="trail.pop()"
      @open="trail.push($event)"
      @installed="load()"
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
        <button type="button" class="ghost" @click="showSync = true">Sync</button>
        <button type="button" class="ghost" @click="showDoctor = true">Doctor</button>
      </form>
    </header>

    <CatalogTabs v-if="multiCatalog" v-model="activeCatalog" :catalogs="catalogs" />
    <CatalogSummary v-if="selectedCatalog" :catalog="selectedCatalog" />

    <p v-if="!loading && !errorMessage" class="summary">
      {{ summary }}
      <label v-if="activeCatalog === null && overriddenCount" class="summary__toggle">
        <input v-model="hideOverridden" type="checkbox" />
        Hide overridden
      </label>
    </p>

    <Busy v-if="loading" label="Reading the catalog…" />
    <pre v-else-if="errorMessage" class="state error">{{ errorMessage }}</pre>
    <p v-else-if="!filtered.length" class="state">No matching entries.</p>
    <EntryList
      v-else
      class="fade-in"
      :rows="filtered"
      :catalogs="catalogs"
      :show-origin="multiCatalog"
      @select="trail = [$event]"
    />
    </template>

    <ActivityBar />
    <CommandLog />
  </main>
</template>

<style>
:root {
  color-scheme: light dark;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, sans-serif;
  --app-bg: #f6f6f7;
  /* The sticky header composites over scrolling content, so it needs a surface of
     its own; a bare backdrop-filter leaves the text to overlap the list. */
  --app-bg-sticky: rgba(246, 246, 247, 0.95);
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

/* Global, not scoped: a parent's scoped styles never reach a child component's inner
   elements, so button chrome defined here would leave every `.ghost` in EntryDetail,
   Doctor, and FirstRun rendering as a default browser button. */
button {
  padding: 0.5rem 0.9rem;
  border-radius: 8px;
  border: 1px solid transparent;
  background: #3b82f6;
  color: #fff;
  font-weight: 500;
  font-family: inherit;
  font-size: 0.9rem;
  cursor: pointer;
}
button.ghost {
  background: transparent;
  color: inherit;
  border-color: rgba(128, 128, 128, 0.4);
}
button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Global so every view can ease its results in with one class, instead of each
   inventing its own keyframes. Content arriving after a subprocess is the whole
   app, so this is the default motion, not a flourish. */
.fade-in {
  animation: fade-in 0.22s ease-out;
}
@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .fade-in {
    animation: none;
  }
}
</style>

<style scoped>
.app {
  max-width: 860px;
  margin: 0 auto;
  /* No top padding: the sticky header carries its own, so the gap above the title
     stays part of the opaque surface instead of scrolling away from under it. */
  padding: 0 1.25rem 5rem;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 0.75rem 0;
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
.summary {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.85rem;
  opacity: 0.7;
  margin: 0.5rem 0 1rem;
}
.summary__toggle {
  display: flex;
  align-items: center;
  gap: 0.3rem;
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
