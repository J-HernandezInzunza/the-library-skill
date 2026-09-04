<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { purgeable } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type Entry,
  type RemovePreview,
  type RemoveReport,
} from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The copy being removed. Only this one catalog's entry goes away. */
  entry: Entry;
}>();
const emit = defineEmits<{ removed: []; close: [] }>();

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
 *
 * `entry.receipt` is the only one `list` carries, and that is enough here: the checkbox is
 * offered only when the entry's sole scope is `global`, which is one standard destination.
 * A `--dir` install never appears in `scopes` at all, so it cannot be under-reported by a
 * checkbox that has already refused to appear.
 */
const purge_ = computed(() =>
  purgeable(props.entry.scopes, props.entry.receipt ? [props.entry.receipt] : []),
);

/**
 * Ask the CLI what the removal would change, rather than describing it from here.
 *
 * `dependents[]` is the reason this is a two-step action: the CLI reports it as a stderr
 * warning, which `--json` sends nowhere a GUI can read, so without the dry run a removal
 * that breaks six entries looks identical to one that breaks none.
 *
 * Run on mount rather than behind a button: this panel only exists because the user just
 * clicked Remove, so a second "Remove" button would be asking the same question twice
 * before asking the one that matters.
 */
async function startPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  purge.value = false;
  try {
    preview.value = await withActivity(`checking what removing ${props.entry.name} changes…`, () =>
      invoke<RemovePreview>("entry_remove_preview", {
        name: props.entry.name,
        catalog: props.entry.catalog,
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
    report.value = await withActivity(
      `removing ${props.entry.name} from ${props.entry.catalog}…`,
      () =>
        invoke<RemoveReport>("entry_remove", {
          name: props.entry.name,
          catalog: props.entry.catalog,
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

onMounted(startPreview);
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

    <Busy v-if="running && !preview" inline label="Checking what would change…" />

    <div v-if="preview" class="remove__confirm fade-in">
      <p class="remove__question">
        Remove {{ preview.removed.name }} from {{ entry.catalog }}?
      </p>

      <p v-if="preview.dependents.length" class="remove__dependents">
        {{ preview.dependents.length }}
        {{ preview.dependents.length === 1 ? "entry in" : "entries in" }} {{ entry.catalog }}
        still {{ preview.dependents.length === 1 ? "requires" : "require" }} this one and will
        no longer resolve: {{ preview.dependents.join(", ") }}.
      </p>

      <pre class="remove__diff">{{ preview.diff }}</pre>

      <template v-if="entry.scopes.length">
        <p class="remove__orphan">
          {{ entry.name }} is installed ({{ entry.scopes.join(", ") }}). Removing the catalog
          entry leaves those files where they are, and there will be no entry left to uninstall
          them from.
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
          only be deleted from there. Open {{ entry.name }} from the catalog and use
          <strong>Remove installed copies</strong> first, then remove the entry.
        </p>
      </template>

      <p class="remove__note">
        Only {{ entry.catalog }}'s copy of the entry is removed. The file the source points at
        is not touched.
      </p>

      <div class="remove__actions">
        <button type="button" class="ghost" @click="emit('close')">Cancel</button>
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
