<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { catalogHue, editableCatalogs } from "../catalog";
import type { Catalog, Entry } from "../types";
import EntryEditor from "./EntryEditor.vue";
import EntryRemove from "./EntryRemove.vue";

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
const emit = defineEmits<{ close: []; changed: [] }>();

/**
 * Three levels in one view: the registry, one catalog's entries, one entry's forms.
 *
 * Held as two ids rather than a route because the whole view is reached from a button and
 * left with Back; a `null` at either level is the level above.
 */
const openCatalog = ref<string | null>(props.atCatalog ?? null);
const openEntry = ref<string | null>(props.atEntry ?? null);

/** Catalogs whose entries this app will edit, which is the ones on this machine. */
const editableIds = computed(
  () => new Set(editableCatalogs(props.catalogs).map((catalog) => catalog.id)),
);

const catalog = computed(
  () => props.catalogs.find((c) => c.id === openCatalog.value) ?? null,
);

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

const entry = computed(() => held.value.find((e) => e.name === openEntry.value) ?? null);

/**
 * Why a catalog cannot be managed here, or "" when it can.
 *
 * Stated per catalog rather than by hiding the row: a missing catalog reads as a bug,
 * and the three reasons have three different answers.
 */
function unmanageable(candidate: Catalog): string {
  if (candidate.skipped) return `not loaded — ${candidate.skipped}`;
  if (!candidate.writable) return "registered as read-only";
  if (candidate.kind !== "local") {
    return "a shared catalog — entries are changed in the repository itself, so the change gets the same review as any other";
  }
  return "";
}

// A catalog that stops being available under us must not leave the view pointing at it.
watch(
  () => props.catalogs,
  () => {
    if (openCatalog.value && !props.catalogs.some((c) => c.id === openCatalog.value)) {
      openCatalog.value = null;
      openEntry.value = null;
    }
  },
);

/**
 * A removal takes the entry with it, so the view steps back to the list.
 *
 * Staying would leave the forms bound to a record the next reload deletes.
 */
function afterRemove() {
  openEntry.value = null;
  emit("changed");
}
</script>

<template>
  <section class="catalogs">
    <header class="catalogs__head">
      <button v-if="!openCatalog" type="button" class="ghost" @click="emit('close')">
        ← Back to {{ backTo }}
      </button>
      <button
        v-else-if="!openEntry"
        type="button"
        class="ghost"
        @click="openCatalog = null"
      >
        ← All catalogs
      </button>
      <button v-else type="button" class="ghost" @click="openEntry = null">
        ← {{ openCatalog }}
      </button>

      <h2 class="catalogs__title">
        <template v-if="openEntry">{{ openEntry }}</template>
        <template v-else-if="openCatalog">{{ openCatalog }}</template>
        <template v-else>Catalogs</template>
      </h2>
    </header>

    <!-- Level 1: the registry. -->
    <template v-if="!openCatalog">
      <p class="catalogs__lead">
        Where your entries come from, in precedence order: when two catalogs define the same
        name, the one nearer the top is the copy that installs.
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
              {{ option.kind }} · {{ option.write_mode }} ·
              {{ option.entries === null ? "—" : `${option.entries} entries` }}
            </span>
            <button
              v-if="editableIds.has(option.id)"
              type="button"
              class="ghost catalogs__manage"
              @click="openCatalog = option.id"
            >
              Manage entries
            </button>
          </div>
          <p class="catalogs__where">{{ option.location }}</p>
          <p v-if="unmanageable(option)" class="catalogs__why">
            {{ unmanageable(option) }}
          </p>
        </li>
      </ul>
    </template>

    <!-- Level 2: one catalog's entries, one line each. -->
    <template v-else-if="!entry">
      <p class="catalogs__lead">{{ catalog?.location }}</p>
      <p v-if="!held.length" class="catalogs__empty">
        This catalog has no entries yet. Add one from the catalog view.
      </p>
      <ul v-else class="catalogs__entries">
        <li v-for="held_ in held" :key="held_.name">
          <button type="button" class="catalogs__entry" @click="openEntry = held_.name">
            <span class="catalogs__entry-name">{{ held_.name }}</span>
            <span class="catalogs__entry-type">{{ held_.type }}</span>
            <span class="catalogs__entry-desc">{{ held_.description }}</span>
          </button>
        </li>
      </ul>
    </template>

    <!-- Level 3: the forms for one entry. -->
    <template v-else>
      <p class="catalogs__lead">
        Editing {{ entry.catalog }}'s copy. This changes the catalog entry, not the files
        installed from it.
      </p>
      <EntryEditor :entry="entry" :entries="entries" @saved="emit('changed')" />
      <EntryRemove :entry="entry" @removed="afterRemove()" />
    </template>
  </section>
</template>

<style scoped>
.catalogs {
  padding: 1.5rem 0 3rem;
}
.catalogs__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.catalogs__title {
  margin: 0;
  font-size: 1.15rem;
}
.catalogs__lead,
.catalogs__empty {
  margin: 0 0 1.1rem;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.7;
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
  display: grid;
  grid-template-columns: minmax(8rem, auto) auto 1fr;
  align-items: baseline;
  gap: 0.6rem;
  width: 100%;
  padding: 0.5rem 0.85rem;
  border: none;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.catalogs__entry:hover {
  background: rgba(128, 128, 128, 0.16);
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
</style>
