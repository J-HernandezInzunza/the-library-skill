<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeDestState, installPlan, summarizeChanges } from "../catalog";
import { describeAppError, type UsePreview, type UseReport } from "../types";

const props = defineProps<{ name: string }>();
const emit = defineEmits<{ installed: [] }>();

const preview = ref<UsePreview | null>(null);
const report = ref<UseReport | null>(null);
const loading = ref(false);
const installing = ref(false);
const error = ref("");
/** Ticked by hand when the plan would discard local edits. */
const acknowledged = ref(false);

const plan = computed(() => {
  if (!preview.value) return null;
  return installPlan(preview.value, props.name);
});

/** The catalog `overrides` refers to — the requested entry's, never a dependency's. */
const winningCatalog = computed(
  () => plan.value?.items.find((item) => item.target)?.install.catalog ?? "",
);

const canInstall = computed(() => {
  if (!plan.value || installing.value) return false;
  return !plan.value.blocked || acknowledged.value;
});

/** Installed, but the main file the type expects is not there. */
const unverified = computed(() => report.value?.installed.filter((item) => !item.verified) ?? []);

async function runPreview() {
  loading.value = true;
  error.value = "";
  report.value = null;
  try {
    preview.value = await invoke<UsePreview>("entry_use_preview", { name: props.name });
  } catch (e) {
    error.value = describeAppError(e);
    preview.value = null;
  } finally {
    loading.value = false;
  }
}

async function install() {
  installing.value = true;
  error.value = "";
  try {
    report.value = await invoke<UseReport>("entry_use", { name: props.name });
    // The plan described the disk as it was before the write, so it is now a lie.
    preview.value = null;
    acknowledged.value = false;
    emit("installed");
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    installing.value = false;
  }
}

// A plan resolved for one entry says nothing about the next one.
watch(
  () => props.name,
  () => {
    preview.value = null;
    report.value = null;
    error.value = "";
    acknowledged.value = false;
  },
);
</script>

<template>
  <section class="install-preview">
    <h3 class="install-preview__heading">Install</h3>

    <button type="button" class="ghost" :disabled="loading" @click="runPreview()">
      {{ loading ? "Resolving…" : plan ? "Re-check" : "Preview install" }}
    </button>

    <pre v-if="error" class="install-preview__error">{{ error }}</pre>

    <template v-if="plan">
      <p v-if="plan.blocked" class="install-preview__warning">
        Installing overwrites local edits that the tool did not make. The edited copies
        are marked below; they cannot be recovered afterwards.
      </p>

      <p class="install-preview__scope">
        {{ preview?.scope }} · nothing has been written
        <span v-if="preview?.overrides.length">
          · installing the {{ winningCatalog }} copy, over {{ preview.overrides.join(", ") }}
        </span>
      </p>

      <ul class="install-preview__plan">
        <li
          v-for="item in plan.items"
          :key="item.install.dest"
          class="install-preview__item"
          :class="{ 'install-preview__item--drifted': item.drifted }"
        >
          <span class="install-preview__item-head">
            <strong>{{ item.install.name }}</strong>
            <span v-if="!item.target" class="install-preview__role">dependency</span>
            <span
              class="install-preview__state"
              :class="{ 'install-preview__state--drifted': item.drifted }"
            >
              {{ describeDestState(item.install.state) }}
            </span>
          </span>
          <code class="install-preview__dest">{{ item.install.dest }}</code>
        </li>
      </ul>

      <label v-if="plan.blocked" class="install-preview__ack">
        <input v-model="acknowledged" type="checkbox" />
        Overwrite {{ plan.drifted.length }} locally edited
        {{ plan.drifted.length === 1 ? "copy" : "copies" }}, discarding those edits.
      </label>

      <button
        type="button"
        class="install-preview__go"
        :disabled="!canInstall"
        @click="install()"
      >
        {{ installing ? "Installing…" : "Install globally" }}
      </button>
    </template>

    <template v-if="report">
      <p class="install-preview__done">
        Installed {{ report.installed.length }}
        {{ report.installed.length === 1 ? "item" : "items" }}.
      </p>

      <p v-if="unverified.length" class="install-preview__warning">
        {{ unverified.map((item) => item.name).join(", ") }} landed, but the main file the
        catalog expects is not there. The copy is on disk; the catalog entry needs fixing.
      </p>

      <ul class="install-preview__plan">
        <li
          v-for="item in report.installed"
          :key="item.dest"
          class="install-preview__item"
        >
          <span class="install-preview__item-head">
            <strong>{{ item.name }}</strong>
            <span class="install-preview__state">{{ summarizeChanges(item.changes) }}</span>
          </span>
          <code class="install-preview__dest">{{ item.dest }}</code>
          <ul v-if="!item.changes.new_install" class="install-preview__files">
            <li v-for="file in item.changes.modified" :key="`~${file}`">~ {{ file }}</li>
            <li v-for="file in item.changes.added" :key="`+${file}`">+ {{ file }}</li>
            <li v-for="file in item.changes.removed" :key="`-${file}`">- {{ file }}</li>
          </ul>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.install-preview {
  margin-top: 1.75rem;
}
.install-preview__heading {
  margin: 0 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.install-preview__error {
  margin: 0.75rem 0 0;
  padding: 1rem;
  border-radius: 8px;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.install-preview__warning {
  margin: 0.75rem 0 0;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  font-size: 0.82rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
}
.install-preview__scope {
  margin: 0.75rem 0 0.5rem;
  font-size: 0.75rem;
  opacity: 0.6;
}
.install-preview__plan {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.install-preview__item {
  padding: 0.5rem 0.85rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.install-preview__item--drifted {
  border-left: 3px solid #f59e0b;
}
.install-preview__item-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
}
.install-preview__role {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.55;
}
.install-preview__state {
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: rgba(128, 128, 128, 0.2);
}
.install-preview__state--drifted {
  background: rgba(245, 158, 11, 0.2);
  color: #b45309;
  font-weight: 600;
}
.install-preview__dest {
  display: block;
  margin-top: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.65;
  overflow-wrap: anywhere;
}
.install-preview__files {
  list-style: none;
  margin: 0.35rem 0 0;
  padding: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
}
.install-preview__ack {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  margin: 0.75rem 0 0;
  font-size: 0.82rem;
  line-height: 1.4;
}
.install-preview__go {
  margin-top: 0.75rem;
}
.install-preview__go:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.install-preview__done {
  margin: 0.75rem 0 0.5rem;
  font-size: 0.85rem;
  color: #16a34a;
  font-weight: 600;
}
</style>
