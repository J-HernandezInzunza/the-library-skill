<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { archivedPath, catalogHue, SESSION_TIMING, type Row } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type Catalog,
  type EntryRef,
  type ToggleReport,
} from "../types";
import { notify } from "../toasts";

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
  /** Which copy was opened. A row is one catalog's copy, so its name alone is ambiguous. */
  select: [ref: EntryRef];
  toggle: [name: string];
  /** A toggle landed; the list's owner refetches, because the CLI owns the new state. */
  changed: [];
}>();

/**
 * The entries a toggle is in flight for, so only those controls go inert.
 *
 * A set rather than one name: the switch on every *other* row stays live while one is
 * moving, so a second flip can start before the first has come back.
 */
const toggling = ref(new Set<string>());

/**
 * Flips the CLI has not confirmed yet, as name → the state the user asked for.
 *
 * The switch renders from this before it renders from the catalog, so it moves under the
 * finger instead of sitting still through a command and the re-read that follows it. This
 * is intent, not truth — it survives only until fresh rows arrive, and a refusal drops it
 * so the row snaps back to what is actually on disk.
 */
const pending = ref(new Map<string, boolean>());

watch(
  () => props.rows,
  () => {
    // Fresh rows are the authority, so an intent that is no longer in flight has done its
    // job: it existed only to cover the gap until this arrived. Intents for a toggle still
    // running are kept — the list also re-renders on every search keystroke, and dropping
    // one then would snap the switch back while its command is still going.
    for (const name of [...pending.value.keys()]) {
      if (!toggling.value.has(name)) pending.value.delete(name);
    }
  },
);

/** True while the list is in selection mode at all. */
const selecting = computed(
  () => props.selected !== null && props.selected !== undefined,
);

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
  else emit("select", { name: row.entry.name, catalog: row.entry.catalog });
}

/** True while the entry's content is on the machine but parked out of the agent's reach. */
function switchedOff(row: Row): boolean {
  return row.entry.state === "disabled";
}

/** Where the switch sits: the unconfirmed flip if there is one, otherwise the catalog. */
function switchedOn(row: Row): boolean {
  return pending.value.get(row.entry.name) ?? !switchedOff(row);
}

/**
 * A switched-off card's hover text: where the content went, and when the change lands.
 *
 * Both are longer than a badge in a wrapping head row can hold, which is why the badge
 * says only `disabled · <scope>`. It hangs off the card rather than the badge because the
 * button that opens the entry is stretched over the badge, and the top element owns the
 * hover. The two surfaces with room for the sentence itself are the banner after a toggle
 * and the detail view.
 */
function statusTitle(row: Row): string {
  if (!switchedOff(row)) return row.status;
  const parked = archivedPath(row.entry);
  return `${row.status}\n${parked ? `Parked at ${parked}\n` : ""}${SESSION_TIMING}`;
}

/**
 * Whether this row gets an on/off control.
 *
 * Skills only for now, and only once there is content on the machine to switch: the CLI
 * refuses anything else. Hidden during selection, where the card is a pick target and a
 * second, mutating control inside it would be a trap.
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

  // Before the command, not after: the whole point is that the switch is already where
  // the user put it while the CLI catches up.
  pending.value.set(name, turningOn);
  toggling.value.add(name);
  try {
    await withActivity(`${turningOn ? "enabling" : "disabling"} ${name}…`, () =>
      invoke<ToggleReport>(command, { names: [name] }),
    );
    // The moment the timing matters: the user who just switched a skill off is about to
    // go back to a terminal that still has it loaded.
    notify({
      kind: "success",
      message: `${name} is switched ${turningOn ? "on" : "off"}. ${SESSION_TIMING}`,
    });
    emit("changed");
  } catch (e) {
    // Nothing moved, so the switch must not claim it did.
    pending.value.delete(name);
    notify({
      kind: "error",
      message: `Could not switch ${name}.`,
      detail: describeAppError(e),
    });
  } finally {
    toggling.value.delete(name);
  }
}

const hueByCatalog = computed(
  () =>
    new Map(
      props.catalogs.map((catalog) => [
        catalog.id,
        catalogHue(catalog.precedence),
      ]),
    ),
);
</script>

<template>
  <ul class="entry-list">
    <li
      v-for="row in rows"
      :key="`${row.entry.catalog}:${row.entry.name}`"
      class="entry-list__row"
    >
      <div
        class="entry-list__card"
        :class="{
          'entry-list__card--picked': selected?.has(row.entry.name),
          'entry-list__card--off': switchedOff(row),
        }"
      >
        <div class="entry-list__item">
          <!-- An empty button stretched over the card, rather than one wrapping its text.
               The card must stay the same width whether or not the row has an on/off
               control, which means the control lives *inside* the card — and a button
               inside a button is invalid markup the browser resolves by swallowing one of
               the two clicks. This keeps the whole card clickable and leaves the inside
               free for real interactive elements, which sit above it on the z-axis. -->
          <button
            type="button"
            class="entry-list__open"
            :disabled="selecting && !selectable(row)"
            :aria-pressed="
              selectable(row) ? selected?.has(row.entry.name) : undefined
            "
            :aria-label="`${selecting ? 'Select' : 'Open'} ${row.entry.name}`"
            :title="switchedOff(row) ? statusTitle(row) : undefined"
            @click="activate(row)"
          ></button>

          <!-- In a column of its own rather than inline after the name. Inline, the one
               word telling a skill from a prompt started at a different x on every row,
               so there was no edge for the eye to run down — and it sat between a
               600-weight name and two filled pills, which is every contrast contest on
               the card lost. A fixed column costs no colour: the alignment is the cue. -->
          <span class="entry-list__type">{{ row.entry.type }}</span>

          <!-- Under the type, in the same column, on the description's line. A track and
               a knob, not a labelled button: the label had to be read to work out which way
               the row would move, and it was the only thing on the card whose width varied
               with its state. -->
          <button
            v-if="switchable(row)"
            type="button"
            role="switch"
            class="entry-list__switch"
            :aria-checked="switchedOn(row)"
            :disabled="toggling.has(row.entry.name)"
            :aria-label="`${row.entry.name} enabled`"
            :title="`${switchedOn(row) ? 'Disable' : 'Enable'} ${row.entry.name}`"
            @click="flip(row)"
          ></button>

          <div class="entry-list__head">
            <span class="entry-list__name">{{ row.entry.name }}</span>
            <span
              v-if="showOrigin"
              class="entry-list__origin"
              :style="{
                '--catalog-hue': hueByCatalog.get(row.entry.catalog) ?? 220,
              }"
            >
              {{ row.entry.catalog }}
            </span>

            <!-- Only where a pin is doing work: on a single-catalog machine every row
                 would carry it and it would say nothing. -->
            <span
              v-if="row.entry.pinned && showOrigin"
              class="entry-list__pinned"
              title="Pinned: this catalog's copy installs, whatever the catalog order says"
            >
              pinned
            </span>

            <span v-if="row.overriddenBy" class="entry-list__overridden">
              overridden by {{ row.overriddenBy }}
            </span>

            <span v-if="row.overrides.length" class="entry-list__overrides">
              overrides {{ row.overrides.join(", ") }}
            </span>

            <!-- Whether this copy is on the machine, held apart from the precedence pills
                 so an overridden copy can say both "overridden by X" and "not installed".
                 Right-aligned on its own now that the switch it used to travel with sits
                 in the left column. -->
            <span
              class="entry-list__status"
              :class="`entry-list__status--${row.tone}`"
            >
              {{ row.status }}
            </span>
          </div>

          <p class="entry-list__desc">{{ row.entry.description }}</p>
          <p v-if="row.entry.requires.length" class="entry-list__requires">
            requires: {{ row.entry.requires.join(", ") }}
          </p>
        </div>

        <!-- The pick indicator, outside the card so it cannot be mistaken for part of it.
             Rendered only in selection mode, so no row reserves space for nothing. -->
        <span v-if="selectable(row)" class="entry-list__controls">
          <span
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
  position: relative;
  display: flex;
  align-items: stretch;
  border-radius: 10px;
  transition: background 0.12s ease;
}
.entry-list__card--picked {
  background: var(--accent-tint);
}
/* Dimmed, because the content is on the machine but nothing is loading it. The text is
   dimmed rather than the card: opacity composites a whole subtree, so fading the card
   would fade the switch that undoes the state it is reporting, and the one control the
   row offers would read as unavailable. */
.entry-list__card--off > .entry-list__item {
  border-style: dashed;
}
.entry-list__card--off .entry-list__type,
.entry-list__card--off .entry-list__name,
.entry-list__card--off .entry-list__desc {
  opacity: 0.55;
}
.entry-list__card > .entry-list__item {
  flex: 1;
  min-width: 0;
}
.entry-list__controls {
  display: flex;
  align-items: center;
  padding: 0 0.9rem 0 0.7rem;
}
.entry-list__switch {
  /* Above the stretched open button, so pressing the switch does not open the entry. */
  position: relative;
  z-index: 2;
  grid-column: 1;
  grid-row: 2;
  justify-self: start;
  /* Out of the baseline group — a control has no text to sit on a baseline — and nudged
     to centre on the description's first line, which is what row 2 is. */
  align-self: start;
  margin-top: 0.5rem;
  width: 1.9rem;
  height: 1.05rem;
  padding: 0;
  border: 1px solid var(--control-off-edge);
  border-radius: 999px;
  background: var(--control-off);
  cursor: pointer;
  transition:
    background 0.12s ease,
    border-color 0.12s ease;
}
.entry-list__switch::after {
  /* The knob. A pseudo-element because the button has no text to lay out around. */
  content: "";
  position: absolute;
  top: 50%;
  left: 0.1rem;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  /* The knob is the light one in both states, because the off track is light and the on
     track is dark. Only the on knob is a token: the off knob has to stay white to carry
     any contrast at all against a track that is nearly the card colour. */
  background: var(--text-on-accent);
  transform: translateY(-50%);
  transition: transform 0.12s ease;
}
.entry-list__switch[aria-checked="true"] {
  background: var(--control-on);
  border-color: var(--control-on);
}
.entry-list__switch[aria-checked="true"]::after {
  background: var(--control-on-knob);
}
.entry-list__switch[aria-checked="true"]::after {
  transform: translate(0.8rem, -50%);
}
.entry-list__switch:hover:not(:disabled) {
  border-color: var(--control-on);
}
.entry-list__switch:disabled {
  /* Only lightly faded: the switch has already moved to where the user put it, and the
     point of moving it early is lost if the new position is hard to read. */
  opacity: 0.7;
  cursor: progress;
}
.entry-list__tick {
  /* A square box, because the selection is a multi-pick: a circle would promise a radio. */
  display: flex;
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 5px;
  border: 2px solid var(--border-control);
}
.entry-list__tick--on {
  border-color: var(--accent-bright);
  background: var(--accent-bright);
  color: var(--text-on-accent);
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
  position: relative;
  /* Two columns — the type and its switch, then everything else — and two rows: the
     name's line, then the description's. The column is sized by the label's own
     `min-width` rather than here, so changing the label's font size cannot silently
     stagger the names (see `.entry-list__type`).

     `baseline` rather than the default, because the label and the name are set five
     points apart: aligning their line boxes centres two different amounts of leading and
     leaves the smaller word riding ~2px high. Sharing a baseline is size-independent. */
  display: grid;
  grid-template-columns: auto 1fr;
  column-gap: 0.5rem;
  align-items: baseline;
  width: 100%;
  padding: 0.85rem 1rem;
  border-radius: 10px;
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  text-align: left;
  cursor: pointer;
}
.entry-list__item:hover {
  border-color: var(--border-control);
  background: var(--surface-hover);
}
.entry-list__open {
  /* Stretched over the card and invisible: the card is the hit target, the button is
     only what makes it one. Above 0 rather than at it, because the description and
     several pills are dimmed with `opacity`, and an element with opacity below 1 paints
     where a `z-index: 0` positioned element would — at 0 the overlay would sit under
     them, and clicking an entry's own description would miss the button that opens it. */
  position: absolute;
  inset: 0;
  z-index: 1;
  padding: 0;
  border: 0;
  border-radius: inherit;
  background: none;
  cursor: inherit;
}
.entry-list__open:disabled {
  cursor: default;
}
/* The focus ring belongs on the card, not on the invisible button filling it. */
.entry-list__open:focus-visible {
  outline: 2px solid var(--accent-bright);
  outline-offset: 1px;
}
.entry-list__head {
  grid-column: 2;
  grid-row: 1;
  /* A grid item's automatic minimum is its content, so without this a long unbroken
     name or catalog id widens the column instead of wrapping inside it. */
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.entry-list__name {
  font-weight: 600;
}
.entry-list__type {
  grid-column: 1;
  grid-row: 1;
  /* The column's width lives here, in the label's own em, so it tracks whatever font
     size this rule is set to: the longest type is ~4.5em wide, and a value in `rem` went
     stale the moment the size changed, widening the prompt rows' column and pushing
     their names ~9px right of everyone else's. A type longer than this still widens its
     own row rather than colliding, because the track is `auto`. */
  min-width: 4.75em;
  /* A width decision as much as a type one: small caps run ~20% wider than lower case,
     and the column above is measured in this size, so every step up here is an indent
     taken off the description on all 45 rows. */
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  /* Widens the column rather than breaking across two lines, which would put the second
     half of a long type under the card's first word. */
  white-space: nowrap;
  opacity: 0.7;
}
.entry-list__origin {
  --catalog-hue: 220;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  background: var(--catalog-fill);
  color: var(--text-on-accent);
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
  background: var(--status-ok-tint);
  color: var(--status-ok-ink);
}
.entry-list__status--absent {
  background: var(--surface-sunken);
  opacity: 0.8;
}
.entry-list__status--disabled {
  /* Violet, so "switched off" reads as neither the green of a loading skill nor the grey
     of one that was never installed. */
  background: var(--status-disabled-tint);
  color: var(--status-disabled-ink);
}
.entry-list__status--attention {
  background: var(--status-attention-tint);
  color: var(--status-attention-ink);
  font-weight: 600;
}
.entry-list__overridden {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--status-override-tint);
  color: var(--status-attention-ink);
}
.entry-list__pinned {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--status-ok-tint);
  color: var(--status-ok-ink);
}
.entry-list__overrides {
  font-size: 0.7rem;
  opacity: 0.55;
}
.entry-list__desc {
  grid-column: 2;
  grid-row: 2;
  margin: 0.4rem 0 0;
  font-size: 0.88rem;
  line-height: 1.4;
  opacity: 0.85;
}
.entry-list__requires {
  grid-column: 2;
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
