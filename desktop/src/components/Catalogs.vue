<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { catalogHue, describeCatalog, editableCatalogs } from "../catalog";
import type { Catalog, Entry } from "../types";
import EntryEditor from "./EntryEditor.vue";
import EntryRemove from "./EntryRemove.vue";
import PageHeader from "./PageHeader.vue";

const props = defineProps<{
  /** The registry, which decides what can be managed and in what order. */
  catalogs: Catalog[];
  /** The loaded catalog: one record per copy, so a catalog's inventory is a filter. */
  entries: Entry[];
  /** Open straight at a catalog, and optionally an entry, when arriving from elsewhere. */
  atCatalog?: string | null;
  atEntry?: string | null;
  /** What leaving the view returns to, which is not always the catalog list. */
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
  emit("navigate", id);
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
    <!-- Level 1: the registry. -->
    <template v-if="!openCatalog">
      <PageHeader title="Catalogs" :back="`Back to ${backTo}`" @back="emit('close')">
        <!-- `doctor` validates config and catalog integrity, so this is its subject rather
             than the install list it used to sit above. -->
        <button type="button" class="ghost catalogs__health" @click="emit('doctor')">
          Check health
        </button>
      </PageHeader>
      <p class="catalogs__lead">
        Where your entries come from, in precedence order: when two catalogs define the same
        name, the one nearer the top is the copy that installs.
      </p>
      <p v-if="nothingEditable" class="catalogs__lead">
        None of these is a catalog you can edit from here. A catalog of your own is a
        <code>library.yaml</code> file on this machine; register one and it appears in this list
        with its entries editable.
      </p>
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
              class="ghost catalogs__manage"
              @click="goTo(option.id)"
            >
              Manage entries
            </button>
          </div>
          <p class="catalogs__where">{{ option.location }}</p>
          <p class="catalogs__why">{{ describeCatalog(option).note }}</p>
        </li>
      </ul>
    </template>

    <!-- Level 2: one catalog's entries, each row carrying its own actions. -->
    <template v-else>
      <PageHeader :title="openCatalog" back="All catalogs" @back="goTo(null)">
        <button type="button" class="ghost" @click="emit('add', openCatalog)">Add an entry</button>
      </PageHeader>
      <p class="catalogs__lead">{{ catalog?.location }}</p>
      <p v-if="!held.length" class="catalogs__lead">
        This catalog has no entries yet.
      </p>
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
                class="ghost"
                :aria-pressed="isOpen(entry.name, 'edit')"
                @click="show(entry.name, 'edit')"
              >
                Edit
              </button>
              <button
                type="button"
                class="ghost catalogs__danger"
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
.catalogs__list,
.catalogs__entries {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.catalogs__row {
  padding: 0.7rem 0.9rem;
  border-radius: 8px;
  border-left: 3px solid hsl(var(--catalog-hue), 65%, 52%);
  background: rgba(128, 128, 128, 0.08);
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
  background: hsl(var(--catalog-hue, 220), 65%, 50%);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
}
.catalogs__meta {
  flex: 1;
  font-size: 0.75rem;
  opacity: 0.7;
}
.catalogs__health {
  margin-left: auto;
}
.catalogs__manage {
  padding: 0.3rem 0.6rem;
  font-size: 0.75rem;
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
.catalogs__entry {
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.catalogs__entry--open {
  background: rgba(128, 128, 128, 0.14);
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
  gap: 0.35rem;
}
.catalogs__entry-actions button {
  padding: 0.25rem 0.6rem;
  font-size: 0.75rem;
}
/* Pressed state, so an open form's own button reads as the thing that opened it. */
.catalogs__entry-actions button[aria-pressed="true"] {
  background: rgba(128, 128, 128, 0.25);
}
.catalogs__danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.4);
}
.catalogs__panel {
  padding: 0.2rem 0.85rem 0.85rem;
}
</style>
