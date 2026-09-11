<script setup lang="ts">
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { catalogHue, SESSION_TIMING, type Row } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type Catalog, type ToggleReport } from "../types";
import StatusBanner from "./StatusBanner.vue";

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

const emit = defineEmits<{
  select: [name: string];
  toggle: [name: string];
  /** A toggle landed; the list's owner refetches, because the CLI owns the new state. */
  changed: [];
}>();

/** The entry a toggle is in flight for, so only that one control goes inert. */
const toggling = ref<string | null>(null);

/** The last toggle failure, named, because the list shows many entries at once. */
const toggleError = ref<{ name: string; message: string } | null>(null);

/**
 * The last toggle that landed, so the list can say when it reaches the agent.
 *
 * Said here rather than only on the badge because this is the moment it matters: the user
 * who just switched a skill off is about to go back to a terminal that still has it.
 */
const toggleNote = ref<{ name: string; turnedOn: boolean } | null>(null);

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

/** True while the entry's content is on the machine but parked out of the agent's reach. */
function switchedOff(row: Row): boolean {
  return row.entry.state === "disabled";
}

/**
 * The badge's hover text: the full status, and for a switched-off row when it lands.
 *
 * The card has no room for the sentence itself — the badge is already an elided absolute
 * path capped at 45% of the head — and the two places that do have room are the banner
 * after a toggle and the detail view.
 */
function statusTitle(row: Row): string {
  if (!switchedOff(row)) return row.status;
  return `${row.status}\n${SESSION_TIMING}`;
}

/**
 * Whether this row gets an on/off control.
 *
 * Skills only for now, and only once there is content on the machine to switch: the CLI
 * refuses anything else. Hidden during selection, where the card is a pick target and a
 * second, mutating button beside it would be a trap.
 */
function switchable(row: Row): boolean {
  if (selecting.value || row.entry.type !== "skill") return false;
  return row.entry.state === "installed" || switchedOff(row);
}

/**
 * Switch one skill off or on, then ask for a refetch.
 *
 * The report is not rendered: the new state comes from re-reading the catalog, never from
 * assuming the move landed. A result with `moved: false` is the CLI saying the entry was
 * already in the requested state, which is a success.
 */
async function flip(row: Row) {
  const name = row.entry.name;
  const turningOn = switchedOff(row);
  const command = turningOn ? "entry_enable" : "entry_disable";

  toggling.value = name;
  toggleError.value = null;
  toggleNote.value = null;
  try {
    await withActivity(`${turningOn ? "enabling" : "disabling"} ${name}…`, () =>
      invoke<ToggleReport>(command, { names: [name] }),
    );
    toggleNote.value = { name, turnedOn: turningOn };
    emit("changed");
  } catch (e) {
    toggleError.value = { name, message: describeAppError(e) };
  } finally {
    toggling.value = null;
  }
}

const hueByCatalog = computed(
  () => new Map(props.catalogs.map((catalog) => [catalog.id, catalogHue(catalog.precedence)])),
);
</script>

<template>
  <ul class="entry-list">
    <!-- Top of the list rather than under the control that was clicked, which is where
         this app reports every command. A list item because the list is the surface. -->
    <li v-if="toggleError">
      <StatusBanner kind="error" :detail="toggleError.message">
        Could not switch {{ toggleError.name }}.
      </StatusBanner>
    </li>

    <li v-if="toggleNote">
      <StatusBanner kind="success">
        {{ toggleNote.name }} is switched {{ toggleNote.turnedOn ? "on" : "off" }}.
        {{ SESSION_TIMING }}
      </StatusBanner>
    </li>

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
        :class="{
          'entry-list__card--picked': selected?.has(row.entry.name),
          'entry-list__card--off': switchedOff(row),
        }"
      >
      <button
        type="button"
        class="entry-list__item"
        :disabled="selecting && !selectable(row)"
        :aria-pressed="selectable(row) ? selected?.has(row.entry.name) : undefined"
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

        <span v-if="row.overriddenBy" class="entry-list__overridden">
          overridden by {{ row.overriddenBy }}
        </span>

        <span v-if="row.overrides.length" class="entry-list__overrides">
          overrides {{ row.overrides.join(", ") }}
        </span>

        <!-- Whether this copy is on the machine, held apart from the precedence pills so
             an overridden copy can say both "overridden by X" and "not installed". The
             full text is also the title, because a disabled badge carries the archive
             path and that is longer than the card has room for. -->
        <span
          class="entry-list__status"
          :class="`entry-list__status--${row.tone}`"
          :title="statusTitle(row)"
        >
          {{ row.status }}
        </span>
      </div>

      <p class="entry-list__desc">{{ row.entry.description }}</p>
      <p v-if="row.entry.requires.length" class="entry-list__requires">
        requires: {{ row.entry.requires.join(", ") }}
      </p>
      </button>

      <!-- The slot beside the card button: the on/off control and the pick indicator.
           Rendered only when it has something in it, so no row reserves space for
           nothing. -->
      <span v-if="selectable(row) || switchable(row)" class="entry-list__controls">
        <button
          v-if="switchable(row)"
          type="button"
          class="entry-list__switch"
          :disabled="toggling === row.entry.name"
          :aria-label="`${switchedOff(row) ? 'Enable' : 'Disable'} ${row.entry.name}`"
          @click="flip(row)"
        >
          {{ switchedOff(row) ? "Enable" : "Disable" }}
        </button>

        <span
          v-if="selectable(row)"
          class="entry-list__tick"
          :class="{ 'entry-list__tick--on': selected?.has(row.entry.name) }"
          aria-hidden="true"
        >
          <!-- Drawn, not a glyph, so it cannot pick up a font's baseline: two legs of
               different length is a shape CSS gradients cannot express. -->
          <svg v-if="selected?.has(row.entry.name)" viewBox="0 0 16 16">
            <path d="M4 8.4l2.7 2.7L12 5.2" />
          </svg>
        </span>
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
/* Dimmed, because the content is on the machine but nothing is loading it. */
.entry-list__card--off > .entry-list__item {
  opacity: 0.65;
  border-style: dashed;
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
.entry-list__switch {
  margin-right: 0.5rem;
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.75rem;
  cursor: pointer;
  white-space: nowrap;
}
.entry-list__switch:hover:not(:disabled) {
  border-color: rgba(128, 128, 128, 0.7);
  background: rgba(128, 128, 128, 0.14);
}
.entry-list__switch:disabled {
  opacity: 0.5;
  cursor: progress;
}
.entry-list__tick {
  /* A square box, because the selection is a multi-pick: a circle would promise a radio. */
  display: flex;
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 5px;
  border: 2px solid rgba(128, 128, 128, 0.5);
}
.entry-list__tick--on {
  border-color: #3b82f6;
  background: #3b82f6;
  color: #fff;
}
.entry-list__tick svg {
  width: 100%;
  height: 100%;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.25;
  stroke-linecap: round;
  stroke-linejoin: round;
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
  /* Pushed to the card's top-right, apart from the precedence pills on the left. */
  margin-left: auto;
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  white-space: nowrap;
}
.entry-list__status--installed {
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
}
.entry-list__status--absent {
  background: rgba(128, 128, 128, 0.18);
  opacity: 0.8;
}
.entry-list__status--disabled {
  /* Violet, so "switched off" reads as neither the green of a loading skill nor the grey
     of one that was never installed. */
  background: rgba(139, 92, 246, 0.18);
  color: #7c3aed;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
}
.entry-list__status--attention {
  background: rgba(245, 158, 11, 0.2);
  color: #b45309;
  font-weight: 600;
}
.entry-list__overridden {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: rgba(234, 179, 8, 0.18);
  color: #b45309;
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
