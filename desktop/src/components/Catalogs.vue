<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { catalogHue, describeCatalog, editableCatalogs } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type Catalog,
  type CatalogSource,
  type Entry,
  type Pin,
  type UnpinReport,
  type UnregisterReport,
} from "../types";
import EntryEditor from "./EntryEditor.vue";
import EntryRemove from "./EntryRemove.vue";
import PageHeader from "./PageHeader.vue";
import RegisterCatalog from "./RegisterCatalog.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The registry, which decides what can be managed and in what order. */
  catalogs: Catalog[];
  /** The loaded catalog: one record per copy, so a catalog's inventory is a filter. */
  entries: Entry[];
  /** Open straight at a catalog, and optionally an entry, when arriving from elsewhere. */
  atCatalog?: string | null;
  atEntry?: string | null;
  /** The title of the page leaving this one returns to, which is not always the catalog. */
  backTo: string;
}>();
const emit = defineEmits<{
  close: [];
  changed: [];
  /** Add an entry to the catalog currently being managed. */
  add: [catalog: string];
  /** `doctor` validates config and catalog integrity, which is this view's subject. */
  doctor: [];
  /**
   * Which catalog is open, reported up as it changes.
   *
   * Opening the add form or the health report *unmounts* this view, so its own level would
   * be lost and it would come back at the registry. The parent holds the position instead,
   * and hands it back as `atCatalog` on the next mount.
   */
  navigate: [catalog: string | null];
}>();

/** Which form a row is showing. One row, one form, app-wide. */
type Panel = { name: string; mode: "edit" | "remove" };

const openCatalog = ref<string | null>(props.atCatalog ?? null);

/**
 * True while the view is sitting on a catalog it was *opened at* rather than navigated to.
 *
 * Back has to mean "where I came from", and for a hand-off from an entry's detail page that
 * is the entry — not the registry. Without this the entry page's "Edit this entry in …"
 * button led to a form whose Back went to a list of catalogs the user had never visited,
 * and only the *second* Back returned to the entry. A level the user did not walk through
 * is not a level they should have to walk back out of.
 */
const arrivedHere = ref(!!props.atCatalog);
/**
 * The single open panel.
 *
 * Deliberately one value rather than a flag per row and per mode: edit and remove are
 * alternatives, not layers, and two destructive-adjacent forms open at once is a way to
 * confirm the wrong one. Being unable to represent that state is a stronger guarantee
 * than closing the other one on every click.
 */
const panel = ref<Panel | null>(null);

/** Catalogs whose entries this app will edit, which is the ones on this machine. */
const editableIds = computed(
  () => new Set(editableCatalogs(props.catalogs).map((catalog) => catalog.id)),
);

const catalog = computed(() => props.catalogs.find((c) => c.id === openCatalog.value) ?? null);

/** True when nothing on this machine can be written to, which needs saying out loud. */
const nothingEditable = computed(() => editableIds.value.size === 0);

/**
 * Whether there is no catalog of your own at all — the case the page has to teach rather
 * than merely report.
 *
 * Split from `nothingEditable`, which was answering two questions with one sentence: a
 * local catalog that is skipped or read-only *is* yours, and its fix is repairing the one
 * you have. Offering to create a second one there answers a question nobody asked and
 * leaves the broken one broken.
 */
const noOwnCatalog = computed(
  // Guarded on there being a registry at all: a failed load empties `catalogs` under
  // whatever view is open, and a card explaining what "every catalog below" is would then
  // be describing an empty list, next to the banner saying why it is empty.
  () => props.catalogs.length > 0 && !props.catalogs.some((catalog) => catalog.kind === "local"),
);

/**
 * Which act the register level opens on, and `null` for "not registering".
 *
 * One value rather than a flag beside a mode, for the reason `panel` is one value: the
 * form is open *at* something, and "open but with no act chosen" is a state the view would
 * otherwise have to decide what to do with.
 */
const registerAs = ref<CatalogSource | null>(null);
/** The catalog awaiting an unregister confirmation. */
const unregistering = ref<Catalog | null>(null);
const purgeClone = ref(false);
const purgeInstalls = ref(false);

/**
 * Roughly how many installed copies a purge would delete, for the confirmation to name.
 *
 * **An estimate, and labelled as one.** The authority is the install receipt, which
 * records the catalog a copy actually came from; this counts the catalog's *current*
 * entries that are installed, which is a different set — it misses a copy whose entry has
 * since been removed from the catalog file, and over-counts one whose installed copy came
 * from elsewhere. Deriving the real answer here is not possible: `list` cannot see either
 * case, which is exactly why the deletion itself is receipt-driven and lives in the CLI.
 */
const installedFrom = computed(() => {
  const id = unregistering.value?.id;
  if (!id) return 0;
  return props.entries.filter((entry) => entry.catalog === id && entry.installed).length;
});
const failure = ref("");
const removed = ref<UnregisterReport | null>(null);
/**
 * Whether the finished unregister had been *asked* to delete copies.
 *
 * The report cannot say: an empty `purged_installs` means "none were deleted", which is
 * the same payload whether you ticked the box or not. Reporting both as "untouched" made
 * the confirmation look like it had ignored the checkbox — and in the case that produced
 * this, it had matched nothing, which is a third outcome that deserves its own sentence.
 */
const askedToPurge = ref(false);

/**
 * The CLI refuses to unregister the last catalog, and the form should not offer it.
 *
 * Not a duplicated rule so much as the same fact stated where it is actionable: a button
 * that always fails is worse than no button, and the refusal is still surfaced if the
 * registry changes underneath.
 */
const canUnregister = computed(() => props.catalogs.length > 1);

async function unregister() {
  const target = unregistering.value;
  if (!target) return;

  failure.value = "";
  askedToPurge.value = purgeInstalls.value;
  try {
    removed.value = await withActivity(`unregistering ${target.id}…`, () =>
      invoke<UnregisterReport>("registry_remove", {
        id: target.id,
        purgeClone: purgeClone.value,
        purgeInstalls: purgeInstalls.value,
      }),
    );
    unregistering.value = null;
    purgeClone.value = false;
    purgeInstalls.value = false;
    emit("changed");
  } catch (e) {
    failure.value = describeAppError(e);
  }
}

/**
 * Every pin, fetched rather than derived from `entries`.
 *
 * A *dangling* pin — one whose catalog is unregistered, skipped, or no longer holds the
 * name — has no row in `entries` carrying `pinned`, because no copy of that name is
 * pinned to anything that exists. It is also the only case worth a block of its own, so
 * deriving would have missed exactly what this is for.
 */
const pins = ref<Pin[]>([]);
const pinFailure = ref("");
/** The name whose pin is being cleared, which is also "a write is in flight". */
const clearing = ref("");

async function loadPins() {
  try {
    pins.value = await invoke<Pin[]>("pins_list");
  } catch (e) {
    pinFailure.value = describeAppError(e);
  }
}

async function clearPin(name: string) {
  clearing.value = name;
  pinFailure.value = "";
  try {
    await withActivity(`unpinning ${name}…`, () =>
      invoke<UnpinReport>("entry_unpin", { name }),
    );
    await loadPins();
    // The registry did not change, but which copy of that name resolves did, and the
    // list behind this view renders it.
    emit("changed");
  } catch (e) {
    pinFailure.value = describeAppError(e);
  } finally {
    clearing.value = "";
  }
}

onMounted(loadPins);
// Unregistering a catalog can strand a pin that named it, so the block is re-read rather
// than left describing the registry as it was.
watch(() => props.catalogs, loadPins);

/**
 * The catalog's own inventory, overridden copies included.
 *
 * Managing a catalog is the "what's in this catalog?" question (D15), so it stays
 * copy-keyed: losing to a higher-precedence catalog says nothing about whether this is
 * your entry to edit.
 */
const held = computed(() =>
  props.entries
    .filter((entry) => entry.catalog === openCatalog.value)
    .sort((a, b) => a.name.localeCompare(b.name)),
);

/** Toggle a row's form, closing whatever was open — including the same button again. */
function show(name: string, mode: Panel["mode"]) {
  const open = panel.value;
  panel.value = open && open.name === name && open.mode === mode ? null : { name, mode };
}

function isOpen(name: string, mode: Panel["mode"]): boolean {
  return panel.value?.name === name && panel.value.mode === mode;
}

/** The one place the open catalog changes, so the parent cannot be told about only some. */
function goTo(id: string | null) {
  openCatalog.value = id;
  panel.value = null;
  // Navigating within the view means the registry *is* now behind us, so Back stops
  // belonging to whoever opened it.
  arrivedHere.value = false;
  emit("navigate", id);
}

/** Back out of a catalog: to the caller when we were dropped here, else to the registry. */
function leaveCatalog() {
  if (arrivedHere.value) emit("close");
  else goTo(null);
}

// A catalog that stops being available under us must not leave the view pointing at it.
watch(
  () => props.catalogs,
  () => {
    if (openCatalog.value && !props.catalogs.some((c) => c.id === openCatalog.value)) {
      goTo(null);
    }
  },
);

/**
 * A removal takes the entry with it, so its row closes.
 *
 * Leaving the panel open would bind the forms to a record the next reload deletes.
 */
function afterRemove() {
  panel.value = null;
  emit("changed");
}

/**
 * Arriving from an entry's detail page lands on that entry's edit form, opened.
 *
 * The hand-off exists so "edit this" reaches the form; dropping the user at the top of a
 * 35-row list to find the row again would make the button a navigation hint rather than
 * an action. Scrolled into view because the row is usually below the fold.
 */
watch(
  () => props.atEntry,
  async (name) => {
    if (!name) return;
    panel.value = { name, mode: "edit" };
    await nextTick();
    const row = document.getElementById(`entry-${name}`);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    row?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  },
  { immediate: true },
);
</script>

<template>
  <section class="view">
    <!-- Its own level, not a panel dropped on top of the list: the list already has a
         drill-in level for managing a catalog's entries, and a second navigation idiom
         for "go do a focused thing, then come back" was the inconsistency, not the
         inline form itself. -->
    <template v-if="registerAs">
      <PageHeader title="Add a catalog" back="Catalogs" @back="registerAs = null">

        <RegisterCatalog
          :catalogs="catalogs"
          :start="registerAs"
          @registered="emit('changed')"
          @close="registerAs = null"
        />
      </PageHeader>
    </template>

    <!-- Level 1: the registry. -->
    <template v-else-if="!openCatalog">
      <PageHeader title="Catalogs" :back="backTo" @back="emit('close')">
        <template #actions>
          <button type="button" class="ghost" @click="registerAs = 'existing'">
            Add a catalog
          </button>
          <!-- `doctor` validates config and catalog integrity, so this is its subject
               rather than the install list it used to sit above. -->
          <button type="button" class="ghost" @click="emit('doctor')">
            Check catalog health
          </button>
        </template>

        <StatusBanner v-if="failure" kind="error" :detail="failure" />
        <StatusBanner v-else-if="removed" kind="success">
          <p class="catalogs__done">
            Unregistered {{ removed.id }}.
            <template v-if="removed.purged_installs.length">
              Deleted {{ removed.purged_installs.length }} installed
              {{ removed.purged_installs.length === 1 ? "copy" : "copies" }}. The catalog's own
              entries stay in its file.
            </template>
            <!-- The third outcome, and the one that made this banner look broken: the box
                 was ticked and nothing matched. Silence there reads as the tick being
                 ignored. -->
            <template v-else-if="askedToPurge">
              <strong>No copies were deleted.</strong> None of the installs on this machine
              record having come from {{ removed.id }}, so nothing was attributable to it.
            </template>
            <template v-else>
              Its entries and every file installed from them are untouched.
            </template>
            <template v-if="removed.purged_clone">Its clone was deleted.</template>
            <template v-else-if="removed.clone_kept_at">
              Its clone is still at {{ removed.clone_kept_at }}.
            </template>
          </p>
          <ul v-if="removed.purged_installs.length" class="catalogs__deleted">
            <li v-for="path in removed.purged_installs" :key="path"><code>{{ path }}</code></li>
          </ul>
          <p v-if="removed.cleared_receipts.length" class="catalogs__done-detail">
            Also cleared {{ removed.cleared_receipts.length }} stale
            {{ removed.cleared_receipts.length === 1 ? "receipt" : "receipts" }} for copies that
            were already gone.
          </p>
        </StatusBanner>

        <!-- Above the precedence lead, not beside it: to someone whose only catalog is the
             team's, precedence is a rule about a collision they cannot have yet, while this
             is the one thing on the page worth doing. It carries no dismiss control on
             purpose — it is derived from the registry, so registering a catalog of your own
             is what takes it away, and nothing else can leave it wrongly hidden. -->
        <section v-if="noOwnCatalog" class="catalogs__own card">
          <h3 class="catalogs__own-title">You don't have a personal catalog yet</h3>
          <p class="catalogs__own-lead">
            Every catalog below lives in a git repository, where a change lands for everyone
            who reads it. A <strong>personal catalog</strong> is a second one, kept on this
            machine and checked first, holding entries only you see.
          </p>
          <ul class="catalogs__own-why">
            <li>Add your own skills, agents, and prompts without opening a pull request.</li>
            <li>
              Keep your own version of a shared entry: added under the same name, your copy is
              the one that installs and the shared one stays where it is.
            </li>
            <li>
              It is a plain <code>library.yaml</code> on this machine. Nothing in it is shared
              until you decide to share it.
            </li>
          </ul>
          <button type="button" @click="registerAs = 'create'">
            Create a personal catalog
          </button>
          <p class="catalogs__own-note">
            Writes an empty <code>library.yaml</code> where you choose and registers it ahead of
            what you already have. No repository, no review step. Already keep a
            <code>library.yaml</code> on this machine? <strong>Add a catalog</strong> above
            registers that one as it stands.
          </p>
        </section>
        <!-- The other half of what one sentence used to cover, and a different fix: a local
             catalog that is registered but unusable is repaired, not replaced. -->
        <p v-else-if="nothingEditable" class="catalogs__lead">
          None of these is a catalog you can edit from here. The ones on this machine are
          read-only or were not loaded, and each row below says which.
          <strong>Check catalog health</strong> above is where that gets diagnosed.
        </p>

        <p class="catalogs__lead">
          Where your entries come from, in precedence order: when two catalogs define the same
          name, the one nearer the top is the copy that installs.
        </p>
        <!-- Below the registry, because it is the exception to what the registry says
             and reads as nonsense before the order it overrides. -->
        <section v-if="pins.length" class="catalogs__pins">
          <h3 class="catalogs__pins-title">Pinned names ({{ pins.length }})</h3>
          <p class="catalogs__lead">
            These ignore the order above and come from the catalog named here. Set one from
            an entry's own page, under <strong>Where this comes from</strong>.
          </p>
          <StatusBanner v-if="pinFailure" kind="error" :detail="pinFailure" />
          <ul class="catalogs__pin-list">
            <li v-for="pin in pins" :key="pin.name" class="catalogs__pin">
              <span class="catalogs__pin-name">{{ pin.name }}</span>
              <span class="catalogs__pin-arrow">→</span>
              <span class="catalogs__chip catalogs__pin-chip">{{ pin.catalog }}</span>
              <!-- The silent failure this block exists for: the name still installs, just
                   not from where it was asked, so nothing else would ever mention it. -->
              <span v-if="pin.dangling" class="catalogs__pin-dangling">
                {{ pin.catalog }} can't supply this
                <template v-if="pin.resolves_to">
                  — it comes from {{ pin.resolves_to }} instead
                </template>
                <template v-else>— and no catalog defines the name</template>
              </span>
              <button
                type="button"
                class="ghost btn-xs catalogs__pin-clear"
                :disabled="clearing === pin.name"
                @click="clearPin(pin.name)"
              >
                {{ clearing === pin.name ? "Clearing…" : "Clear" }}
              </button>
            </li>
          </ul>
        </section>

        <ul class="catalogs__list">
          <li
            v-for="option in catalogs"
            :key="option.id"
            class="catalogs__row"
            :style="{ '--catalog-hue': catalogHue(option.precedence) }"
          >
            <div class="catalogs__row-head">
              <span class="catalogs__chip">{{ option.id }}</span>
              <span class="catalogs__meta">
                {{ describeCatalog(option).what }} ·
                {{ option.entries === null ? "entry count unknown" : `${option.entries} entries` }}
              </span>
              <button
                v-if="editableIds.has(option.id)"
                type="button"
                class="ghost btn-sm catalogs__manage"
                @click="goTo(option.id)"
              >
                Manage entries
              </button>
            </div>
            <p class="catalogs__where">{{ option.location }}</p>
            <p class="catalogs__why">{{ describeCatalog(option).note }}</p>

            <button
              v-if="canUnregister && unregistering?.id !== option.id"
              type="button"
              class="danger btn-sm catalogs__unregister"
              @click="unregistering = option"
            >
              Unregister
            </button>

            <div v-if="unregistering?.id === option.id" class="catalogs__confirm fade-in">
              <p class="catalogs__question">Stop reading from {{ option.id }}?</p>
              <p class="catalogs__note">
                Its entries stay in their catalog file and every copy installed from them stays
                on disk. Only this machine's list of catalogs changes, and registering it again
                brings it back.
              </p>
              <!-- Not a convenience: `uninstall` resolves through the catalog, so once this
                   is unregistered its installed copies are invisible to the app and refused
                   by the CLI. Without this tick, removing them means doing it by hand. -->
              <label class="catalogs__purge">
                <input v-model="purgeInstalls" type="checkbox" />
                <span>
                  Also delete the copies installed from it<template v-if="installedFrom">
                    — around {{ installedFrom }}</template
                  >. Otherwise they stay on disk with no catalog left to remove them through,
                  and only deleting the folders by hand will clear them. Copies you put there
                  yourself are never touched.
                </span>
              </label>
              <label v-if="option.kind !== 'local'" class="catalogs__purge">
                <input v-model="purgeClone" type="checkbox" />
                <span>Also delete the clone this machine keeps of that repository.</span>
              </label>
              <div class="catalogs__confirm-actions">
                <button type="button" class="ghost" @click="unregistering = null">Cancel</button>
                <button type="button" class="danger" @click="unregister()">
                  {{ purgeInstalls ? "Unregister and delete the copies" : "Unregister" }}
                </button>
              </div>
            </div>
          </li>
        </ul>
      </PageHeader>
    </template>

    <!-- Level 2: one catalog's entries, each row carrying its own actions. -->
    <template v-else>
      <PageHeader :title="openCatalog" :back="arrivedHere ? backTo : 'Catalogs'" @back="leaveCatalog()">
        <template #actions>
          <button type="button" class="ghost" @click="emit('add', openCatalog)">
            Add an entry
          </button>
        </template>

        <p class="catalogs__lead">{{ catalog?.location }}</p>
        <!-- The second beat of the empty registry: a catalog just created is a file with
             nothing in it, and "no entries" on its own leaves the next move to be guessed.
             It names the header's button rather than repeating it — the same action twice on
             one screen is what the page has been removing, and the control is a line away. -->
        <div v-if="!held.length" class="catalogs__empty">
          <p class="catalogs__empty-line">{{ openCatalog }} has no entries yet.</p>
          <p class="catalogs__empty-line catalogs__first">
            <strong>Add an entry</strong> above puts the first one in.
            <!-- Only where it is true: the claim is precedence 1's, not every catalog's, and
                 a "wins" box left unticked at registration is exactly how this one ends up
                 further down the order. -->
            <template v-if="catalog?.precedence === 1">
              A common first move is your own version of a shared entry, added under the same
              name: this catalog is checked first, so your copy is the one that installs and
              the shared one stays where it is.
            </template>
          </p>
        </div>
        <ul v-else class="catalogs__entries">
          <li
            v-for="entry in held"
            :id="`entry-${entry.name}`"
            :key="entry.name"
            class="catalogs__entry"
            :class="{ 'catalogs__entry--open': panel?.name === entry.name }"
          >
            <div class="catalogs__entry-line">
              <span class="catalogs__entry-name">{{ entry.name }}</span>
              <span class="catalogs__entry-type">{{ entry.type }}</span>
              <span class="catalogs__entry-desc">{{ entry.description }}</span>
              <span class="catalogs__entry-actions">
                <button
                  type="button"
                  class="ghost btn-sm"
                  :aria-pressed="isOpen(entry.name, 'edit')"
                  @click="show(entry.name, 'edit')"
                >
                  Edit
                </button>
                <button
                  type="button"
                  class="danger btn-sm"
                  :aria-pressed="isOpen(entry.name, 'remove')"
                  @click="show(entry.name, 'remove')"
                >
                  Remove
                </button>
              </span>
            </div>

            <div v-if="panel?.name === entry.name" class="catalogs__panel fade-in">
              <EntryEditor
                v-if="panel.mode === 'edit'"
                :entry="entry"
                :entries="entries"
                @saved="emit('changed')"
                @close="panel = null"
              />
              <EntryRemove v-else :entry="entry" @removed="afterRemove()" @close="panel = null" />
            </div>
          </li>
        </ul>
      </PageHeader>
    </template>
  </section>
</template>

<style scoped>
.catalogs__lead {
  margin: 0 0 1.1rem;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.7;
  overflow-wrap: anywhere;
}
/* A card rather than another paragraph: it is the only block on the page that teaches
   something instead of reporting it, and at the lead's size and opacity it read as more of
   the boilerplate above the list — which is where it spent a release being skipped. */
.catalogs__own {
  margin-bottom: 1.4rem;
  border-left: 3px solid var(--accent-edge);
}
.catalogs__own-title {
  margin: 0 0 0.4rem;
  font-size: 0.95rem;
}
.catalogs__own-lead {
  margin: 0;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.85;
}
.catalogs__own-why {
  margin: 0.5rem 0 0.8rem;
  padding-left: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.8;
}
.catalogs__own-note {
  margin: 0.5rem 0 0;
  font-size: 0.74rem;
  line-height: 1.45;
  opacity: 0.6;
}
/* The shape the app's other empty states use — centred, roomy, quiet — rather than a lead
   paragraph, so an empty catalog reads as a state and not as a page whose list failed to
   render. Its own rule and not App.vue's `.state`, which is scoped to that component. */
.catalogs__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.7rem;
  padding: 2rem 0;
  text-align: center;
  opacity: 0.8;
}
.catalogs__empty-line {
  margin: 0;
}
/* A measure on the follow-through only: centred running text past this width stops being
   readable as a sentence, and the line above it is short enough not to care. */
.catalogs__first {
  max-width: 34rem;
  font-size: 0.85rem;
  line-height: 1.5;
}
.catalogs__list,
.catalogs__entries {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.catalogs__pins {
  margin-bottom: 1.4rem;
}
.catalogs__pins-title {
  margin: 0 0 0.4rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.catalogs__pin-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.catalogs__pin {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.45rem 0.9rem;
  border-radius: 8px;
  background: var(--surface-raised);
}
.catalogs__pin-name {
  font-size: 0.85rem;
  font-weight: 600;
}
.catalogs__pin-arrow {
  opacity: 0.4;
}
.catalogs__pin-chip {
  background: var(--surface-strong);
  color: inherit;
}
.catalogs__pin-dangling {
  flex: 1;
  font-size: 0.74rem;
  line-height: 1.4;
  color: var(--status-attention-ink);
}
.catalogs__pin-clear {
  margin-left: auto;
}
.catalogs__row {
  padding: 0.7rem 0.9rem;
  border-radius: 8px;
  border-left: 3px solid var(--catalog-edge);
  background: var(--surface-raised);
}
.catalogs__row-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.catalogs__chip {
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  background: var(--catalog-fill);
  color: var(--text-on-accent);
  font-size: 0.7rem;
  font-weight: 600;
}
.catalogs__meta {
  flex: 1;
  font-size: 0.75rem;
  opacity: 0.7;
}
.catalogs__where {
  margin: 0.35rem 0 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  opacity: 0.55;
  overflow-wrap: anywhere;
}
.catalogs__why {
  margin: 0.35rem 0 0;
  font-size: 0.74rem;
  line-height: 1.45;
  opacity: 0.65;
}
.catalogs__unregister {
  margin-top: 0.5rem;
}
.catalogs__confirm {
  margin-top: 0.6rem;
  padding: 0.7rem;
  border-radius: 8px;
  background: var(--status-danger-tint);
  border-left: 3px solid var(--status-danger-ink);
}
.catalogs__question {
  margin: 0;
  font-size: 0.85rem;
  font-weight: 600;
}
.catalogs__note {
  margin: 0.4rem 0 0;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.8;
}
.catalogs__purge {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  margin-top: 0.5rem;
  font-size: 0.76rem;
  line-height: 1.45;
}
.catalogs__confirm-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
}
.catalogs__done {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.45;
}
.catalogs__done-detail {
  margin: 0.4rem 0 0;
  font-size: 0.78rem;
  opacity: 0.8;
}
.catalogs__deleted {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
  max-height: 10rem;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  opacity: 0.8;
  overflow-wrap: anywhere;
}
.catalogs__entry {
  border-radius: 8px;
  background: var(--surface-raised);
}
.catalogs__entry--open {
  background: var(--surface-hover);
}
.catalogs__entry-line {
  display: grid;
  grid-template-columns: minmax(8rem, auto) auto 1fr auto;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem 0.4rem 0.85rem;
}
.catalogs__entry-name {
  font-size: 0.88rem;
  font-weight: 600;
}
.catalogs__entry-type {
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.55;
}
.catalogs__entry-desc {
  font-size: 0.78rem;
  opacity: 0.65;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.catalogs__entry-actions {
  display: flex;
  gap: 0.5rem;
}
/* Pressed state, so an open form's own button reads as the thing that opened it. */
.catalogs__entry-actions button[aria-pressed="true"] {
  background: var(--surface-strong);
}
.catalogs__panel {
  padding: 0.2rem 0.85rem 0.85rem;
}
</style>
