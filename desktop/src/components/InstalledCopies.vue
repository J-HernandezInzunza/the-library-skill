<script setup lang="ts">
import { ref, watch } from "vue";
import type { InstalledCopy } from "../catalog";
import type { Source } from "../types";
import PushControl from "./PushControl.vue";
import UninstallControl from "./UninstallControl.vue";

const props = defineProps<{
  name: string;
  copies: InstalledCopy[];
  /**
   * The catalog whose copy this page is about.
   *
   * Both catalogs' copies land at the same destination, so without this the page cannot
   * tell "your copy is installed" from "the other catalog's copy is sitting where yours
   * would go" — and those call for opposite next actions.
   */
  subject: string;
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

    <!-- Copies in a project this app is not anchored at are not listed and not counted:
         installing into a project hands the files over, and a row for one would be a
         record of something this app can no longer refresh, remove, or vouch for. -->
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

          <!-- The one fact that distinguishes two copies once they are on disk. Silent
               when they agree, which is the common case and not worth a line. -->
          <span
            v-if="copy.fromCatalog && copy.fromCatalog !== subject"
            class="copies__foreign"
          >
            from {{ copy.fromCatalog }}, not {{ subject }}
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
              type="button"
              class="ghost danger"
              :aria-pressed="isOpen(copy.scope, 'remove')"
              @click="show(copy.scope, 'remove')"
            >
              Remove
            </button>
          </span>
        </div>

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
  background: var(--surface-raised);
}
.copies__item--open {
  background: var(--surface-hover);
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
.copies__foreign {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--status-attention-tint);
  color: var(--status-attention-ink);
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
  background: var(--surface-strong);
}
.copies__panel {
  padding: 0.2rem 0.85rem 0.85rem;
}
</style>
