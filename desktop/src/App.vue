<script setup lang="ts">
import { ref, computed, defineAsyncComponent, onMounted, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { allRows, catalogRows, isOnDisk, searchRows, winningRows, type Row } from "./catalog";
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
// Only reached from an entry page, and it pulls in the install preview and the setup report,
// so it stays out of the initial bundle.
const EntryInstall = defineAsyncComponent(() => import("./components/EntryInstall.vue"));
// Only reachable inside a catalog tab, so it stays out of the initial bundle.
const BulkInstall = defineAsyncComponent(() => import("./components/BulkInstall.vue"));
// Only reached by starting a setup walkthrough, and it pulls in the agent transcript machinery,
// so it stays well out of the initial bundle.
const Walkthrough = defineAsyncComponent(() => import("./components/Walkthrough.vue"));

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
/**
 * The skill a walkthrough is open for, or null.
 *
 * Held here rather than inside the entry page because the walkthrough is a full view, and one
 * that unmounted when you navigated would end the session behind your back — `Walkthrough` calls
 * `walkthrough_end` on unmount, deliberately.
 */
const walkingThrough = ref<string | null>(null);
/**
 * The entry whose install and setup page is open, or null (D23).
 *
 * Held here rather than inside the detail page for the same reason the walkthrough is: it is a
 * full view of its own, and the walkthrough opens *from* it, so both have to outlive the page
 * that launched them.
 */
const installFor = ref<string | null>(null);
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
/**
 * Names ticked for a bulk install, or null when not selecting.
 *
 * Only offered inside a catalog tab: there, "these entries" is unambiguous. In the
 * all-catalogs view a name can appear twice, and ticking the overridden copy would
 * promise a copy `use` will not install.
 */
const picked = ref<Set<string> | null>(null);

function togglePicked(name: string) {
  // Never a way *into* the mode. The list only emits this while selecting, so today the
  // guard is unreachable — which is exactly why it is worth having: the invariant should
  // hold here rather than depend on a child continuing to behave.
  if (!picked.value) return;

  const next = new Set(picked.value);
  if (!next.delete(name)) next.add(name);
  picked.value = next;
}

/** Every row in this tab that `use` would actually install. */
const selectable = computed(() =>
  filtered.value.filter((row) => !row.entry.overridden_by).map((row) => row.entry.name),
);
const pickedNames = computed(() =>
  selectable.value.filter((name) => picked.value?.has(name)),
);

function selectAll() {
  picked.value = new Set(selectable.value);
}

/** Enter or leave selection mode. Leaving discards the selection, which is its point. */
function setSelecting(on: boolean) {
  picked.value = on ? new Set() : null;
}

/**
 * A finished bulk action has consumed the selection, so it stops being selected.
 *
 * Selection mode stays on, because the list under it is still the one you were picking
 * from — and because `BulkInstall` owns the result banner, unmounting it here would
 * throw away the report of what just happened.
 */
function afterBulkAction() {
  picked.value = new Set();
  load();
}
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
  // The install page is about one name too, and a page whose entry the catalog no longer has is
  // a page whose every command would fail.
  if (installFor.value !== null && !known.has(installFor.value)) installFor.value = null;
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

/**
 * Whether the entry whose install page is open has a copy on this machine.
 *
 * Read from the catalog the app already holds, so an install on that page updates it: the page
 * itself reloads the list, and a boolean captured at navigation time would have left the setup
 * card hidden until the user navigated out and back.
 */
const installForOnDisk = computed(() => {
  const entry = entries.value.find((candidate) => candidate.name === installFor.value);
  return entry ? isOnDisk(entry.state) : false;
});

const multiCatalog = computed(() => catalogs.value.length > 1);

const selectedCatalog = computed(() => {
  const found = catalogs.value.find((catalog) => catalog.id === activeCatalog.value);
  return found ?? null;
});

/**
 * Changing tabs leaves selection mode entirely.
 *
 * `null`, not an empty Set: an empty Set *is* selection mode, so the earlier version
 * turned it on for every catalog tab the moment you switched to one, without anyone
 * asking. Mode is only ever entered by pressing the button that says so.
 */
watch(activeCatalog, () => {
  picked.value = null;
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

/** Case-insensitive search over name + description, name matches first, computed client-side. */
const filtered = computed(() => searchRows(rows.value, query.value));

const summary = computed(() => {
  const installed = filtered.value.filter(({ tone }) => tone === "installed").length;
  const overridden = filtered.value.filter(({ overriddenBy }) => overriddenBy !== null).length;

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
    <ActivityBar />

    <!-- Exactly one view is on screen, and every view is itself the window's frame: a chrome row
         that cannot scroll, one scrolling body, and — where the view has one — a second chrome row
         at the bottom. The command bar below is the app's last row, so a view's bottom chrome
         lands directly on it. -->
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

      <!-- Above the page it was opened from, so closing it lands back there. -->
      <Walkthrough
        v-else-if="walkingThrough"
        :skill="walkingThrough"
        :back-to="installFor ? 'Install and set up' : (openEntry ?? 'The Library')"
        @close="walkingThrough = null"
      />

      <!-- Above the entry page, and below the walkthrough it starts. -->
      <EntryInstall
        v-else-if="installFor"
        :name="installFor"
        :installed="installForOnDisk"
        :back-to="installFor"
        @close="installFor = null"
        @installed="load()"
        @walkthrough="walkingThrough = installFor"
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
        @install="installFor = $event"
      />

    <!-- The catalog list: the one view with nowhere to go back to, so its head is its title and
         what you do to the list rather than a back row. Adding an entry and checking catalog
         health both moved into Catalogs, which is their subject (D18). -->
    <section v-else class="view">
      <header class="view__head column">
        <h1>The Library</h1>
        <form class="searchbar" @submit.prevent>
          <input
            v-model="query"
            type="search"
            placeholder="Search skills, agents, prompts…"
          />
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

      <div class="view__body column">
        <CatalogTabs v-if="multiCatalog" v-model="activeCatalog" :catalogs="catalogs" />
        <CatalogSummary v-if="selectedCatalog" :catalog="selectedCatalog" />

        <p v-if="!loading && !errorMessage" class="summary">
          {{ summary }}
          <label v-if="activeCatalog === null && overriddenCount" class="summary__toggle">
            <input v-model="hideOverridden" type="checkbox" />
            Hide overridden
          </label>
          <!-- A missing control reads as a bug rather than a decision, so a tab where nothing
               would install says so instead of just not offering it. -->
          <span v-if="activeCatalog && !selectable.length && rows.length" class="summary__note">
            Nothing here can be installed: every copy is overridden by a higher-precedence
            catalog, so installing any of these names would fetch that catalog's copy instead.
          </span>
          <template v-if="activeCatalog && selectable.length">
            <button
              v-if="!picked"
              type="button"
              class="ghost summary__all"
              @click="setSelecting(true)"
            >
              Select
            </button>
            <template v-else>
              <button
                type="button"
                class="ghost summary__all"
                @click="pickedNames.length === selectable.length ? (picked = new Set()) : selectAll()"
              >
                {{ pickedNames.length === selectable.length ? "Select none" : `Select all ${selectable.length}` }}
              </button>
              <button
                v-if="pickedNames.length"
                type="button"
                class="ghost summary__clear"
                @click="picked = new Set()"
              >
                Clear
              </button>
              <button type="button" class="ghost summary__done" @click="setSelecting(false)">
                Stop selecting
              </button>
            </template>
          </template>
        </p>

        <!-- Rendered for the whole of selection mode, not just while something is ticked: it
             owns the success banner, and the install clears the selection that produced it. -->
        <BulkInstall
          v-if="activeCatalog && picked"
          :names="pickedNames"
          :catalog-id="activeCatalog"
          @installed="afterBulkAction()"
          @uninstalled="afterBulkAction()"
        />

        <Busy v-if="loading" label="Reading the catalog…" />
        <StatusBanner v-else-if="errorMessage" kind="error" :detail="errorMessage" />
        <p v-else-if="!filtered.length" class="state">No matching entries.</p>
        <EntryList
          v-else
          class="fade-in"
          :rows="filtered"
          :catalogs="catalogs"
          :show-origin="multiCatalog"
          :selected="picked"
          @select="trail = [$event]"
          @toggle="togglePicked($event)"
        />
      </div>
    </section>

    <!-- The app's last row, under whichever view is on screen: in the flow, so it is the bottom
         of the window by construction and nothing can move it. -->
    <CommandLog />
  </main>
</template>

<style>
:root {
  color-scheme: light dark;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, sans-serif;
  --app-bg: #f6f6f7;
  /* For the one thing that still floats over content: the activity bar's label. It needs a
     surface of its own, since a bare backdrop-filter leaves the text overlapping the view. */
  --app-bg-sticky: rgba(246, 246, 247, 0.95);
}
/* The window is a fixed frame; only a view's `.view__body` inside it scrolls (D22).

   The document itself must not, and `overflow: hidden` here is what says so. A scrolling
   document put the app at the mercy of the WebView's rubber-band overscroll: the whole
   page could be dragged away from both ends, and every `position: fixed` element — the
   command bar, the walkthrough's composer — travelled with it, so the bar that is
   supposed to be the bottom of the window visibly left it. */
html,
body,
#app {
  height: 100%;
}
body {
  margin: 0;
  overflow: hidden;
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
/* Global for the same reason `.ghost` is, and because it had already drifted: the catalog
   manager styled its Remove red from a component-local rule while the entry page left the
   identical action looking like every other button. One destructive style, one place. */
button.danger {
  background: transparent;
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.45);
}
button.danger:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.1);
}
button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* The surface a section's content sits on. Global because it is the thing that makes the
   page read as grouped: "On this machine", "Catalogs holding this name", and "Required by"
   each had one and Source and Install did not, so those two read as loose text between
   grouped blocks rather than as sections of their own. */
.card {
  padding: 0.7rem 0.85rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}

/* The content column, as padding rather than as a centred box. A view's three parts are three
   rows of the window and each has to be full-bleed — the head's hairline and the foot's border
   go edge to edge — while their *content* lines up on one measure. A `max-width` box cannot do
   both, and the earlier one had the rows drifting a rem apart from each other. */
.column {
  padding-inline: max(1.25rem, calc((100% - 860px) / 2));
}

/* Every full-screen view is the window's frame (D22).
 *
 * Three rows: the view's own chrome, the one thing in it that scrolls, and — where the view has
 * one — a second chrome row at the bottom. The rows are placed explicitly rather than by source
 * order, because a view's conditional siblings (a status banner, a branch that swaps the whole
 * page) would otherwise decide which row the body lands in.
 *
 * This is what makes the chrome unscrollable rather than merely sticky. A row that is not inside
 * the scroller cannot be scrolled at all; sticky pins an element only while its containing block
 * is in view, and `fixed` rode the WebView's overscroll, which is how the command bar used to
 * leave the bottom of the window.
 */
.view {
  /* The app's first row, and a grid of its own. Sized by that track rather than by a
     `height: 100%` — a percentage against an auto-sized track is cyclic, so the view grew to its
     content instead of to the window: nothing scrolled, and the foot row landed under the command
     bar, which paints over it. `min-height: 0` for the same reason the body needs it, one level
     up: a `1fr` track's floor is the item's min-content unless the item says otherwise. */
  grid-row: 1;
  min-height: 0;
  display: grid;
  grid-template-rows: auto 1fr auto;
}
.view__head {
  grid-row: 1;
  padding-block: 0.75rem;
  /* Opaque, and a hairline: content is clipped at this edge rather than scrolling under it, and
     the line is what makes the edge read as the frame of the window instead of a cut-off row. */
  background: var(--app-bg);
  border-bottom: 1px solid rgba(128, 128, 128, 0.18);
}
.view__body {
  grid-row: 2;
  /* Without this the `1fr` track refuses to shrink below its content — a grid track's floor is
     min-content — and the view grows past the window instead of this row scrolling. */
  min-height: 0;
  overflow-y: auto;
  /* Keep a scroll that reaches either end from becoming the window's own overscroll: the
     rubber-band that used to drag the whole layout, taking every fixed element with it. */
  overscroll-behavior: contain;
  /* Reserve the scrollbar's width whether or not this body is scrolling. Without it the app
     visibly breathes: the column is centred, so a view long enough to scroll loses the
     scrollbar's width and *both* edges move inward by half of it. Invisible on a Mac set to
     overlay scrollbars, which is the kind of defect worth pinning rather than eyeballing. */
  scrollbar-gutter: stable;
  /* The padding five views had each written for themselves, in three different values, so the
     header visibly shifted as you navigated between them. */
  padding-block: 1.25rem 2rem;
}
.view__foot {
  grid-row: 3;
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
/* Two rows: whichever view is on screen, then the command bar. Exactly the window tall, so the
   bar is the bottom of the window by construction rather than by positioning itself there (D22).
   The view fills the first row and provides its own chrome rows inside it. */
.app {
  height: 100%;
  display: grid;
  grid-template-rows: 1fr auto;
}
/* A grid of its own so the single view inside it is stretched to the row rather than sized by
   its content, and `min-height: 0` so that row can be shorter than what the view holds — which
   is what lets the view's body scroll instead of the window growing. */

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
  /* Reserve the height of the tallest variant — the two-line "nothing can be installed"
     note on a fully-overridden catalog — so switching tabs does not shift the list up
     and down under it. Content stays vertically centred in the reserved space. */
  min-height: 2.5rem;
}
.summary__toggle {
  /* Pinned right so it stays put when the counts text changes width on toggle — it
     otherwise scooted left as "7 overridden" appeared and disappeared. */
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.summary__all {
  margin-left: auto;
  padding: 0.2rem 0.5rem;
  font-size: 0.75rem;
}
.summary__clear,
.summary__done {
  padding: 0.2rem 0.5rem;
  font-size: 0.75rem;
}
.summary__note {
  flex: 1;
  font-size: 0.75rem;
  line-height: 1.4;
  opacity: 0.75;
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
