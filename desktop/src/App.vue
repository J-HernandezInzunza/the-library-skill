<script setup lang="ts">
import { ref, computed, defineAsyncComponent, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { allRows, catalogRows, winningRows, type Row } from "./catalog";
import { useCommandActivity, withActivity } from "./commandActivity";
import { describeAppError, isAppError, type Catalog, type Entry } from "./types";
import ActivityBar from "./components/ActivityBar.vue";
import Busy from "./components/Busy.vue";
import CatalogSummary from "./components/CatalogSummary.vue";
import CatalogTabs from "./components/CatalogTabs.vue";
import CommandLog from "./components/CommandLog.vue";
import EntryList from "./components/EntryList.vue";
import StatusBanner from "./components/StatusBanner.vue";

// Shown only on a machine that has never run the tool, so it stays out of the
// initial bundle everyone else loads.
const FirstRun = defineAsyncComponent(() => import("./components/FirstRun.vue"));

// Only reached by clicking into an entry, so it stays out of the initial bundle.
const EntryDetail = defineAsyncComponent(() => import("./components/EntryDetail.vue"));
const Doctor = defineAsyncComponent(() => import("./components/Doctor.vue"));
const Sync = defineAsyncComponent(() => import("./components/Sync.vue"));
const AddEntry = defineAsyncComponent(() => import("./components/AddEntry.vue"));
const Catalogs = defineAsyncComponent(() => import("./components/Catalogs.vue"));

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
/** The catalog an add is destined for; `null` closes the form. */
const addingTo = ref<string | null>(null);
/**
 * The catalog manager, and where inside it to land.
 *
 * Held here rather than inside the view so the detail page can hand off to a specific
 * entry: "edit this" has to arrive at the form, not at the top of a three-level view.
 */
const manage = ref<{ catalog: string | null; entry: string | null } | null>(null);
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
    const [loadedEntries, loadedCatalogs] = await withActivity("reading the catalog…", () =>
      Promise.all([invoke<Entry[]>("library_list"), invoke<Catalog[]>("registry_list")]),
    );
    entries.value = loadedEntries;
    catalogs.value = loadedCatalogs;
    // Only on a successful load: a failed one empties the list, and pruning against that
    // would discard the trail every time the CLI hiccups.
    pruneTrail(loadedEntries);
  } catch (e) {
    failure.value = e;
    entries.value = [];
    catalogs.value = [];
  } finally {
    loading.value = false;
  }
}

/**
 * Drop trail entries the catalog no longer has.
 *
 * Removing an entry from the catalog manager can delete the very name the detail view
 * behind it is showing, and Back would then run `show` against a name that is gone —
 * turning a successful removal into a failed command one click later.
 */
function pruneTrail(loaded: Entry[]) {
  const known = new Set(loaded.map((entry) => entry.name));
  trail.value = trail.value.filter((name) => known.has(name));
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

    <!-- Ordered so a view opened *from* another sits above it: closing Doctor or the add
         form falls back to whatever is still open underneath, with no state to restore. -->
    <Doctor
      v-else-if="showDoctor"
      :back-to="manage ? 'Catalogs' : 'The Library'"
      @close="showDoctor = false"
    />

    <Sync v-else-if="showSync" @close="showSync = false" @synced="load()" />

    <AddEntry
      v-else-if="addingTo"
      :catalog-id="addingTo"
      :catalogs="catalogs"
      :entries="entries"
      @close="addingTo = null"
      @added="load()"
    />

    <Catalogs
      v-else-if="manage"
      :catalogs="catalogs"
      :entries="entries"
      :at-catalog="manage.catalog"
      :at-entry="manage.entry"
      :back-to="openEntry ?? 'The Library'"
      @close="manage = null"
      @changed="load()"
      @add="addingTo = $event"
      @doctor="showDoctor = true"
      @navigate="manage = { catalog: $event, entry: null }"
    />

    <EntryDetail
      v-else-if="openEntry"
      :name="openEntry"
      :back-to="previousEntry"
      :catalogs="catalogs"
      :entries="entries"
      @close="trail.pop()"
      @open="trail.push($event)"
      @installed="load()"
      @manage="manage = { catalog: $event.catalog, entry: $event.name }"
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
        <!-- What you do to this list, and where you go. Adding an entry and checking
             catalog health both moved into Catalogs, which is their subject (D18). -->
        <button type="button" class="ghost" @click="load()">Refresh</button>
        <button
          type="button"
          class="ghost"
          @click="manage = { catalog: null, entry: null }"
        >
          Catalogs
        </button>
        <button type="button" class="ghost" @click="showSync = true">Sync</button>
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
    <StatusBanner v-else-if="errorMessage" kind="error" :detail="errorMessage" />
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
/* Reserve the scrollbar's width on every page, scrolling or not.

   Without this the app visibly breathes: `.app` is centred with `margin: 0 auto`, so a
   page long enough to scroll loses the scrollbar's width from the viewport and *both*
   edges move inward by half of it. Every navigation between a long view (the catalog, a
   catalog's entries) and a short one (Add, the registry) shifted the whole layout.

   It is invisible on a Mac set to overlay scrollbars, which reserve no space — so this is
   a defect that only some machines can see, which is the kind worth pinning rather than
   eyeballing. */
html {
  scrollbar-gutter: stable;
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
  /* Fast on purpose: this is the acknowledgement of the click itself, so it has to
     land in the same frame rather than easing in over the command's latency. */
  transition: transform 0.06s ease, opacity 0.15s ease, filter 0.15s ease;
}
button:active:not(:disabled) {
  transform: scale(0.97);
  filter: brightness(0.92);
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

/* Every full-screen view's root. Global rather than repeated per component because the
   whole point is that the views agree: five of them had drifted to three different
   paddings, so the header visibly shifted as you navigated between them. */
.view {
  padding: 1.5rem 0 3rem;
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
