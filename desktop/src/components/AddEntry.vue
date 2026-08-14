<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { contributedCatalogs, editableCatalogs, requirableRefs } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type AddReport, type Catalog, type Entry } from "../types";
import Busy from "./Busy.vue";

const props = defineProps<{
  /** The registry, for the destination dropdown. */
  catalogs: Catalog[];
  /** The loaded catalog, for the requires picker. */
  entries: Entry[];
}>();
const emit = defineEmits<{ close: []; added: [] }>();

const TYPES = ["skill", "agent", "prompt"] as const;

const name = ref("");
const type = ref<(typeof TYPES)[number]>("skill");
const description = ref("");
const source = ref("");
const requires = ref<string[]>([]);
const submitting = ref(false);
const failure = ref("");
const report = ref<AddReport | null>(null);

const destinations = computed(() => editableCatalogs(props.catalogs));
const contributed = computed(() => contributedCatalogs(props.catalogs));
const catalogId = ref(destinations.value[0]?.id ?? "");

/** Only this catalog's entries: a ref into another catalog would dangle. */
const available = computed(() => requirableRefs(props.entries, catalogId.value));

// Switching destination invalidates the picked refs, since they belong to the catalog
// that was selected when they were picked.
watch(catalogId, () => {
  requires.value = [];
});

/**
 * What the source has to point at, which differs by type.
 *
 * A skill's source names its `SKILL.md` and the *containing folder* is what installs, so
 * pointing at the folder itself would install that folder's parent. The CLI checks only
 * that the path exists, so this is the one place the difference gets said out loud.
 */
const sourceHint = computed(() => {
  if (type.value === "skill") {
    return "A URL, or a file on this machine. Point at the skill's SKILL.md — the folder holding it is what installs.";
  }
  return `A URL, or a file on this machine. Point at the ${type.value} file itself.`;
});

/**
 * Pick the source file natively.
 *
 * A picked file is absolute and real, which is the shape the CLI requires and the shape a
 * typed path most often gets wrong. The field stays editable for a URL.
 */
async function pickSource() {
  const picked = await open({ directory: false, title: "Which file is this entry?" });
  if (typeof picked === "string") source.value = picked;
}

const filled = computed(
  () => !!name.value.trim() && !!description.value.trim() && !!source.value.trim(),
);
const canSubmit = computed(() => filled.value && !!catalogId.value && !submitting.value);

async function submit() {
  submitting.value = true;
  failure.value = "";
  report.value = null;
  try {
    report.value = await withActivity(`adding ${name.value.trim()}…`, () =>
      invoke<AddReport>("entry_add", {
        request: {
          name: name.value.trim(),
          type: type.value,
          description: description.value.trim(),
          source: source.value.trim(),
          requires: requires.value,
          catalog: catalogId.value,
        },
      }),
    );
    emit("added");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="add-entry">
    <header class="add-entry__head">
      <button type="button" class="ghost" @click="emit('close')">← Catalog</button>
      <h2 class="add-entry__title">Add an entry</h2>
    </header>

    <p v-if="!destinations.length" class="add-entry__empty">
      You have no catalog of your own on this machine, so there is nowhere to add an entry yet.
      A personal catalog is a <code>library.yaml</code> file you own; register one and it appears
      here.
    </p>

    <form v-else class="add-entry__form" @submit.prevent="submit">
      <label class="add-entry__field">
        <span>Name</span>
        <input v-model="name" type="text" placeholder="bug-investigator" autofocus />
      </label>

      <label class="add-entry__field">
        <span>Type</span>
        <select v-model="type">
          <option v-for="option in TYPES" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>

      <label class="add-entry__field">
        <span>Description</span>
        <input v-model="description" type="text" placeholder="What it does, in one line." />
      </label>

      <label class="add-entry__field">
        <span>Source</span>
        <span class="add-entry__row">
          <input
            v-model="source"
            type="text"
            placeholder="https://github.com/your-team/repo/blob/main/bug-investigator/SKILL.md"
          />
          <button type="button" class="ghost" @click="pickSource">Choose file…</button>
        </span>
        <span class="add-entry__hint">{{ sourceHint }}</span>
      </label>

      <label class="add-entry__field">
        <span>Destination catalog</span>
        <select v-model="catalogId">
          <option v-for="option in destinations" :key="option.id" :value="option.id">
            {{ option.id }} · {{ option.write_mode }}
          </option>
        </select>
      </label>

      <fieldset v-if="available.length" class="add-entry__requires">
        <legend>Requires</legend>
        <label v-for="ref in available" :key="ref" class="add-entry__check">
          <input v-model="requires" type="checkbox" :value="ref" />
          <span>{{ ref }}</span>
        </label>
      </fieldset>

      <button type="submit" :disabled="!canSubmit">Add to {{ catalogId }}</button>
      <Busy v-if="submitting" inline label="Writing the catalog…" />
    </form>

    <p v-if="contributed.length" class="add-entry__deferred">
      <template v-for="(shared, index) in contributed" :key="shared.id">
        <span v-if="index">, </span><code>{{ shared.id }}</code>
      </template>
      {{ contributed.length > 1 ? "are shared catalogs" : "is a shared catalog" }}, so entries
      there are contributed through the repository itself rather than from here — that way the
      change goes through the same review as any other.
      <span v-for="shared in contributed" :key="shared.id" class="add-entry__where">
        {{ shared.location }}
      </span>
    </p>

    <pre v-if="failure" class="add-entry__failure">{{ failure }}</pre>

    <div v-else-if="report" class="add-entry__result fade-in">
      <p>
        Added <strong>{{ report.added.name }}</strong> to <code>{{ report.catalog }}</code> under
        <code>{{ report.added.section }}</code>.
      </p>
      <p v-if="report.path" class="add-entry__where">{{ report.path }}</p>
      <p v-if="report.pushed" class="add-entry__where">
        Committed and pushed to {{ report.branch }}.
      </p>
      <p v-else-if="report.committed" class="add-entry__where">
        Committed to {{ report.branch }}; the push did not happen.
      </p>
    </div>
  </section>
</template>

<style scoped>
.add-entry {
  padding: 1.5rem 0 3rem;
}
.add-entry__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}
.add-entry__title {
  margin: 0;
  font-size: 1.15rem;
}
.add-entry__empty {
  opacity: 0.7;
  line-height: 1.5;
}
.add-entry__form {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  max-width: 34rem;
}
.add-entry__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.add-entry__field input,
.add-entry__field select {
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
}
.add-entry__field input {
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.add-entry__row {
  display: flex;
  gap: 0.4rem;
}
.add-entry__row input {
  flex: 1;
  min-width: 0;
}
.add-entry__hint {
  font-size: 0.72rem;
  opacity: 0.6;
  line-height: 1.4;
}
.add-entry__deferred {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  max-width: 34rem;
  margin: 1.5rem 0 0;
  padding: 0.75rem 0.9rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.1);
  font-size: 0.8rem;
  line-height: 1.5;
  opacity: 0.85;
}
.add-entry__deferred code {
  font-size: 0.85em;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.2);
}
.add-entry__check {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.8rem;
  line-height: 1.4;
  opacity: 0.85;
}
.add-entry__requires {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-height: 12rem;
  overflow-y: auto;
  margin: 0;
  padding: 0.6rem 0.75rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 8px;
}
.add-entry__requires legend {
  font-size: 0.78rem;
  opacity: 0.7;
  padding: 0 0.3rem;
}
.add-entry__check code,
.add-entry__result code {
  font-size: 0.85em;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.15);
}
.add-entry__failure {
  margin: 1.25rem 0 0;
  padding: 1rem;
  border-radius: 8px;
  max-width: 34rem;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.add-entry__result {
  margin-top: 1.25rem;
  max-width: 34rem;
  line-height: 1.5;
}
.add-entry__result p {
  margin: 0 0 0.35rem;
}
.add-entry__where {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
  overflow-wrap: anywhere;
}
</style>
