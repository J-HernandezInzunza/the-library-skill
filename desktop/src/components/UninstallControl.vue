<script setup lang="ts">
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import type { InstalledCopy } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type UninstallReport } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** The copy being deleted. No scope list: you pressed this copy's button. */
  copy: InstalledCopy;
  /**
   * Installed entries that depend on this one, from the CLI's `dependents[]`.
   *
   * Removing the files leaves these satisfied on paper and broken on disk.
   */
  affected: string[];
}>();
const emit = defineEmits<{ uninstalled: []; close: [] }>();

const report = ref<UninstallReport | null>(null);
const running = ref(false);
const error = ref("");

/** The refusal awaiting its own, separate confirmation. */
const refused = computed(() => report.value?.refused ?? []);

async function remove(force: boolean) {
  running.value = true;
  error.value = "";
  try {
    const result = await withActivity(`removing the ${props.copy.scope} copy…`, () =>
      invoke<UninstallReport>("entry_uninstall", {
        name: props.name,
        scope: props.copy.scope,
        force,
      }),
    );
    report.value = result;
    if (result.deleted.length) emit("uninstalled");
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}
</script>

<template>
  <section class="uninstall">
    <StatusBanner v-if="error" kind="error" :detail="error" />
    <StatusBanner v-else-if="report?.deleted.length" kind="success">
      Removed {{ report.deleted.join(", ") }}. The catalog entry is still listed.
    </StatusBanner>

    <!-- A refusal is a second confirmation, never a retry: the CLI would not delete a
         destination it has no receipt for, and --force is only ever pressed from here. -->
    <div v-if="refused.length" class="uninstall__refused">
      <p class="uninstall__question">
        The tool has no install receipt for
        {{ refused.length === 1 ? "this path" : "these paths" }}, so it cannot prove it
        created {{ refused.length === 1 ? "it" : "them" }}:
      </p>
      <ul class="uninstall__paths">
        <li v-for="path in refused" :key="path"><code>{{ path }}</code></li>
      </ul>
      <p class="uninstall__note">
        Deleting anyway removes whatever is there, including anything you put there
        yourself.
      </p>
      <div class="uninstall__actions">
        <button type="button" class="ghost" @click="emit('close')">Leave it alone</button>
        <button type="button" class="danger" :disabled="running" @click="remove(true)">
          Delete anyway
        </button>
      </div>
    </div>

    <div v-else-if="!report" class="uninstall__confirm">
      <p class="uninstall__question">Delete the {{ copy.scope }} copy of {{ name }}?</p>
      <p v-if="copy.dest" class="uninstall__paths"><code>{{ copy.dest }}</code></p>

      <p v-if="affected.length" class="uninstall__affected">
        {{ affected.length }} installed
        {{ affected.length === 1 ? "entry depends" : "entries depend" }} on this and will be
        left incomplete: {{ affected.join(", ") }}.
      </p>
      <p class="uninstall__note">
        The catalog entry is untouched. Installing it again brings the files back.
      </p>
      <div class="uninstall__actions">
        <button type="button" class="ghost" @click="emit('close')">Cancel</button>
        <button type="button" class="danger" :disabled="running" @click="remove(false)">
          Delete
        </button>
      </div>
    </div>

    <Busy v-if="running" inline label="Removing files…" />
  </section>
</template>

<style scoped>
.uninstall__confirm,
.uninstall__refused {
  padding: 0.75rem;
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
  font-size: 0.85rem;
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
.uninstall__paths code {
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.uninstall__affected {
  margin: 0.5rem 0 0;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  font-size: 0.8rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
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
</style>
