<script setup lang="ts">
import { computed } from "vue";
import { catalogHue, type Row } from "../catalog";
import type { Catalog } from "../types";

const props = defineProps<{
  rows: Row[];
  catalogs: Catalog[];
  /** Origin is only worth the space once more than one catalog is registered. */
  showOrigin: boolean;
  /**
   * Names ticked for a bulk install, or null when selection is off.
   *
   * Null rather than an empty array so "not selecting" and "selected nothing" stay
   * different states: the first renders no checkboxes at all.
   */
  selected?: Set<string> | null;
}>();

const emit = defineEmits<{ select: [name: string]; toggle: [name: string] }>();

/** True while the list is in selection mode at all. */
const selecting = computed(() => props.selected !== null && props.selected !== undefined);

/**
 * An overridden copy cannot be picked, because `use` would not install it.
 *
 * It resolves to whichever catalog wins the name, so picking it would promise this
 * catalog's copy and deliver another's. The row already says which catalog beats it.
 */
function selectable(row: Row): boolean {
  return selecting.value && !row.entry.overridden_by;
}

/**
 * One click handler, because the card is the hit target in both modes.
 *
 * A separate checkbox was a ~13px target beside a full-width card, and it needed a
 * reserved gutter so rows stayed aligned — which showed as an empty column in a tab where
 * nothing is selectable. Making the card itself the control removes both problems.
 */
function activate(row: Row) {
  if (selecting.value) emit("toggle", row.entry.name);
  else emit("select", row.entry.name);
}

const hueByCatalog = computed(
  () => new Map(props.catalogs.map((catalog) => [catalog.id, catalogHue(catalog.precedence)])),
);
</script>

<template>
  <ul class="entry-list">
    <li
      v-for="row in rows"
      :key="`${row.entry.catalog}:${row.entry.name}`"
      class="entry-list__row"
    >
      <!-- The card is the unit, and the button fills it. Controls sit beside the button
           rather than inside it, so a future per-entry control can be a real interactive
           element without nesting one button in another. -->
      <div
        class="entry-list__card"
        :class="{ 'entry-list__card--picked': selected?.has(row.entry.name) }"
      >
      <button
        type="button"
        class="entry-list__item"
        :disabled="selecting && !selectable(row)"
        @click="activate(row)"
      >
      <div class="entry-list__head">
        <span class="entry-list__name">{{ row.entry.name }}</span>
        <span class="entry-list__type">{{ row.entry.type }}</span>

        <span
          v-if="showOrigin"
          class="entry-list__origin"
          :style="{ '--catalog-hue': hueByCatalog.get(row.entry.catalog) ?? 220 }"
        >
          {{ row.entry.catalog }}
        </span>

        <span class="entry-list__status" :class="`entry-list__status--${row.tone}`">
          {{ row.status }}
        </span>

        <span v-if="row.overrides.length" class="entry-list__overrides">
          overrides {{ row.overrides.join(", ") }}
        </span>
      </div>

      <p class="entry-list__desc">{{ row.entry.description }}</p>
      <p v-if="row.entry.requires.length" class="entry-list__requires">
        requires: {{ row.entry.requires.join(", ") }}
      </p>
      </button>

      <!-- The slot that grows: a pick indicator today, an on/off control later. Rendered
           only when it has something in it, so no row reserves space for nothing. -->
      <span v-if="selectable(row)" class="entry-list__controls">
        <span
          class="entry-list__tick"
          :class="{ 'entry-list__tick--on': selected?.has(row.entry.name) }"
          aria-hidden="true"
        />
      </span>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.entry-list__card {
  display: flex;
  align-items: stretch;
  border-radius: 10px;
  transition: background 0.12s ease;
}
.entry-list__card--picked {
  background: rgba(59, 130, 246, 0.14);
}
.entry-list__card > .entry-list__item {
  flex: 1;
  min-width: 0;
}
.entry-list__controls {
  display: flex;
  align-items: center;
  padding: 0 0.9rem 0 0.2rem;
}
.entry-list__tick {
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 50%;
  border: 2px solid rgba(128, 128, 128, 0.5);
}
.entry-list__tick--on {
  border-color: #3b82f6;
  background: #3b82f6;
  /* The check, drawn rather than a glyph, so it cannot pick up a font's baseline. */
  background-image: linear-gradient(45deg, transparent 45%, #fff 45%, #fff 55%, transparent 55%),
    linear-gradient(-45deg, transparent 60%, #fff 60%, #fff 72%, transparent 72%);
}
.entry-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.entry-list__item {
  display: block;
  width: 100%;
  padding: 0.85rem 1rem;
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.08);
  border: 1px solid rgba(128, 128, 128, 0.15);
  color: inherit;
  font: inherit;
  font-weight: normal;
  text-align: left;
  cursor: pointer;
}
.entry-list__item:hover {
  border-color: rgba(128, 128, 128, 0.4);
  background: rgba(128, 128, 128, 0.14);
}
.entry-list__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.entry-list__name {
  font-weight: 600;
}
.entry-list__type {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.6;
}
.entry-list__origin {
  --catalog-hue: 220;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  background: hsl(var(--catalog-hue), 65%, 50%);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.entry-list__status {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
}
.entry-list__status--installed {
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
}
.entry-list__status--absent {
  background: rgba(128, 128, 128, 0.18);
  opacity: 0.8;
}
.entry-list__status--overridden {
  background: rgba(234, 179, 8, 0.18);
  color: #b45309;
}
.entry-list__status--attention {
  background: rgba(245, 158, 11, 0.2);
  color: #b45309;
  font-weight: 600;
}
.entry-list__overrides {
  font-size: 0.7rem;
  opacity: 0.55;
}
.entry-list__desc {
  margin: 0.4rem 0 0;
  font-size: 0.88rem;
  line-height: 1.4;
  opacity: 0.85;
}
.entry-list__requires {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
