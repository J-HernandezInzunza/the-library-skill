<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { purgeable } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type CatalogCopy,
  type Receipt,
  type RemovePreview,
  type RemoveReport,
} from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** The copy being removed. Only one catalog's entry goes away. */
  copy: CatalogCopy;
  /** Scopes with a copy on disk, from `entry.scopes` — disk-driven, not receipt-driven. */
  scopes: string[];
  /** Receipts, which supply the paths to name but not whether anything is installed. */
  installs: Receipt[];
}>();
const emit = defineEmits<{ removed: [] }>();

const preview = ref<RemovePreview | null>(null);
const report = ref<RemoveReport | null>(null);
const purge = ref(false);
const running = ref(false);
const failure = ref("");

/**
 * What a purge from here could actually delete.
 *
 * `--purge` resolves a project install against `LIBRARY_CWD`, which for this command is
 * the tool repo, so it reaches only the global copy. Offering the checkbox regardless
 * would tick a box that says "delete the installed copies" and leave the project ones
 * untouched, which is worse than not offering it.
 */
const purge_ = computed(() => purgeable(props.scopes, props.installs));

/**
 * Ask the CLI what the removal would change, rather than describing it from here.
 *
 * `dependents[]` is the reason this is a two-step action: the CLI reports it as a stderr
 * warning, which `--json` sends nowhere a GUI can read, so without the dry run a removal
 * that breaks six entries looks identical to one that breaks none.
 */
async function startPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  purge.value = false;
  try {
    preview.value = await withActivity(`checking what removing ${props.name} changes…`, () =>
      invoke<RemovePreview>("entry_remove_preview", {
        name: props.name,
        catalog: props.copy.catalog,
      }),
    );
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

async function confirm() {
  running.value = true;
  failure.value = "";
  try {
    report.value = await withActivity(`removing ${props.name} from ${props.copy.catalog}…`, () =>
      invoke<RemoveReport>("entry_remove", {
        name: props.name,
        catalog: props.copy.catalog,
        purge: purge.value,
      }),
    );
    preview.value = null;
    emit("removed");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

function cancel() {
  preview.value = null;
  purge.value = false;
}

watch(
  () => [props.name, props.copy.catalog],
  () => {
    cancel();
    report.value = null;
    failure.value = "";
  },
);
</script>

<template>
  <section class="remove">
    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="report" kind="success">
      <p class="remove__done">
        Removed <strong>{{ report.removed.name }}</strong> from
        <strong>{{ report.catalog }}</strong
        >, out of {{ report.removed.section }}.
      </p>
      <p v-if="report.deleted.length" class="remove__done-detail">
        Deleted {{ report.deleted.join(", ") }}.
      </p>
    </StatusBanner>

    <button
      v-if="!preview"
      type="button"
      class="ghost remove__start"
      :disabled="running"
      @click="startPreview()"
    >
      Remove from {{ copy.catalog }}
    </button>

    <Busy v-if="running && !preview" inline label="Checking what would change…" />

    <div v-if="preview" class="remove__confirm fade-in">
      <p class="remove__question">
        Remove {{ preview.removed.name }} from {{ copy.catalog }}?
      </p>

      <p v-if="preview.dependents.length" class="remove__dependents">
        {{ preview.dependents.length }}
        {{ preview.dependents.length === 1 ? "entry in" : "entries in" }} {{ copy.catalog }}
        still {{ preview.dependents.length === 1 ? "requires" : "require" }} this one and will
        no longer resolve: {{ preview.dependents.join(", ") }}.
      </p>

      <pre class="remove__diff">{{ preview.diff }}</pre>

      <template v-if="scopes.length">
        <p class="remove__orphan">
          {{ name }} is installed ({{ scopes.join(", ") }}). Removing the catalog entry leaves
          those files where they are, and there will be no entry left to uninstall them from.
        </p>
        <label v-if="purge_.offered" class="remove__purge">
          <input v-model="purge" type="checkbox" />
          <span>
            Also delete the installed copies. This deletes whatever is at
            <template v-if="purge_.paths.length">{{ purge_.paths.join(", ") }}</template>
            <template v-else>the global install location</template>, including anything you
            put there yourself — the receipt check that normally refuses that does not apply
            here.
          </span>
        </label>
        <p v-else class="remove__orphan">
          The {{ purge_.blockedBy.join(" and ") }} copy lives in its own directory, so it can
          only be deleted from there. Use <strong>Remove installed copies</strong> above first,
          then remove the entry.
        </p>
      </template>

      <p class="remove__note">
        Only {{ copy.catalog }}'s copy of the entry is removed. The file the source points at
        is not touched.
      </p>

      <div class="remove__actions">
        <button type="button" class="ghost" @click="cancel()">Cancel</button>
        <button type="button" :disabled="running" @click="confirm()">
          {{ purge ? "Remove and delete the copies" : "Remove the entry" }}
        </button>
      </div>
      <Busy v-if="running" inline label="Writing the catalog…" />
    </div>
  </section>
</template>

<style scoped>
.remove {
  margin-top: 0.75rem;
}
.remove__confirm {
  margin-top: 0.75rem;
  padding: 0.85rem;
  border-radius: 8px;
  background: rgba(220, 38, 38, 0.08);
  border-left: 3px solid #dc2626;
}
.remove__question {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.45;
  font-weight: 600;
}
.remove__dependents {
  margin: 0.5rem 0 0;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  font-size: 0.8rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
}
.remove__diff {
  margin: 0.6rem 0 0;
  padding: 0.6rem 0.7rem;
  border-radius: 6px;
  max-height: 16rem;
  overflow: auto;
  background: rgba(128, 128, 128, 0.12);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  line-height: 1.5;
  white-space: pre;
}
.remove__orphan {
  margin: 0.6rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.85;
}
.remove__purge {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  margin-top: 0.5rem;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  background: rgba(220, 38, 38, 0.1);
  font-size: 0.78rem;
  line-height: 1.45;
}
.remove__note {
  margin: 0.6rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.75;
}
.remove__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.remove__done {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
.remove__done-detail {
  margin: 0.4rem 0 0;
  font-size: 0.78rem;
  opacity: 0.8;
  overflow-wrap: anywhere;
}
</style>
