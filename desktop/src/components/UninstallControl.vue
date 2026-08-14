<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeAppError, type Receipt, type UninstallReport } from "../types";
import Busy from "./Busy.vue";

const props = defineProps<{
  name: string;
  /** Scopes with a copy on disk, from `entry.scopes`. */
  scopes: string[];
  /** Receipts, which cover only what the tool itself installed. */
  installs: Receipt[];
}>();
const emit = defineEmits<{ uninstalled: [] }>();

/** The scope awaiting its first confirmation. */
const confirming = ref<string | null>(null);
/** The scope whose refusal is awaiting the second, separate confirmation. */
const escalating = ref<string | null>(null);
const report = ref<UninstallReport | null>(null);
const running = ref(false);
const error = ref("");

/**
 * The paths the confirmation names.
 *
 * Receipts cover only what the tool installed, so a hand-made copy has none — and that
 * is exactly the copy `uninstall` will refuse. The scope is named instead of inventing
 * a path the app would be guessing at.
 */
const confirmingPaths = computed(() =>
  props.installs.filter((install) => install.scope === confirming.value).map((i) => i.dest),
);

async function remove(scope: string, force: boolean) {
  running.value = true;
  error.value = "";
  try {
    const result = await invoke<UninstallReport>("entry_uninstall", {
      name: props.name,
      scope,
      force,
    });
    confirming.value = null;
    // A refusal is a report, not a retry cue: it opens a second confirmation naming
    // the paths, and nothing is deleted until the user answers that one too.
    escalating.value = result.refused.length ? scope : null;
    report.value = result;
    if (result.deleted.length) emit("uninstalled");
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

function cancel() {
  confirming.value = null;
  escalating.value = null;
}

watch(() => props.name, () => {
  cancel();
  report.value = null;
  error.value = "";
});
</script>

<template>
  <section v-if="scopes.length" class="uninstall">
    <h3 class="uninstall__heading">Remove installed copies</h3>

    <ul class="uninstall__scopes">
      <li v-for="scope in scopes" :key="scope" class="uninstall__scope">
        <span class="uninstall__scope-name">{{ scope }}</span>
        <button
          type="button"
          class="ghost"
          :disabled="running"
          @click="confirming = scope"
        >
          Remove
        </button>
      </li>
    </ul>

    <Busy v-if="running" inline label="Removing files…" />

    <div v-if="confirming" class="uninstall__confirm fade-in">
      <p class="uninstall__question">
        Delete the {{ confirming }} copy of {{ name }}?
      </p>
      <ul v-if="confirmingPaths.length" class="uninstall__paths">
        <li v-for="path in confirmingPaths" :key="path"><code>{{ path }}</code></li>
      </ul>
      <p class="uninstall__note">
        The catalog entry is untouched. Installing it again brings the files back.
      </p>
      <div class="uninstall__actions">
        <button type="button" class="ghost" @click="cancel()">Cancel</button>
        <button type="button" :disabled="running" @click="remove(confirming, false)">
          Delete
        </button>
      </div>
    </div>

    <div v-if="escalating && report" class="uninstall__refused fade-in">
      <p class="uninstall__question">
        The tool has no install receipt for
        {{ report.refused.length === 1 ? "this path" : "these paths" }}, so it cannot
        prove it created {{ report.refused.length === 1 ? "it" : "them" }}:
      </p>
      <ul class="uninstall__paths">
        <li v-for="path in report.refused" :key="path"><code>{{ path }}</code></li>
      </ul>
      <p class="uninstall__note">
        Deleting anyway removes whatever is there, including anything you put there
        yourself.
      </p>
      <div class="uninstall__actions">
        <button type="button" class="ghost" @click="cancel()">Leave it alone</button>
        <button type="button" :disabled="running" @click="remove(escalating, true)">
          Delete anyway
        </button>
      </div>
    </div>

    <p v-if="report?.deleted.length" class="uninstall__done fade-in">
      Removed {{ report.deleted.join(", ") }}. The catalog entry is still listed.
    </p>

    <pre v-if="error" class="uninstall__error">{{ error }}</pre>
  </section>
</template>

<style scoped>
.uninstall {
  margin-top: 1.75rem;
}
.uninstall__heading {
  margin: 0 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.uninstall__scopes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.uninstall__scope {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.4rem 0.85rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
  font-size: 0.85rem;
}
.uninstall__scope-name {
  flex: 1;
}
.uninstall__confirm,
.uninstall__refused {
  margin-top: 0.75rem;
  padding: 0.85rem;
  border-radius: 8px;
  background: rgba(220, 38, 38, 0.08);
  border-left: 3px solid #dc2626;
}
.uninstall__refused {
  background: rgba(245, 158, 11, 0.14);
  border-left-color: #f59e0b;
}
.uninstall__question {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.45;
  font-weight: 600;
}
.uninstall__paths {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}
.uninstall__note {
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.8;
}
.uninstall__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.uninstall__done {
  margin: 0.75rem 0 0;
  font-size: 0.83rem;
  color: #16a34a;
  overflow-wrap: anywhere;
}
.uninstall__error {
  margin: 0.75rem 0 0;
  padding: 1rem;
  border-radius: 8px;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
</style>
