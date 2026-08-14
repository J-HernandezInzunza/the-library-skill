<script setup lang="ts">
import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { withActivity } from "../commandActivity";
import { describeAppError, type DoctorItem, type DoctorReport } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import PageHeader from "./PageHeader.vue";

/** Reached from the catalog list and from the Catalogs view, so Back names where it goes. */
defineProps<{ backTo: string }>();
defineEmits<{ close: [] }>();

const report = ref<DoctorReport | null>(null);
const deep = ref(false);
const loading = ref(false);
const error = ref("");

async function run() {
  loading.value = true;
  error.value = "";
  try {
    report.value = await withActivity("checking the catalog…", () =>
      invoke<DoctorReport>("catalog_doctor", { deep: deep.value }),
    );
  } catch (e) {
    error.value = describeAppError(e);
    report.value = null;
  } finally {
    loading.value = false;
  }
}

/** Where a finding applies, when it names something specific. */
function subject(item: DoctorItem): string {
  return item.entry ?? item.catalog ?? "";
}

run();
</script>

<template>
  <section class="view">
    <PageHeader title="Catalog health" :back="`Back to ${backTo}`" @back="$emit('close')">
      <label class="doctor__deep">
        <input v-model="deep" type="checkbox" @change="run()" />
        Deep checks (reaches the network)
      </label>
      <button type="button" class="ghost" :disabled="loading" @click="run()">
        {{ loading ? "Checking…" : "Re-run" }}
      </button>
    </PageHeader>

    <Busy v-if="loading" :label="deep ? 'Checking every source…' : 'Checking the catalog…'" />
    <StatusBanner v-else-if="error" kind="error" :detail="error" />

    <template v-else-if="report">
      <p class="doctor__summary fade-in">
        {{ report.entries }} entries · {{ report.errors.length }} errors ·
        {{ report.warnings.length }} warnings
      </p>

      <p v-if="!report.errors.length && !report.warnings.length" class="doctor__clean">
        Nothing to report.
      </p>

      <template v-if="report.errors.length">
        <h3 class="doctor__section doctor__section--error">Errors</h3>
        <ul class="doctor__list fade-in">
          <li v-for="(item, i) in report.errors" :key="`e${i}`" class="doctor__item doctor__item--error">
            <span v-if="subject(item)" class="doctor__subject">{{ subject(item) }}</span>
            <span class="doctor__message">{{ item.message }}</span>
          </li>
        </ul>
      </template>

      <template v-if="report.warnings.length">
        <h3 class="doctor__section">Warnings</h3>
        <ul class="doctor__list fade-in">
          <li v-for="(item, i) in report.warnings" :key="`w${i}`" class="doctor__item">
            <span v-if="subject(item)" class="doctor__subject">{{ subject(item) }}</span>
            <span class="doctor__message">{{ item.message }}</span>
          </li>
        </ul>
      </template>
    </template>
  </section>
</template>

<style scoped>
.doctor__deep {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  opacity: 0.75;
}
.doctor__summary {
  margin: 0.75rem 0 0;
  font-size: 0.85rem;
  opacity: 0.7;
}
.doctor__clean,
.doctor__section {
  margin: 1.5rem 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.doctor__section--error {
  color: #dc2626;
  opacity: 0.85;
}
.doctor__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.doctor__item {
  padding: 0.55rem 0.85rem;
  border-radius: 8px;
  border-left: 3px solid rgba(234, 179, 8, 0.6);
  background: rgba(128, 128, 128, 0.08);
  font-size: 0.83rem;
  line-height: 1.45;
}
.doctor__item--error {
  border-left-color: #dc2626;
}
.doctor__subject {
  display: inline-block;
  margin-right: 0.4rem;
  padding: 0.05rem 0.4rem;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.2);
  font-weight: 600;
  font-size: 0.75rem;
}
.doctor__message {
  overflow-wrap: anywhere;
  opacity: 0.85;
}
</style>
