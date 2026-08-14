<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { entryEdits, requirableRefs } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type Entry, type UpdateReport } from "../types";
import { RAW_TEXT } from "../rawText";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The copy being edited: one catalog's record, which is where the fields start from. */
  entry: Entry;
  /** The loaded catalog, for the requires picker. */
  entries: Entry[];
}>();
const emit = defineEmits<{ saved: []; close: [] }>();

const description = ref("");
const source = ref("");
const requires = ref<string[]>([]);
const saving = ref(false);
const failure = ref("");
const report = ref<UpdateReport | null>(null);

/**
 * Refill from the copy whenever it changes.
 *
 * The panel is mounted by the row that owns it, so it starts from the right entry — but a
 * reload after a save hands back a new object for the same entry, and the form has to
 * follow it rather than keep showing what was typed against the previous read.
 */
watch(
  () => props.entry,
  () => {
    description.value = props.entry.description;
    source.value = props.entry.source;
    requires.value = [...props.entry.requires];
    failure.value = "";
    report.value = null;
  },
  { immediate: true },
);

/** Only this catalog's entries: a ref into another catalog dangles (D9). */
const available = computed(() => requirableRefs(props.entries, props.entry.catalog));

/**
 * What would be sent, or null when nothing was touched.
 *
 * Computed rather than checked on submit so the button can say why it is disabled: a
 * control that refuses without explaining reads as a broken one.
 */
const edits = computed(() =>
  entryEdits(props.entry, {
    description: description.value,
    source: source.value,
    requires: requires.value,
  }),
);

const emptyField = computed(() => !description.value.trim() || !source.value.trim());
const blockedBecause = computed(() => {
  if (emptyField.value) return "A description and a source are required.";
  if (!edits.value) return "Nothing has changed yet.";
  return "";
});

async function save() {
  const changed = edits.value;
  if (!changed) return;

  saving.value = true;
  failure.value = "";
  report.value = null;
  try {
    report.value = await withActivity(`updating ${props.entry.name}…`, () =>
      invoke<UpdateReport>("entry_update", {
        request: { name: props.entry.name, catalog: props.entry.catalog, ...changed },
      }),
    );
    emit("saved");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="editor">
    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="report" kind="success">
      <p v-if="report.changed" class="editor__saved">
        Updated <strong>{{ report.name }}</strong> in <strong>{{ report.catalog }}</strong
        >.
      </p>
      <p v-else class="editor__saved">
        No changes — {{ report.name }} already matched what you asked for.
      </p>
    </StatusBanner>

    <p class="editor__note">
      The name and type cannot be changed here: both decide where the entry lives in the
      file. Remove it and add it again to change either.
    </p>

    <form class="editor__form" @submit.prevent="save">
      <label class="editor__field">
        <span>Description</span>
        <input v-model="description" type="text" v-bind="RAW_TEXT" />
      </label>

      <label class="editor__field">
        <span>Source</span>
        <input v-model="source" type="text" v-bind="RAW_TEXT" />
      </label>

      <fieldset v-if="available.length" class="editor__requires">
        <legend>Requires</legend>
        <div class="editor__requires-list">
          <label v-for="ref in available" :key="ref" class="editor__check">
            <input v-model="requires" type="checkbox" :value="ref" />
            <span>{{ ref }}</span>
          </label>
        </div>
      </fieldset>

      <p v-if="blockedBecause" class="editor__blocked">{{ blockedBecause }}</p>
      <div class="editor__actions">
        <button type="submit" :disabled="!edits || emptyField || saving">Save changes</button>
        <button type="button" class="ghost" @click="emit('close')">Done</button>
      </div>
      <Busy v-if="saving" inline label="Writing the catalog…" />
    </form>
  </section>
</template>

<style scoped>
.editor__note {
  margin: 0 0 0.9rem;
  font-size: 0.75rem;
  line-height: 1.45;
  opacity: 0.65;
}
.editor__form {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}
.editor__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.editor__field input {
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
/* Not a flex container: WKWebView drops a <legend> whose fieldset is `display: flex`,
   so the group label silently disappears. The list inside carries the layout. */
.editor__requires {
  margin: 0;
  padding: 0.5rem 0.75rem 0.6rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 8px;
}
.editor__requires legend {
  padding: 0 0.3rem;
  font-size: 0.78rem;
  opacity: 0.7;
}
.editor__requires-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-height: 12rem;
  overflow-y: auto;
}
.editor__check {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.8rem;
  opacity: 0.85;
}
.editor__blocked {
  margin: 0;
  font-size: 0.75rem;
  opacity: 0.6;
}
.editor__actions {
  display: flex;
  gap: 0.5rem;
}
.editor__saved {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
</style>
