<script setup lang="ts">
import { catalogHue } from "../catalog";
import type { Catalog } from "../types";

defineProps<{ catalogs: Catalog[] }>();

/** The catalog being browsed; `null` browses every catalog's winning entries. */
const active = defineModel<string | null>({ required: true });
</script>

<template>
  <nav class="catalog-tabs">
    <button
      type="button"
      class="catalog-tabs__tab"
      :class="{ 'catalog-tabs__tab--active': active === null }"
      @click="active = null"
    >
      All
    </button>

    <button
      v-for="catalog in catalogs"
      :key="catalog.id"
      type="button"
      class="catalog-tabs__tab"
      :class="{
        'catalog-tabs__tab--active': active === catalog.id,
        'catalog-tabs__tab--skipped': !!catalog.skipped,
      }"
      :style="{ '--catalog-hue': catalogHue(catalog.precedence) }"
      @click="active = catalog.id"
    >
      <span class="catalog-tabs__dot" />
      {{ catalog.id }}
      <!-- A skipped catalog has no count, and showing 0 would read as "nothing shared". -->
      <span class="catalog-tabs__count">{{ catalog.skipped ? "—" : catalog.entries }}</span>
    </button>
  </nav>
</template>

<style scoped>
.catalog-tabs {
  display: flex;
  gap: 0.35rem;
  margin: 0 0 0.75rem;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}
.catalog-tabs__tab {
  --catalog-hue: 220;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.8rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 6px 6px 0 0;
  background: transparent;
  color: inherit;
  font-size: 0.9rem;
  font-weight: 500;
  opacity: 0.65;
}
.catalog-tabs__tab:hover {
  background: rgba(128, 128, 128, 0.1);
  opacity: 0.9;
}
.catalog-tabs__tab--active {
  border-bottom-color: hsl(var(--catalog-hue), 65%, 52%);
  opacity: 1;
}
.catalog-tabs__tab--skipped {
  text-decoration: line-through;
}
.catalog-tabs__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: hsl(var(--catalog-hue), 65%, 52%);
}
/* "All" has no catalog of its own, so it gets no colour. */
.catalog-tabs__tab:first-child .catalog-tabs__dot {
  display: none;
}
.catalog-tabs__count {
  font-size: 0.75rem;
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
}
</style>
