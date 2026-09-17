<script setup lang="ts">
import { computed } from "vue";
import { catalogHue } from "../catalog";
import type { Catalog } from "../types";

const props = defineProps<{
  catalog: Catalog;
  /**
   * Whether to offer the way into managing this catalog's entries.
   *
   * Decided by the caller rather than derived here, because it has to carry the *same* gate
   * the registry puts on its own "Manage entries": the page both open renders Edit and
   * Remove on every row unconditionally, which it can only do while every door into it is
   * gated. A shortcut that skipped the check would offer writes the CLI refuses.
   */
  manageable: boolean;
}>();
const emit = defineEmits<{ manage: [] }>();

const writeMode = computed(() => {
  if (!props.catalog.writable) return "read-only";
  // `pr` means a write lands on a branch and needs review, which is worth saying
  // in full — it changes what "add an entry here" costs you.
  if (props.catalog.write_mode === "pr") return "writes via pull request";
  return "writes directly";
});
</script>

<template>
  <section
    class="catalog-summary"
    :style="{ '--catalog-hue': catalogHue(catalog.precedence) }"
  >
    <div class="catalog-summary__line">
      <p class="catalog-summary__meta">
        <span class="catalog-summary__rank">precedence {{ catalog.precedence }}</span>
        <span>{{ catalog.kind }}</span>
        <span>{{ writeMode }}</span>
        <span class="catalog-summary__location">{{ catalog.location }}</span>
      </p>

      <!-- This strip is the one block on the catalog list whose subject is the catalog
           rather than the entries, so the way into managing it belongs here rather than in
           the head row, which must not change width as tabs are clicked.

           Absent rather than disabled where the app will not write to the catalog: the
           write mode printed to its left — "read-only", "writes via pull request" — is
           already the reason, standing where the button would have been. -->
      <button
        v-if="manageable"
        type="button"
        class="ghost btn-xs catalog-summary__manage"
        @click="emit('manage')"
      >
        Manage entries
      </button>
    </div>

    <p v-if="catalog.skipped" class="catalog-summary__skipped">
      This catalog was skipped, so nothing below comes from it: {{ catalog.skipped }}
    </p>
  </section>
</template>

<style scoped>
.catalog-summary {
  margin: 0 0 1rem;
  padding: 0.6rem 0.8rem;
  border-left: 3px solid var(--catalog-edge);
  border-radius: 0 8px 8px 0;
  background: var(--catalog-wash);
}
.catalog-summary__line {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.catalog-summary__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0;
  /* The button beside it now takes a share of the row, so the meta has to be allowed to
     shrink — a flex item's `auto` minimum would push a long path out past the strip. */
  min-width: 0;
  font-size: 0.78rem;
  opacity: 0.75;
}
.catalog-summary__rank {
  font-weight: 600;
  opacity: 0.9;
}
.catalog-summary__location {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  /* A filesystem path has no break opportunities of its own, so shrinking the space it
     gets is not enough to keep it inside the strip. */
  overflow-wrap: anywhere;
}
.catalog-summary__manage {
  /* `flex: none` because the meta next to it will take every pixel it is offered: without
     this the button is what gives way, and a squeezed button reads as a rendering fault
     long before it has finished disappearing. */
  flex: none;
  margin-left: auto;
}
.catalog-summary__skipped {
  margin: 0.5rem 0 0;
  font-size: 0.82rem;
  color: var(--status-attention-ink);
}
</style>
