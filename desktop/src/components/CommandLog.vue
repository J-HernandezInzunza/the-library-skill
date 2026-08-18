<script setup lang="ts">
import { ref } from "vue";
import { useCommandActivity, type LoggedCommand } from "../commandActivity";

// The stream lives outside this component, so the log keeps recording while the panel
// is collapsed — which is most of the time, and the log is the only safeguard there is.
const { commands } = useCommandActivity();
const open = ref(false);

/**
 * How much of one command is shown before it has to be asked for.
 *
 * Every command in the app fits well inside this except one: the agent spawn carries the entire
 * walkthrough prompt as a single argv element, some two thousand characters of it. Rendered whole
 * it filled the window, buried every other entry, and — because this panel floats over the page —
 * left the transcript showing through from behind. D5 wants the command verbatim, and it still is:
 * one click away, in the same row, rather than as the default that makes the log unreadable.
 */
const LIMIT = 160;

/** Rows the user has asked to see in full, by command id. */
const expanded = ref<Set<number>>(new Set());

function toggle(id: number) {
  const next = new Set(expanded.value);
  if (!next.delete(id)) next.add(id);
  expanded.value = next;
}

function label(run: LoggedCommand): string {
  return run.argv.join(" ");
}

function isLong(run: LoggedCommand): boolean {
  return label(run).length > LIMIT;
}

/** What the row shows now: the whole command, or its head. */
function shown(run: LoggedCommand): string {
  const full = label(run);
  if (!isLong(run) || expanded.value.has(run.id)) return full;
  return `${full.slice(0, LIMIT)}…`;
}
</script>

<template>
  <aside class="command-log" :class="{ 'command-log--open': open }">
    <button type="button" class="command-log__toggle" @click="open = !open">
      <span class="command-log__caret">{{ open ? "▾" : "▸" }}</span>
      Commands
      <span class="command-log__count">{{ commands.length }}</span>
    </button>

    <ol v-show="open" class="command-log__list">
      <li v-for="run in commands" :key="run.id" class="command-log__row">
        <span
          class="command-log__status"
          :class="{
            'command-log__status--running': run.code === null,
            'command-log__status--failed': run.code !== null && run.code !== 0,
          }"
        >
          {{ run.code === null ? "…" : run.code }}
        </span>
        <code class="command-log__argv">{{ shown(run) }}</code>
        <!-- Only where something is actually hidden, so the control's presence means there is
             more to read rather than being decoration on every row. -->
        <button
          v-if="isLong(run)"
          type="button"
          class="command-log__more"
          @click="toggle(run.id)"
        >
          {{ expanded.has(run.id) ? "less" : "full" }}
        </button>
        <span v-if="run.durationMs !== null" class="command-log__time">
          {{ run.durationMs }}ms
        </span>
      </li>
      <li v-if="!commands.length" class="command-log__empty">Nothing has run yet.</li>
    </ol>
  </aside>
</template>

<style scoped>
.command-log {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 20;
  /* Never taller than the window it floats over. One agent spawn carries the whole walkthrough
     prompt as a single argv element — hundreds of words — and an uncapped fixed panel grew past
     the viewport and rendered the log *through* the page behind it, since the background is
     deliberately translucent. Two layers of text on top of each other, found by using the app. */
  max-height: 60vh;
  overflow-y: auto;
  border-top: 1px solid rgba(128, 128, 128, 0.25);
  /* Opaque, not the translucent sticky surface. This panel floats *over* the page rather than
     scrolling with it, and a translucent one let the page render straight through the log —
     two layers of text on top of each other, which is what it looked like in the app. */
  background: var(--app-bg);
}
.command-log__toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.4rem 1rem;
  border: none;
  border-radius: 0;
  background: transparent;
  color: inherit;
  font-size: 0.78rem;
  font-weight: 500;
  opacity: 0.75;
}
.command-log__caret {
  opacity: 0.6;
}
.command-log__count {
  font-variant-numeric: tabular-nums;
  opacity: 0.55;
}
.command-log__list {
  list-style: none;
  max-height: 14rem;
  overflow-y: auto;
  margin: 0;
  padding: 0 1rem 0.75rem;
}
.command-log__row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.2rem 0;
  font-size: 0.75rem;
}
.command-log__status {
  min-width: 1.5rem;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #16a34a;
}
.command-log__status--running {
  color: inherit;
  opacity: 0.5;
}
.command-log__status--failed {
  color: #dc2626;
  font-weight: 600;
}
.command-log__argv {
  flex: 1;
  /* Verbatim (D5) but bounded: even expanded, one entry scrolls inside its own row instead of
     pushing every other command out of the panel. */
  max-height: 9rem;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
  opacity: 0.85;
}
.command-log__more {
  flex: none;
  align-self: flex-start;
  padding: 0 0.35rem;
  border: 1px solid rgba(128, 128, 128, 0.35);
  border-radius: 0.25rem;
  background: transparent;
  color: inherit;
  font-size: 0.68rem;
  opacity: 0.7;
  cursor: pointer;
}
.command-log__time {
  opacity: 0.5;
  font-variant-numeric: tabular-nums;
}
.command-log__empty {
  padding: 0.2rem 0;
  font-size: 0.75rem;
  opacity: 0.5;
}
</style>
