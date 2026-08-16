<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeDestState, installPlan } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type UsePreview, type UseReport } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The ticked names, in list order. */
  names: string[];
  /** The catalog they were picked from, for the confirmation to name. */
  catalogId: string;
}>();
const emit = defineEmits<{ installed: []; clear: [] }>();

const preview = ref<UsePreview | null>(null);
const report = ref<UseReport | null>(null);
const acknowledged = ref(false);
const running = ref(false);
const failure = ref("");

/**
 * One plan for the whole selection, which is why this is one command rather than N.
 *
 * The drift gate is per-plan (T3.1): installing ten entries as ten calls would mean ten
 * acknowledgements, or — far more likely — none at all.
 */
const plan = computed(() => (preview.value ? installPlan(preview.value, props.names) : null));

/** Dependencies dragged in by the selection, which the user did not tick. */
const extras = computed(() => plan.value?.items.filter((item) => !item.target) ?? []);

const canInstall = computed(
  () => !!plan.value && !running.value && (!plan.value.blocked || acknowledged.value),
);

async function runPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  acknowledged.value = false;
  try {
    preview.value = await withActivity(`planning ${props.names.length} installs…`, () =>
      invoke<UsePreview>("entry_use_preview", { names: props.names }),
    );
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

async function install() {
  running.value = true;
  failure.value = "";
  try {
    report.value = await withActivity(`installing ${props.names.length} entries…`, () =>
      invoke<UseReport>("entry_use", { names: props.names }),
    );
    preview.value = null;
    emit("installed");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

// A changed selection describes a different install, so the plan built from the previous
// one is not stale so much as about something else.
watch(() => props.names, () => {
  preview.value = null;
  acknowledged.value = false;
});
</script>

<template>
  <section class="bulk card">
    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="report" kind="success">
      <p class="bulk__done">
        Installed {{ report.requested.length }}
        {{ report.requested.length === 1 ? "entry" : "entries" }} from {{ catalogId }}<template
          v-if="report.installed.length > report.requested.length"
        >
          , with {{ report.installed.length - report.requested.length }} dependencies</template
        >.
      </p>
    </StatusBanner>

    <div class="bulk__bar">
      <span class="bulk__count">
        <template v-if="names.length">
          {{ names.length }} selected from {{ catalogId }}
        </template>
        <template v-else>
          Pick the entries you want, or use <strong>Select all</strong> above.
        </template>
      </span>
      <button
        v-if="names.length"
        type="button"
        class="ghost"
        @click="emit('clear')"
      >
        Clear
      </button>
      <button v-if="names.length" type="button" :disabled="running" @click="runPreview()">
        {{ preview ? "Re-check" : "Preview install" }}
      </button>
    </div>

    <Busy v-if="running && !preview" inline label="Resolving destinations…" />

    <div v-if="plan" class="bulk__plan fade-in">
      <p class="bulk__scope">
        {{ preview?.scope }} · nothing has been written ·
        {{ plan.items.length }} {{ plan.items.length === 1 ? "destination" : "destinations" }}
        <template v-if="extras.length">
          ({{ extras.length }} pulled in as dependencies)
        </template>
      </p>

      <p v-if="plan.blocked" class="bulk__warning">
        {{ plan.drifted.length }} of these have local edits the tool did not make.
        Installing replaces them, and they cannot be recovered afterwards.
      </p>

      <ul class="bulk__items">
        <li
          v-for="item in plan.items"
          :key="item.install.dest"
          class="bulk__item"
          :class="{ 'bulk__item--drifted': item.drifted }"
        >
          <span class="bulk__item-name">{{ item.install.name }}</span>
          <span v-if="!item.target" class="bulk__role">dependency</span>
          <span class="bulk__state" :class="{ 'bulk__state--drifted': item.drifted }">
            {{ describeDestState(item.install.state) }}
          </span>
        </li>
      </ul>

      <label v-if="plan.blocked" class="bulk__ack">
        <input v-model="acknowledged" type="checkbox" />
        Overwrite {{ plan.drifted.length }} locally edited
        {{ plan.drifted.length === 1 ? "copy" : "copies" }}, discarding those edits.
      </label>

      <button type="button" :disabled="!canInstall" @click="install()">
        Install {{ plan.items.length }} globally
      </button>
      <Busy v-if="running" inline label="Fetching and writing files…" />
    </div>
  </section>
</template>

<style scoped>
.bulk {
  margin-bottom: 1rem;
}
.bulk__bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.bulk__count {
  flex: 1;
  font-size: 0.82rem;
  opacity: 0.8;
}
.bulk__bar button {
  padding: 0.35rem 0.7rem;
  font-size: 0.8rem;
}
.bulk__scope {
  margin: 0.75rem 0 0;
  font-size: 0.75rem;
  opacity: 0.65;
}
.bulk__warning {
  margin: 0.6rem 0 0;
  padding: 0.55rem 0.7rem;
  border-radius: 6px;
  font-size: 0.8rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
}
.bulk__items {
  list-style: none;
  margin: 0.6rem 0 0;
  padding: 0;
  max-height: 16rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.bulk__item {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 6px;
  font-size: 0.8rem;
}
.bulk__item--drifted {
  background: rgba(245, 158, 11, 0.12);
}
.bulk__item-name {
  flex: 1;
  font-weight: 500;
}
.bulk__role {
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.5;
}
.bulk__state {
  font-size: 0.72rem;
  opacity: 0.65;
}
.bulk__state--drifted {
  color: #b45309;
  opacity: 1;
}
.bulk__ack {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  margin: 0.7rem 0;
  font-size: 0.8rem;
  line-height: 1.45;
}
.bulk__plan > button:last-of-type {
  margin-top: 0.7rem;
}
.bulk__done {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
</style>
