<script setup lang="ts">
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeDependencyWrite, summarizeChanges } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type SyncedItem, type SyncReport } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import PageHeader from "./PageHeader.vue";

const emit = defineEmits<{ close: []; synced: [] }>();

const report = ref<SyncReport | null>(null);
const loading = ref(false);
const error = ref("");

/**
 * Whether the refresh actually rewrote files.
 *
 * Not the same as `!up_to_date`: one commit anywhere in a catalog moves the source head
 * for every entry in it, so a whole library can be re-fetched with a file or two
 * genuinely different. Grouping on the fetch buries those in a wall of "no changes".
 */
function touchedFiles(item: SyncedItem): boolean {
  const { new_install, added, removed, modified } = item.changes;
  return new_install || added.length > 0 || removed.length > 0 || modified.length > 0;
}

const changed = computed(() => report.value?.synced.filter(touchedFiles) ?? []);
const unchanged = computed(() => report.value?.synced.filter((item) => !touchedFiles(item)) ?? []);
/**
 * Items whose local edits the refresh discarded.
 *
 * `state` is read before the refresh, so this is the only place that can be said.
 */
const overwritten = computed(
  () => report.value?.synced.filter((item) => item.state === "drifted" && !item.up_to_date) ?? [],
);

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

/** One line per file the refresh touched, glyph and colour keyed to the kind of change. */
function fileLines(item: SyncedItem) {
  const { modified, added, removed } = item.changes;
  return [
    ...modified.map((path) => ({ path, glyph: "~", kind: "modified" })),
    ...added.map((path) => ({ path, glyph: "+", kind: "added" })),
    ...removed.map((path) => ({ path, glyph: "-", kind: "removed" })),
  ];
}

run(false);
</script>

<template>
  <section class="view">
    <PageHeader title="Sync" back="The Library" @back="$emit('close')">
      <template #actions>
        <button type="button" class="ghost" :disabled="loading" @click="run(false)">
          {{ loading ? "Syncing…" : "Sync again" }}
        </button>
        <button type="button" class="ghost" :disabled="loading" @click="run(true)">
          Force re-fetch
        </button>
      </template>

      <Busy v-if="loading" label="Checking every installed entry against its source…" />
      <StatusBanner v-else-if="error" kind="error" :detail="error" />

      <template v-else-if="report">
        <p class="sync__summary fade-in">
          <strong v-if="changed.length" class="sync__count">{{ changed.length }} updated</strong>
          <strong v-else class="sync__count sync__count--quiet">Nothing changed</strong>
          <span> · {{ unchanged.length }} already up to date</span>
          <span v-if="report.failed.length" class="sync__count--failed">
            · {{ report.failed.length }} failed
          </span>
          <span v-if="report.missing.length" class="sync__count--gone">
            · {{ report.missing.length }} gone from disk
          </span>
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

        <template v-if="report.missing.length">
          <h3 class="sync__section sync__section--gone">
            Gone from disk · {{ report.missing.length }}
          </h3>
          <!-- Said once above the list rather than on every row: a machine restored from
               backup has this state on everything, and the advice is the same for all of it. -->
          <p class="sync__note">
            The record was kept and nothing was deleted. Install one of these again to put a
            copy back, or uninstall it to drop the record.
          </p>
          <ul class="sync__list fade-in">
            <li v-for="item in report.missing" :key="item.dest" class="sync__item sync__item--gone">
              <div class="sync__head">
                <span class="sync__name">{{ item.name }}</span>
                <span class="sync__detail">{{ item.scope }}</span>
              </div>
              <p class="sync__path">{{ item.dest }}</p>
            </li>
          </ul>
        </template>

        <template v-if="changed.length">
          <h3 class="sync__section sync__section--changed">Updated · {{ changed.length }}</h3>
          <ul class="sync__list fade-in">
            <li
              v-for="item in changed"
              :key="item.name"
              class="sync__item sync__item--changed"
              :class="{ 'sync__item--drifted': item.state === 'drifted' }"
            >
              <div class="sync__head">
                <span class="sync__name">{{ item.name }}</span>
                <span class="sync__badge">{{ summarizeChanges(item.changes) }}</span>
                <span v-if="item.state === 'drifted'" class="sync__badge sync__badge--warn">
                  local edits replaced
                </span>
                <span class="sync__detail">{{ item.scope }}</span>
              </div>
              <ul class="sync__files">
                <li
                  v-for="line in fileLines(item)"
                  :key="`${line.kind}:${line.path}`"
                  :class="`sync__file sync__file--${line.kind}`"
                >
                  <span class="sync__glyph">{{ line.glyph }}</span> {{ line.path }}
                </li>
              </ul>
            </li>
          </ul>
        </template>

        <template v-if="report.dependencies.length">
          <h3 class="sync__section sync__section--changed">
            Also written · {{ report.dependencies.length }}
          </h3>
          <ul class="sync__list fade-in">
            <li
              v-for="item in report.dependencies"
              :key="`${item.type}:${item.name}:${item.scope}`"
              class="sync__item sync__item--changed"
            >
              <div class="sync__head">
                <span class="sync__name">{{ item.name }}</span>
                <span class="sync__badge">{{ describeDependencyWrite(item.state) }}</span>
                <span class="sync__detail">
                  required by {{ item.required_by }} · {{ item.scope }}
                </span>
              </div>
            </li>
          </ul>
        </template>

        <details v-if="unchanged.length" class="sync__unchanged fade-in">
          <summary class="sync__unchanged-summary">
            {{ unchanged.length }} entries unchanged
          </summary>
          <ul class="sync__list">
            <li v-for="item in unchanged" :key="item.name" class="sync__item sync__item--quiet">
              <span class="sync__name">{{ item.name }}</span>
              <span class="sync__detail">{{ item.scope }}</span>
            </li>
          </ul>
        </details>
      </template>
    </PageHeader>
  </section>
</template>

<style scoped>
.sync__summary {
  margin: 0.75rem 0 0;
  font-size: 0.85rem;
  opacity: 0.7;
}
.sync__count {
  font-weight: 600;
  color: var(--accent-bright);
  opacity: 1;
}
.sync__count--quiet {
  color: inherit;
  font-weight: 500;
}
.sync__count--failed {
  color: var(--status-danger-ink);
}
.sync__count--gone {
  color: var(--status-attention-ink);
}
.sync__warning {
  margin: 0.75rem 0 0;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.sync__section {
  margin: 1.5rem 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.sync__section--error {
  color: var(--status-danger-ink);
  opacity: 0.85;
}
.sync__section--changed {
  color: var(--accent-bright);
  opacity: 0.9;
}
.sync__section--gone {
  color: var(--status-attention-ink);
  opacity: 0.85;
}
.sync__note {
  margin: 0 0 0.5rem;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.7;
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
  background: var(--surface-raised);
  font-size: 0.83rem;
}
.sync__item--error {
  border-left: 3px solid var(--status-danger-ink);
}
.sync__item--changed {
  padding: 0.7rem 0.85rem;
  border-left: 3px solid var(--accent-bright);
  background: var(--accent-tint);
}
.sync__item--drifted {
  border-left-color: var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.sync__item--gone {
  border-left: 3px solid var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.sync__item--quiet {
  opacity: 0.6;
}
.sync__path {
  margin: 0.3rem 0 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.7;
  overflow-wrap: anywhere;
}
.sync__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
}
.sync__name {
  font-weight: 600;
  margin-right: 0.5rem;
}
.sync__badge {
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--accent-ink);
  background: var(--accent-tint);
}
.sync__badge--warn {
  color: var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.sync__detail {
  opacity: 0.7;
  overflow-wrap: anywhere;
}
.sync__files {
  list-style: none;
  margin: 0.4rem 0 0;
  padding: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
}
.sync__glyph {
  display: inline-block;
  width: 0.8rem;
  font-weight: 700;
}
.sync__file--added {
  color: var(--status-ok-ink);
}
.sync__file--modified {
  color: var(--status-attention-ink);
}
.sync__file--removed {
  color: var(--status-danger-ink);
}
.sync__unchanged {
  margin-top: 1.5rem;
}
.sync__unchanged-summary {
  cursor: pointer;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
  margin-bottom: 0.5rem;
}

</style>
