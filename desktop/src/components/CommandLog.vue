<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { CommandFinished, CommandStarted } from "../types";

interface LoggedCommand extends CommandStarted {
  code: number | null;
  durationMs: number | null;
}

const commands = ref<LoggedCommand[]>([]);
const open = ref(false);

// The component stays mounted and only its body collapses. Mounting it with the
// panel would miss every command run while the panel was closed, which is most of
// them, and the log is the only safeguard there is.
const unlisteners: UnlistenFn[] = [];

listen<CommandStarted>("command://started", ({ payload }) => {
  commands.value.unshift({ ...payload, code: null, durationMs: null });
}).then((off) => unlisteners.push(off));

listen<CommandFinished>("command://finished", ({ payload }) => {
  const run = commands.value.find((c) => c.id === payload.id);
  if (!run) return;
  run.code = payload.code;
  run.durationMs = payload.duration_ms;
}).then((off) => unlisteners.push(off));

onUnmounted(() => unlisteners.forEach((off) => off()));

function label(run: LoggedCommand): string {
  return run.argv.join(" ");
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
        <code class="command-log__argv">{{ label(run) }}</code>
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
  border-top: 1px solid rgba(128, 128, 128, 0.25);
  background: var(--app-bg-sticky);
  backdrop-filter: blur(8px);
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
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
  opacity: 0.85;
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
