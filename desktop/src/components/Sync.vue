<script setup lang="ts">
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { summarizeChanges } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type SyncedItem, type SyncReport } from "../types";
import Busy from "./Busy.vue";

const emit = defineEmits<{ close: []; synced: [] }>();

const report = ref<SyncReport | null>(null);
const loading = ref(false);
const error = ref("");

const refreshed = computed(() => report.value?.synced.filter((item) => !item.up_to_date) ?? []);
const unchanged = computed(() => report.value?.synced.filter((item) => item.up_to_date) ?? []);
/**
 * Items whose local edits the refresh discarded.
 *
 * `state` is read before the refresh, so this is the only place that can be said.
 */
const overwritten = computed(() => refreshed.value.filter((item) => item.state === "drifted"));

async function run(force: boolean) {
  loading.value = true;
  error.value = "";
  try {
    report.value = await withActivity("syncing installed entries…", () =>
      invoke<SyncReport>("catalog_sync", { force }),
    );
    emit("synced");
  } catch (e) {
    error.value = describeAppError(e);
    report.value = null;
  } finally {
    loading.value = false;
  }
}

function describeItem(item: SyncedItem): string {
  return `${item.scope} · ${summarizeChanges(item.changes)}`;
}

run(false);
</script>

<template>
  <section class="sync">
    <button type="button" class="ghost sync__back" @click="$emit('close')">
      ← Back to catalog
    </button>

    <header class="sync__head">
      <h2 class="sync__title">Sync</h2>
      <button type="button" class="ghost" :disabled="loading" @click="run(false)">
        {{ loading ? "Syncing…" : "Sync again" }}
      </button>
      <button type="button" class="ghost" :disabled="loading" @click="run(true)">
        Force re-fetch
      </button>
    </header>

    <Busy v-if="loading" label="Checking every installed entry against its source…" />
    <pre v-else-if="error" class="sync__error">{{ error }}</pre>

    <template v-else-if="report">
      <p class="sync__summary fade-in">
        {{ refreshed.length }} refreshed · {{ unchanged.length }} already up to date
        <span v-if="report.failed.length"> · {{ report.failed.length }} failed</span>
      </p>

      <p v-if="overwritten.length" class="sync__warning">
        {{ overwritten.map((item) => item.name).join(", ") }} had local edits, which the
        refresh replaced with the catalog's copy.
      </p>

      <template v-if="report.failed.length">
        <h3 class="sync__section sync__section--error">Failed</h3>
        <ul class="sync__list fade-in">
          <li v-for="item in report.failed" :key="item.name" class="sync__item sync__item--error">
            <span class="sync__name">{{ item.name }}</span>
            <span class="sync__detail">{{ item.reason }}</span>
          </li>
        </ul>
      </template>

      <template v-if="refreshed.length">
        <h3 class="sync__section">Refreshed</h3>
        <ul class="sync__list fade-in">
          <li
            v-for="item in refreshed"
            :key="item.name"
            class="sync__item"
            :class="{ 'sync__item--drifted': item.state === 'drifted' }"
          >
            <span class="sync__name">{{ item.name }}</span>
            <span class="sync__detail">{{ describeItem(item) }}</span>
            <ul class="sync__files">
              <li v-for="file in item.changes.modified" :key="`~${file}`">~ {{ file }}</li>
              <li v-for="file in item.changes.added" :key="`+${file}`">+ {{ file }}</li>
              <li v-for="file in item.changes.removed" :key="`-${file}`">- {{ file }}</li>
            </ul>
          </li>
        </ul>
      </template>

      <template v-if="unchanged.length">
        <h3 class="sync__section">Already up to date</h3>
        <ul class="sync__list fade-in">
          <li v-for="item in unchanged" :key="item.name" class="sync__item sync__item--quiet">
            <span class="sync__name">{{ item.name }}</span>
            <span class="sync__detail">{{ item.scope }} · nothing to fetch</span>
          </li>
        </ul>
      </template>
    </template>
  </section>
</template>

<style scoped>
.sync {
  /* The topbar is not rendered in this view, and it owns the app's top padding. */
  padding: 1.5rem 0 2rem;
}
.sync__back {
  margin-bottom: 1rem;
}
.sync__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.sync__title {
  margin: 0;
  font-size: 1.3rem;
  flex: 1;
}
.sync__summary {
  margin: 0.75rem 0 0;
  font-size: 0.85rem;
  opacity: 0.7;
}
.sync__error {
  margin-top: 1rem;
  padding: 1rem;
  border-radius: 8px;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.sync__warning {
  margin: 0.75rem 0 0;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  font-size: 0.82rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
}
.sync__section {
  margin: 1.5rem 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.sync__section--error {
  color: #dc2626;
  opacity: 0.85;
}
.sync__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.sync__item {
  padding: 0.55rem 0.85rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
  font-size: 0.83rem;
}
.sync__item--error {
  border-left: 3px solid #dc2626;
}
.sync__item--drifted {
  border-left: 3px solid #f59e0b;
}
.sync__item--quiet {
  opacity: 0.6;
}
.sync__name {
  font-weight: 600;
  margin-right: 0.5rem;
}
.sync__detail {
  opacity: 0.7;
  overflow-wrap: anywhere;
}
.sync__files {
  list-style: none;
  margin: 0.3rem 0 0;
  padding: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
}
</style>
