<script setup lang="ts">
import {
  ref,
  computed,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
  watch,
} from "vue";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  allRows,
  catalogRows,
  editableCatalogs,
  isOnDisk,
  searchRows,
  winningRows,
  type Row,
} from "./catalog";
import { useCommandActivity, withActivity } from "./commandActivity";
import { tabCatalog, type Tab } from "./tabs";
import {
  describeAppError,
  isAppError,
  type Catalog,
  type Entry,
  type EntryRef,
  type InstallSource,
} from "./types";
import ActivityBar from "./components/ActivityBar.vue";
import Busy from "./components/Busy.vue";
import CatalogSummary from "./components/CatalogSummary.vue";
import CatalogTabs from "./components/CatalogTabs.vue";
import CommandLog from "./components/CommandLog.vue";
import EntryList from "./components/EntryList.vue";
import StatusBanner from "./components/StatusBanner.vue";
import Toasts from "./components/Toasts.vue";

// Shown only on a machine that has never run the tool, so it stays out of the
// initial bundle everyone else loads.
const FirstRun = defineAsyncComponent(
  () => import("./components/FirstRun.vue"),
);

// Only reached by clicking into an entry, so it stays out of the initial bundle.
const EntryDetail = defineAsyncComponent(
  () => import("./components/EntryDetail.vue"),
);
const Doctor = defineAsyncComponent(() => import("./components/Doctor.vue"));
const Sync = defineAsyncComponent(() => import("./components/Sync.vue"));
const AddEntry = defineAsyncComponent(
  () => import("./components/AddEntry.vue"),
);
const Catalogs = defineAsyncComponent(
  () => import("./components/Catalogs.vue"),
);
// Only reached from an entry page, and it pulls in the install preview and the setup report,
// so it stays out of the initial bundle.
const EntryInstall = defineAsyncComponent(
  () => import("./components/EntryInstall.vue"),
);
// Only reachable inside a catalog tab, so it stays out of the initial bundle.
const BulkInstall = defineAsyncComponent(
  () => import("./components/BulkInstall.vue"),
);
// Only reached by starting a setup walkthrough, and it pulls in the agent transcript machinery,
// so it stays well out of the initial bundle.
const Walkthrough = defineAsyncComponent(
  () => import("./components/Walkthrough.vue"),
);

// Attached here, at the earliest point in the app, so the command log and the activity
// bar are subscribed before anything can run.
const { listening } = useCommandActivity();

const entries = ref<Entry[]>([]);
const catalogs = ref<Catalog[]>([]);
/** The catalog being browsed; `null` browses every catalog's winning entries. */
const tab = ref<Tab>({ kind: "all" });

/**
 * The catalog being browsed, or null on a tab that is not one catalog.
 *
 * Derived rather than stored so there is one source for which tab is showing. Everything
 * that only makes sense inside a single catalog — the bulk-install controls, the
 * "nothing here can be installed" note — still reads this and is unchanged by the
 * disabled tab arriving beside them.
 */
const activeCatalog = computed(() => tabCatalog(tab.value));
const query = ref("");
/**
 * The entries clicked into, most recent last.
 *
 * A trail rather than a single name so Back returns to where you came from: opening a
 * dependency from a detail view and landing back on the full catalog loses your place
 * exactly when you are walking a dependency chain.
 *
 * Each stop carries its catalog, because a name is not an identity: the list shows a row
 * per copy, and clicking the overridden one used to open the winner — the same page you
 * would have got from the row above it.
 */
const trail = ref<EntryRef[]>([]);
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
const manage = ref<{ catalog: string | null; entry: string | null } | null>(
  null,
);
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
  filtered.value
    .filter((row) => !row.entry.overridden_by)
    .map((row) => row.entry.name),
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

/**
 * When the catalogs were last pulled, or null before the first one lands.
 *
 * Shown because the app no longer refreshes on every read: with the pull moved to start,
 * Refresh, and Sync, "how old is this?" stops being answerable by assuming it is current,
 * and an unanswered version of that question is how someone ends up looking at a catalog
 * a teammate changed an hour ago without knowing it.
 */
const lastPulled = ref<Date | null>(null);

/**
 * Loading with nothing on screen yet, which is the only time a spinner should replace
 * anything.
 *
 * Every other load is a refresh over a list that is already there, and swapping it for a
 * spinner is what made a toggle look like a page reload — the counts line and the rows
 * both vanished and came back, and everything below them moved twice.
 */
const firstLoad = computed(() => loading.value && !entries.value.length);
/** Kept typed rather than stringified: a first-run state is recoverable, not an error. */
const failure = ref<unknown>(null);

/**
 * True when the app was opened as a plain web page (`npm run dev`) rather than through Tauri
 * (`npm run tauri dev`). Every command runs over IPC to the Rust backend, which only exists in
 * the latter; without it `invoke` has no bridge to call and the catalog can never load. Checked
 * once up front so a developer sees the one command that fixes it, rather than a spinner that
 * never resolves or a bare `__TAURI_INTERNALS__` error.
 */
const browserOnly = !isTauri();

/**
 * Load the catalog and the registry; search and tabs work off that payload.
 *
 * `pull` decides whether the CLI refreshes its catalog clones first. Every read used to,
 * which put a `git pull --ff-only` per remote catalog in front of every command: 0.75s of
 * the 0.88s a `library list` took, on a machine with a good connection, for an action as
 * local as flipping a skill off. The pull now happens where the user is asking for
 * freshness — app start and Sync — and everything else reads the clone that is already
 * on disk (0.14s).
 */
async function load({ pull = false } = {}) {
  // Nothing to load without the backend; leaving loading true here is what strands the spinner.
  if (browserOnly) {
    loading.value = false;
    return;
  }
  loading.value = true;
  failure.value = null;
  try {
    const [loadedEntries, loadedCatalogs] = await withActivity(
      pull ? "refreshing the catalog…" : "reading the catalog…",
      () =>
        Promise.all([
          invoke<Entry[]>("library_list", { noPull: !pull }),
          invoke<Catalog[]>("registry_list"),
        ]),
    );
    entries.value = loadedEntries;
    catalogs.value = loadedCatalogs;
    if (pull) lastPulled.value = new Date();
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
  // Keyed by name, not by copy: a stop whose own catalog dropped the entry is still a
  // page the CLI can answer, from whichever catalog still defines the name.
  trail.value = trail.value.filter((stop) => known.has(stop.name));
  // The install page is about one name too, and a page whose entry the catalog no longer has is
  // a page whose every command would fail.
  if (installFor.value !== null && !known.has(installFor.value))
    installFor.value = null;
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
  const entry = entries.value.find(
    (candidate) => candidate.name === installFor.value,
  );
  return entry ? isOnDisk(entry.state) : false;
});

/**
 * Whether that same open entry ships a setup manifest, read from the same catalog snapshot
 * as `installForOnDisk` so the two never disagree: after an install they refresh together,
 * and the readiness card can trust that `hasSetup: false` truly means nothing to read.
 */
const installForHasSetup = computed(() => {
  const entry = entries.value.find(
    (candidate) => candidate.name === installFor.value,
  );
  return entry?.has_setup ?? false;
});

/**
 * The catalogs the open install page could install that name from, in resolution order.
 *
 * Empty when only one catalog defines the name, which is the signal the install page uses to
 * stay a one-button page: a picker with a single option is a setting the user is failing to use.
 * Read from the same snapshot as `installForOnDisk`, so a pin made on the detail page lands here
 * as soon as the list reloads.
 */
const installForSources = computed<InstallSource[]>(() => {
  const copies = entries.value.filter(
    (candidate) => candidate.name === installFor.value,
  );
  if (copies.length < 2) return [];
  return copies.map((copy) => ({
    catalog: copy.catalog,
    resolves: !copy.overridden_by,
    pinned: copy.pinned,
  }));
});

const multiCatalog = computed(() => catalogs.value.length > 1);

const selectedCatalog = computed(() => {
  const found = catalogs.value.find(
    (catalog) => catalog.id === activeCatalog.value,
  );
  return found ?? null;
});

/**
 * The catalogs this app will write to, which is what the shortcut into the manager is gated on.
 *
 * Deliberately the *same* rule the registry applies to its own "Manage entries" button, because
 * the page they both open renders Edit and Remove on every row without checking: it can only do
 * that while every door into it is gated, and a shortcut is a new door.
 */
const editableIds = computed(
  () => new Set(editableCatalogs(catalogs.value).map((catalog) => catalog.id)),
);

/**
 * Changing tabs leaves selection mode entirely.
 *
 * `null`, not an empty Set: an empty Set *is* selection mode, so the earlier version
 * turned it on for every catalog tab the moment you switched to one, without anyone
 * asking. Mode is only ever entered by pressing the button that says so.
 */
watch(tab, () => {
  picked.value = null;
});

const rows = computed<Row[]>(() => {
  // Winners only: a switched-off copy is by definition the one that resolved, and showing
  // the copies it beats under a tab about install state would be answering the other
  // question. `disabled` is what the CLI reports, not something derived here.
  if (tab.value.kind === "disabled") {
    return winningRows(entries.value).filter(
      (row) => row.entry.state === "disabled",
    );
  }
  if (tab.value.kind === "catalog")
    return catalogRows(entries.value, tab.value.id);
  if (hideOverridden.value) return winningRows(entries.value);
  return allRows(entries.value);
});

/** Drives the disabled tab, counted across every catalog rather than the rows on screen. */
const disabledCount = computed(
  () => entries.value.filter((entry) => entry.state === "disabled").length,
);
/**
 * Leave a tab that has stopped existing.
 *
 * The disabled tab is the only one you can empty from inside it: switching the last skill
 * back on takes the rows away and the tab button with them, which left the app holding a
 * selection that had no button in the strip and an empty list that said nothing about why.
 * A catalog tab goes the same way when its catalog is unregistered.
 *
 * Guarded on having entries at all, because a failed load empties them too — and reading
 * that as "nothing is disabled any more" would move the user off the tab they were on
 * while the banner in front of them is still waiting to be retried.
 */
watch([disabledCount, catalogs], () => {
  if (!entries.value.length) return;

  const current = tab.value;
  let gone = false;
  if (current.kind === "disabled") {
    gone = disabledCount.value === 0;
  } else if (current.kind === "catalog") {
    gone = !catalogs.value.some((catalog) => catalog.id === current.id);
  }

  if (gone) tab.value = { kind: "all" };
});

/** Only worth offering once something is actually being overridden. */
const overriddenCount = computed(
  () => entries.value.filter((entry) => entry.overridden_by).length,
);

/** Case-insensitive search over name + description, name matches first, computed client-side. */
const filtered = computed(() => searchRows(rows.value, query.value));

const summary = computed(() => {
  // Counted off the CLI's own flag and state rather than the row's tone: `installed` means
  // the content is on this device, so a disabled copy is still installed and is counted in
  // both parts.
  const installed = filtered.value.filter(
    ({ entry }) => entry.installed,
  ).length;
  const disabled = filtered.value.filter(
    ({ entry }) => entry.state === "disabled",
  ).length;
  const overridden = filtered.value.filter(
    ({ overriddenBy }) => overriddenBy !== null,
  ).length;

  const parts = [
    `${filtered.value.length} of ${rows.value.length} entries`,
    `${installed} installed`,
  ];
  if (disabled) parts.push(`${disabled} disabled`);
  if (overridden) parts.push(`${overridden} overridden`);
  return parts.join(" · ");
});

/**
 * Ticks once a minute so the freshness cue ages on screen.
 *
 * A timestamp rendered once would be a lie within the minute, and "how old is this?" is
 * the one question the cue exists to answer.
 */
const now = ref(new Date());
const ticking = setInterval(() => (now.value = new Date()), 60_000);
onUnmounted(() => clearInterval(ticking));

/** How long ago the catalogs were pulled, or null until the first pull lands. */
const freshness = computed(() => {
  if (!lastPulled.value) return null;
  const minutes = Math.floor(
    (now.value.getTime() - lastPulled.value.getTime()) / 60_000,
  );
  if (minutes < 1) return "refreshed just now";
  if (minutes === 1) return "refreshed 1 minute ago";
  if (minutes < 60) return `refreshed ${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  return hours === 1 ? "refreshed 1 hour ago" : `refreshed ${hours} hours ago`;
});

/** A sync already pulled every clone, so the read back is local and still counts as fresh. */
async function afterSync() {
  lastPulled.value = new Date();
  await load();
}

onMounted(async () => {
  // The first command must appear in the log like every other one, so it waits for the
  // subscription rather than racing it.
  await listening;
  // The one read that pulls: opening the app is the moment the user is asking to see
  // what the catalogs hold now. Every read after this one goes to the clone on disk
  // until they press Refresh or Sync.
  await load({ pull: true });
});
</script>

<template>
  <main class="app">
    <ActivityBar />

    <!-- Exactly one view is on screen, and every view is itself the window's frame: a chrome row
         that cannot scroll, one scrolling body, and — where the view has one — a second chrome row
         at the bottom. The command bar below is the app's last row, so a view's bottom chrome
         lands directly on it. -->
    <!-- Opened in a browser instead of through Tauri: the backend isn't there, so this stands in
         for every view and names the command that starts it, rather than letting the rest of the
         app render against a catalog that can never load. -->
    <section v-if="browserOnly" class="view">
      <div class="view__body column stack">
        <StatusBanner kind="warning">
          <strong>The backend isn't running.</strong>
          This window is the frontend on its own — the Rust backend it reads the
          catalog from only runs when you launch through Tauri. Stop this, then
          start it with
          <code>npm run tauri dev</code> from the
          <code>desktop</code> directory.
        </StatusBanner>
      </div>
    </section>

    <FirstRun
      v-else-if="setupNeeded"
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

    <!-- No pull on the read back: a sync has just refreshed every clone, so the copy on
           disk is the fresh one and pulling again would be a second round trip for it. -->
    <Sync
      v-else-if="showSync"
      @close="showSync = false"
      @synced="afterSync()"
    />

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
      :back-to="openEntry?.name ?? 'The Library'"
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
      :back-to="
        installFor ? 'Install and set up' : (openEntry?.name ?? 'The Library')
      "
      @close="walkingThrough = null"
    />

    <!-- Above the entry page, and below the walkthrough it starts. -->
    <EntryInstall
      v-else-if="installFor"
      :name="installFor"
      :installed="installForOnDisk"
      :has-setup="installForHasSetup"
      :sources="installForSources"
      :back-to="installFor"
      @close="installFor = null"
      @installed="load()"
      @walkthrough="walkingThrough = installFor"
    />

    <EntryDetail
      v-else-if="openEntry"
      :name="openEntry.name"
      :catalog="openEntry.catalog"
      :back-to="previousEntry?.name ?? null"
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
          <!-- Explicitly asking for fresh, so this is a pull: the ambient reads are not. -->
          <button type="button" class="ghost" @click="load({ pull: true })">
            Refresh
          </button>
          <button
            type="button"
            class="ghost"
            @click="manage = { catalog: null, entry: null }"
          >
            Catalogs
          </button>
          <button type="button" class="ghost" @click="showSync = true">
            Sync
          </button>
        </form>
      </header>

      <div class="view__body column stack">
        <!-- Shown for the disabled tab too, so a single-catalog setup still gets somewhere
             to find what it has switched off. -->
        <CatalogTabs
          v-if="multiCatalog || disabledCount"
          v-model="tab"
          :catalogs="catalogs"
          :disabled-count="disabledCount"
        />
        <!-- Lands straight on that catalog's entries rather than on the registry. `Catalogs`
             already handles the arrival: a non-null `atCatalog` opens at the second level and
             sends Back to the page named here rather than to a list the user never visited. -->
        <CatalogSummary
          v-if="selectedCatalog"
          :catalog="selectedCatalog"
          :manageable="editableIds.has(selectedCatalog.id)"
          @manage="manage = { catalog: activeCatalog, entry: null }"
        />

        <p v-if="!firstLoad && !errorMessage" class="summary">
          {{ summary }}
          <!-- Said out loud because the app stopped pulling on every read: while it did,
               "current" was a safe assumption and needed no cue. It is not any more, and
               an unanswered "how old is this?" is how someone reads a catalog a teammate
               changed an hour ago without knowing it. -->
          <span v-if="freshness" class="summary__freshness">{{
            freshness
          }}</span>
          <label
            v-if="activeCatalog === null && overriddenCount"
            class="summary__toggle"
          >
            <input v-model="hideOverridden" type="checkbox" />
            Hide overridden
          </label>
          <!-- A missing control reads as a bug rather than a decision, so a tab where nothing
               would install says so instead of just not offering it. -->
          <span
            v-if="activeCatalog && !selectable.length && rows.length"
            class="summary__note"
          >
            Nothing here can be installed: every copy is overridden by a
            higher-precedence catalog, so installing any of these names would
            fetch that catalog's copy instead.
          </span>
          <!-- The right-pin belongs to the group, not to a button in it: it sat on "Select
               all", which is hidden once everything is ticked, and the buttons left behind
               slid back into the counts text. -->
          <span
            v-if="activeCatalog && selectable.length"
            class="summary__actions"
          >
            <button
              v-if="!picked"
              type="button"
              class="ghost btn-xs"
              @click="setSelecting(true)"
            >
              Install or remove several
            </button>
            <template v-else>
              <!-- One job each. This was a "Select all"/"Select none" toggle sitting next to
                   Clear, and with everything ticked the toggle and Clear were the same button
                   twice: both emptied the selection. -->
              <button
                v-if="pickedNames.length < selectable.length"
                type="button"
                class="ghost btn-xs"
                @click="selectAll()"
              >
                Select all {{ selectable.length }}
              </button>
              <button
                v-if="pickedNames.length"
                type="button"
                class="ghost btn-xs"
                @click="picked = new Set()"
              >
                Clear
              </button>
              <button
                type="button"
                class="ghost btn-xs"
                @click="setSelecting(false)"
              >
                Stop selecting
              </button>
            </template>
          </span>
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

        <!-- Only the *first* load replaces the list, because only then is there no list to
             show. A refresh keeps the rows on screen and marks them as being re-read: the
             list used to unmount on every refetch, so flipping one switch blanked all 42
             rows, ran a spinner, and faded the whole list back in to show one row changed. -->
        <Busy v-if="firstLoad" label="Reading the catalog…" />
        <StatusBanner
          v-else-if="errorMessage"
          kind="error"
          :detail="errorMessage"
        />
        <!-- Two different nothings, told apart. They were one sentence on the grounds that
             the next action was the same, and it is not: a search that matched nothing is
             fixed by changing the search, and an empty catalog is fixed by putting something
             in it. Reading "No matching entries" under an empty catalog and an empty search
             box is what sent someone hunting for a filter they had not set. -->
        <div v-else-if="!filtered.length" class="state">
          <template v-if="rows.length">
            <p class="state__line">
              Nothing here matches <strong>{{ query }}</strong
              >.
            </p>
            <button type="button" class="ghost" @click="query = ''">
              Clear the search
            </button>
          </template>
          <template v-else-if="activeCatalog">
            <p class="state__line">{{ activeCatalog }} has no entries yet.</p>
            <!-- Same gate as the strip above it, and the same reason: this opens a form that
                 writes to the catalog file. -->
            <button
              v-if="editableIds.has(activeCatalog)"
              type="button"
              class="ghost"
              @click="addingTo = activeCatalog"
            >
              Add the first one
            </button>
          </template>
          <p v-else class="state__line">
            No catalog holds an entry yet. <strong>Catalogs</strong> above is
            where you add one.
          </p>
        </div>
        <EntryList
          v-else
          :class="{ 'is-refreshing': loading }"
          :rows="filtered"
          :catalogs="catalogs"
          :show-origin="multiCatalog"
          :selected="picked"
          @select="trail = [$event]"
          @toggle="togglePicked($event)"
          @changed="load()"
        />
      </div>
    </section>

    <!-- Over the view rather than in it: a notice that took part in the layout pushed the
         list down as it arrived and pulled it back as it went. -->
    <Toasts />

    <!-- The app's last row, under whichever view is on screen: in the flow, so it is the bottom
         of the window by construction and nothing can move it. -->
    <CommandLog />
  </main>
</template>

<style>
:root {
  font-family:
    -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, sans-serif;
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
  background: var(--surface-page);
  color: var(--text-primary);
}

/* Global, not scoped: a parent's scoped styles never reach a child component's inner
   elements, so button chrome defined here would leave every `.ghost` in EntryDetail,
   Doctor, and FirstRun rendering as a default browser button. */
button {
  padding: 0.5rem 0.9rem;
  border-radius: 8px;
  border: 1px solid transparent;
  background: var(--accent-bright);
  color: var(--text-on-accent);
  font-weight: 500;
  font-family: inherit;
  font-size: 0.9rem;
  cursor: pointer;
  /* Fast on purpose: this is the acknowledgement of the click itself, so it has to
     land in the same frame rather than easing in over the command's latency. */
  transition:
    transform 0.06s ease,
    opacity 0.15s ease,
    filter 0.15s ease;
}
button:active:not(:disabled) {
  transform: scale(0.97);
  filter: brightness(0.92);
}
/* Two steps down from the default, keyed to what the button is attached to rather than to
   how big it should look: `.btn-sm` for an action on a row or card inside a view, `.btn-xs`
   for one sitting inside a line of running text. The default is a view's own action.

   Global for the same reason `.ghost` is, and against the same drift: this replaces eleven
   sizes spread over thirteen components, which is what the detail page was showing — the
   copy actions and the installed-copy actions sat 0.03rem of type and 0.05rem of padding
   apart, close enough to read as a rendering fault rather than as a distinction.

   Radius is deliberately not among them. Four values were in use on button-shaped buttons
   (8px, 6px, 0.35rem, 0.25rem) with nothing to tell them apart, so the base 8px is now the
   only one; the genuinely bespoke shapes (the switch, the tabs, the log bar) keep theirs. */
button.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.78rem;
}
button.btn-xs {
  padding: 0.2rem 0.5rem;
  font-size: 0.72rem;
}
button.ghost {
  background: transparent;
  color: inherit;
  border-color: var(--border-control);
}
/* Global for the same reason `.ghost` is, and because it had already drifted: the catalog
   manager styled its Remove red from a component-local rule while the entry page left the
   identical action looking like every other button. One destructive style, one place. */
button.danger {
  background: transparent;
  color: var(--status-danger-ink);
  border-color: var(--status-danger-edge);
}
button.danger:hover:not(:disabled) {
  background: var(--status-danger-tint);
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
  background: var(--surface-raised);
}

/* The content column, as padding rather than as a centred box. A view's three parts are three
   rows of the window and each has to be full-bleed — the head's hairline and the foot's border
   go edge to edge — while their *content* lines up on one measure. A `max-width` box cannot do
   both, and the earlier one had the rows drifting a rem apart from each other. */
.column {
  padding-inline: max(1.25rem, calc((100% - 860px) / 2));
}

/* A page's vertical rhythm, owned by the stack rather than by the things in it.

   The space between two rows is a fact about the page, not about either row, and every
   element that decided its own ended up deciding a different one: the catalog view's tabs
   and its precedence strip each carried 0.75rem, the counts line under them 0.5rem and a
   2.5rem reserve it centred its text in, and the banner that renders between them 1.25rem.
   Four opinions, so the strip sat 12px under the tabs and 24px above the counts — the same
   element looking mispositioned because nothing on the page was measuring against anything
   else.

   `gap` rather than margins on purpose: the rows here are almost all conditional, and a gap
   only exists between rows that rendered. Margins had to be on one side to avoid doubling
   up, which is why every one of them was a bottom margin, and a bottom margin is the wrong
   shape for this — the last row in the stack was paying for a neighbour it did not have. */
.view__body.stack {
  display: flex;
  flex-direction: column;
  /* The step, declared once. Not a custom property: this rule is already the only place
     that decides it, and tokens.css — where a token would have to be declared for
     `tokens.spec` to accept it — is the colour vocabulary and says so in its first line. */
  gap: 0.75rem;
}
/* Two classes deep so this beats a row's own rule whatever order the styles load in.

   Aimed at the outer margins only: a row still owns everything inside it. Shared rows keep
   their margins for the pages that are not stacks yet — this says the stack does not want
   them, rather than taking them away from elsewhere. */
.view__body.stack > * {
  margin-block: 0;
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
  background: var(--surface-page);
  border-bottom: 1px solid var(--border-subtle);
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
  padding-block: 0.5rem 1.5rem;
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
/* A refresh over a list that is already on screen. Deliberately not the fade: an entrance
   animation replayed on every refetch reads as the list being rebuilt, which is exactly
   the impression to avoid when all that changed is one row.

   The dim is delayed and the recovery is not, so the two refresh speeds get the treatment
   each deserves: a local read returns in ~0.14s, well inside the delay, so nothing visibly
   happens at all and the row simply updates; a pull takes ~0.9s and dims, because at that
   length silence reads as a hang. A symmetric transition would hold the list dim for the
   delay *after* it finished, which is the flicker this is avoiding. */
.is-refreshing {
  opacity: 0.6;
  transition: opacity 0.12s ease 0.4s;
  /* The rows are one command away from being replaced, so a click now would act on a row
     that is already stale. */
  pointer-events: none;
}
.entry-list {
  transition: opacity 0.12s ease;
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
  .is-refreshing {
    transition: none;
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
  /* The containing block for the toast stack, which is absolutely positioned into row 1
     so it can sit over the view without its width feeding the column's sizing. */
  position: relative;
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
  border: 1px solid var(--border-control);
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
}
.summary__toggle {
  /* Pinned right so it stays put when the counts text changes width on toggle — it
     otherwise scooted left as "7 overridden" appeared and disappeared. */
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.summary__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.summary__freshness {
  /* Reads as a footnote to the counts, not as another count: it is about the data's age,
     not its contents. Dimmer than the `· ` separated parts beside it for that reason. */
  opacity: 0.5;
  font-size: 0.75rem;
}
.summary__freshness::before {
  content: "· ";
}
.summary__note {
  flex: 1;
  font-size: 0.75rem;
  line-height: 1.4;
  opacity: 0.75;
}
.state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.7rem;
  padding: 2rem 0;
  text-align: center;
  opacity: 0.8;
}
.state__line {
  margin: 0;
}
.state.error {
  text-align: left;
  color: var(--status-danger-ink);
  white-space: pre-wrap;
  background: var(--status-danger-tint);
  padding: 1rem;
  border-radius: 8px;
}
</style>
