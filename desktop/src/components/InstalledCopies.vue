<script setup lang="ts">
import { ref, watch } from "vue";
import type { InstalledCopy } from "../catalog";
import type { Source } from "../types";
import PushControl from "./PushControl.vue";
import UninstallControl from "./UninstallControl.vue";

const props = defineProps<{
  name: string;
  copies: InstalledCopy[];
  /** The entry's source, so a push can name where the edits are going. */
  source: Source;
  /** Installed entries that depend on this one, for the removal warning. */
  affected: string[];
}>();
const emit = defineEmits<{ changed: [] }>();

/**
 * The one place the page asks "which copy", and it asks by being pressed.
 *
 * Install, push, and remove each used to carry their own scope picker, so the page put
 * the same question three times and answered it in three different vocabularies —
 * radios, a dropdown, and a list. Attaching the actions to the copy they act on removes
 * the question rather than harmonising it, and gives each action the copy's real path.
 *
 * One panel at a time, held as a single value so two open forms are not representable —
 * the same shape the catalog manager uses.
 */
type Panel = { scope: string; mode: "push" | "remove" };
const panel = ref<Panel | null>(null);

function show(scope: string, mode: Panel["mode"]) {
  const open = panel.value;
  panel.value = open && open.scope === scope && open.mode === mode ? null : { scope, mode };
}

function isOpen(scope: string, mode: Panel["mode"]): boolean {
  return panel.value?.scope === scope && panel.value.mode === mode;
}

watch(() => props.name, () => {
  panel.value = null;
});
</script>

<template>
  <section class="copies">
    <h3 class="copies__heading">On this machine ({{ copies.length }})</h3>

    <p v-if="!copies.length" class="copies__none">
      Not installed anywhere yet. Installing puts a copy in your Claude directory; the
      catalog entry above is only a pointer to where it comes from.
    </p>

    <ul v-else class="copies__list">
      <li
        v-for="copy in copies"
        :key="copy.scope + (copy.dest ?? '')"
        class="copies__item"
        :class="{ 'copies__item--open': panel?.scope === copy.scope }"
      >
        <div class="copies__line">
          <span class="copies__scope">{{ copy.scope }}</span>
          <code v-if="copy.dest" class="copies__dest">{{ copy.dest }}</code>
          <span v-else class="copies__dest copies__dest--unknown">
            put here by hand — the tool has no record of it
          </span>

          <span class="copies__actions">
            <button
              type="button"
              class="ghost"
              :aria-pressed="isOpen(copy.scope, 'push')"
              @click="show(copy.scope, 'push')"
            >
              Send edits back
            </button>
            <button
              v-if="copy.removable"
              type="button"
              class="ghost danger"
              :aria-pressed="isOpen(copy.scope, 'remove')"
              @click="show(copy.scope, 'remove')"
            >
              Remove
            </button>
          </span>
        </div>

        <!-- A receipt whose destination this app cannot resolve: real, worth showing, and
             not safely removable from here, because the scope would resolve elsewhere. -->
        <p v-if="!copy.removable" class="copies__caveat">
          Recorded by the tool but outside the directory this app resolves, so it can only
          be removed from that project.
        </p>

        <div v-if="panel?.scope === copy.scope" class="copies__panel fade-in">
          <PushControl
            v-if="panel.mode === 'push'"
            :name="name"
            :copy="copy"
            :source="source"
            @close="panel = null"
          />
          <UninstallControl
            v-else
            :name="name"
            :copy="copy"
            :affected="affected"
            @uninstalled="emit('changed')"
            @close="panel = null"
          />
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.copies {
  margin-top: 1.75rem;
}
.copies__heading {
  margin: 0 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.copies__none {
  margin: 0;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.7;
}
.copies__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.copies__item {
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.copies__item--open {
  background: rgba(128, 128, 128, 0.14);
}
.copies__line {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  padding: 0.45rem 0.6rem 0.45rem 0.85rem;
}
.copies__scope {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.65;
}
.copies__dest {
  flex: 1;
  min-width: 12rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}
.copies__dest--unknown {
  font-family: inherit;
  opacity: 0.6;
}
.copies__actions {
  display: flex;
  gap: 0.35rem;
}
.copies__actions button {
  padding: 0.25rem 0.6rem;
  font-size: 0.75rem;
}
.copies__actions button[aria-pressed="true"] {
  background: rgba(128, 128, 128, 0.25);
}
.copies__caveat {
  margin: 0;
  padding: 0 0.85rem 0.5rem;
  font-size: 0.74rem;
  line-height: 1.45;
  opacity: 0.65;
}
.copies__panel {
  padding: 0.2rem 0.85rem 0.85rem;
}
</style>
