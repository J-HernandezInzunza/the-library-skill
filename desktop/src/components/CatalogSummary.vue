<script setup lang="ts">
import { computed } from "vue";
import { catalogHue } from "../catalog";
import type { Catalog } from "../types";

const props = defineProps<{ catalog: Catalog }>();

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
    <p class="catalog-summary__meta">
      <span class="catalog-summary__rank">precedence {{ catalog.precedence }}</span>
      <span>{{ catalog.kind }}</span>
      <span>{{ writeMode }}</span>
      <span class="catalog-summary__location">{{ catalog.location }}</span>
    </p>

    <p v-if="catalog.skipped" class="catalog-summary__skipped">
      This catalog was skipped, so nothing below comes from it: {{ catalog.skipped }}
    </p>
  </section>
</template>

<style scoped>
.catalog-summary {
  margin: 0 0 1rem;
  padding: 0.6rem 0.8rem;
  border-left: 3px solid hsl(var(--catalog-hue), 65%, 52%);
  border-radius: 0 8px 8px 0;
  background: hsl(var(--catalog-hue), 45%, 50%, 0.08);
}
.catalog-summary__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0;
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
}
.catalog-summary__skipped {
  margin: 0.5rem 0 0;
  font-size: 0.82rem;
  color: #b45309;
}
</style>
