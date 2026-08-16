<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeDestState, installPlan } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type UninstallReport,
  type UsePreview,
  type UseReport,
} from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The ticked names, in list order. */
  names: string[];
  /** The catalog they were picked from, for the confirmation to name. */
  catalogId: string;
}>();
const emit = defineEmits<{ installed: []; uninstalled: [] }>();

const preview = ref<UsePreview | null>(null);
const report = ref<UseReport | null>(null);
const acknowledged = ref(false);
const running = ref(false);
const failure = ref("");

/**
 * The uninstall side of the panel.
 *
 * `confirming` gates the delete behind a naming confirmation, the same shape the
 * single-copy control uses. A bulk uninstall targets the **global** scope, mirroring
 * bulk install: the selection lives in a catalog tab and names entries, not per-copy
 * scopes, so there is one honest scope to act on.
 */
const confirming = ref(false);
const uninstallReport = ref<UninstallReport | null>(null);

/**
 * Entries the tool would not delete because it has no receipt for them.
 *
 * Surfaced by name so each can be removed from its own page, where the refusal gets its
 * own confirmation. There is deliberately no blanket "delete anyway" over the batch: a
 * single force over a whole selection is exactly the escalation the refusal exists to
 * prevent (T3.5).
 */
const refused = computed(() =>
  (uninstallReport.value?.results ?? []).filter((r) => r.refused.length),
);
/** Entries whose copies were actually removed. */
const removed = computed(() =>
  (uninstallReport.value?.results ?? []).filter((r) => r.deleted.length),
);

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

/** Switch the panel to its uninstall confirmation, clearing any install plan. */
function startUninstall() {
  preview.value = null;
  report.value = null;
  acknowledged.value = false;
  uninstallReport.value = null;
  failure.value = "";
  confirming.value = true;
}

async function runPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  acknowledged.value = false;
  confirming.value = false;
  uninstallReport.value = null;
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

async function uninstall() {
  running.value = true;
  failure.value = "";
  try {
    uninstallReport.value = await withActivity(
      `removing ${props.names.length} ${props.names.length === 1 ? "entry" : "entries"}…`,
      () =>
        // No --force: the tool deletes the copies it has receipts for and refuses the
        // rest, which are then handled one at a time.
        invoke<UninstallReport>("entry_uninstall", {
          names: props.names,
          scope: "global",
          force: false,
        }),
    );
    confirming.value = false;
    emit("uninstalled");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

// A changed selection describes a different action, so a plan or confirmation built from
// the previous one is not stale so much as about something else.
watch(() => props.names, () => {
  preview.value = null;
  acknowledged.value = false;
  confirming.value = false;
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
    <StatusBanner v-else-if="uninstallReport" :kind="refused.length ? 'warning' : 'success'">
      <p class="bulk__done">
        <template v-if="removed.length">
          Removed {{ removed.length }}
          {{ removed.length === 1 ? "entry" : "entries" }} from this machine.
        </template>
        <template v-else-if="!refused.length">
          None of the selected entries were installed — nothing to remove.
        </template>
        <template v-if="refused.length">
          {{ refused.length }}
          {{ refused.length === 1 ? "entry was" : "entries were" }} left in place because
          the tool has no record of installing
          {{ refused.length === 1 ? "it" : "them" }}:
          {{ refused.map((r) => r.name).join(", ") }}. Open
          {{ refused.length === 1 ? "it" : "each" }} to remove
          {{ refused.length === 1 ? "it" : "them" }} individually.
        </template>
      </p>
    </StatusBanner>

    <div class="bulk__bar">
      <span class="bulk__count">
        <template v-if="names.length">
          {{ names.length }} selected from {{ catalogId }}
        </template>
        <template v-else>
          <!-- The space says what the mode is for. It previously pointed at a button, and
               pointed the wrong way. -->
          Act on several at once: install a selection as one plan with shared dependencies
          fetched once, or remove them together. Tick the entries you want.
        </template>
      </span>
      <button
        v-if="names.length"
        type="button"
        class="ghost danger"
        :disabled="running"
        @click="startUninstall()"
      >
        Uninstall
      </button>
      <button v-if="names.length" type="button" :disabled="running" @click="runPreview()">
        {{ preview ? "Re-check" : "Preview install" }}
      </button>
    </div>

    <div v-if="confirming" class="bulk__confirm fade-in">
      <p class="bulk__confirm-q">
        Remove the global copies of {{ names.length }} selected
        {{ names.length === 1 ? "entry" : "entries" }}?
      </p>
      <p class="bulk__confirm-note">
        Entries that are not installed are skipped, and a copy the tool has no receipt for
        is refused rather than force-deleted. The catalog entries are untouched —
        installing again brings the files back.
      </p>
      <div class="bulk__confirm-actions">
        <button type="button" class="ghost" :disabled="running" @click="confirming = false">
          Cancel
        </button>
        <button type="button" class="danger" :disabled="running" @click="uninstall()">
          Remove {{ names.length }}
        </button>
      </div>
      <Busy v-if="running" inline label="Removing files…" />
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
.bulk__confirm {
  margin-top: 0.75rem;
  padding: 0.75rem;
  border-radius: 8px;
  background: rgba(220, 38, 38, 0.08);
  border-left: 3px solid #dc2626;
}
.bulk__confirm-q {
  margin: 0;
  font-size: 0.85rem;
  font-weight: 600;
  line-height: 1.45;
}
.bulk__confirm-note {
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.8;
}
.bulk__confirm-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
</style>
