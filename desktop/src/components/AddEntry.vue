<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { contributedCatalogs, editableCatalogs, requirableRefs } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type AddReport,
  type Catalog,
  type Entry,
  type SourceSuggestion,
} from "../types";
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
const suggestion = ref<SourceSuggestion | null>(null);
const failure = ref("");
const report = ref<AddReport | null>(null);

const destinations = computed(() => editableCatalogs(props.catalogs));
const contributed = computed(() => contributedCatalogs(props.catalogs));
const contributedNames = computed(() => contributed.value.map((c) => c.id).join(", "));
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
 * Pick the source file natively, then ask what URL it would have.
 *
 * A picked file is absolute and real, which is the shape the CLI requires and the shape a
 * typed path most often gets wrong. The field stays editable for a URL.
 */
async function pickSource() {
  const picked = await open({ directory: false, title: "Which file is this entry?" });
  if (typeof picked !== "string") return;
  source.value = picked;
  await suggestFor(picked);
}

/**
 * The URL a teammate could resolve for a local path, derived by the CLI.
 *
 * Offered rather than applied: the URL comes from the checked-out branch and the `origin`
 * remote, either of which can be wrong for the intent (a feature branch, a fork). A miss
 * is a successful call with a reason, so it is shown rather than swallowed — "not in a git
 * repo" and "origin is not GitHub or Bitbucket" have different fixes.
 */
async function suggestFor(path: string) {
  suggestion.value = null;
  try {
    suggestion.value = await withActivity("looking up the source URL…", () =>
      invoke<SourceSuggestion>("source_suggestion", { path }),
    );
  } catch {
    // A suggestion is an optional convenience, so a failure here must not look like a
    // failure of the form. The typed path stays exactly as the user left it.
    suggestion.value = null;
  }
}

function applySuggestion() {
  if (suggestion.value?.suggestion) source.value = suggestion.value.suggestion;
  suggestion.value = null;
}

const filled = computed(
  () => !!name.value.trim() && !!description.value.trim() && !!source.value.trim(),
);
const canSubmit = computed(() => filled.value && !!catalogId.value && !submitting.value);

/**
 * Clear what described the entry just added, keep what describes the next one.
 *
 * Type and destination survive: registering several entries in a row is the normal use,
 * and they are almost always the same kind going to the same catalog. Re-picking both
 * every time would be the annoying half of a reset.
 */
function resetForm() {
  name.value = "";
  description.value = "";
  source.value = "";
  requires.value = [];
  suggestion.value = null;
}

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
    resetForm();
    emit("added");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    submitting.value = false;
  }
}

/**
 * Show the catalog file in Finder.
 *
 * Revealing rather than opening: a `.yaml` opens in whatever the OS has registered for
 * it, which could be anything, while "here is the file that changed" is the same answer
 * on every machine. A failure is reported because the user asked for this one explicitly.
 */
async function reveal(path: string) {
  try {
    await revealItemInDir(path);
  } catch (e) {
    failure.value = `Could not show ${path}: ${e}`;
  }
}
</script>

<template>
  <section class="add-entry">
    <header class="add-entry__head">
      <button type="button" class="ghost" @click="emit('close')">← Catalog</button>
      <h2 class="add-entry__title">Add an entry</h2>
    </header>

    <div v-if="report" class="add-entry__added fade-in">
      <p class="add-entry__added-line">
        Added <strong>{{ report.added.name }}</strong> to
        <strong>{{ report.catalog }}</strong>, under {{ report.added.section }}.
      </p>
      <p v-if="report.path" class="add-entry__added-where">
        <button type="button" class="ghost" @click="reveal(report.path)">Show in Finder</button>
        <code>{{ report.path }}</code>
      </p>
      <p v-if="report.pushed" class="add-entry__added-where">
        Committed and pushed to {{ report.branch }}.
      </p>
      <p v-else-if="report.committed" class="add-entry__added-where">
        Committed to {{ report.branch }}; the push did not happen.
      </p>
    </div>

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
        <input v-model="description" type="text" placeholder="What it does, in one line, this is what will show in the catalog entry" />
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

        <span v-if="suggestion?.suggestion" class="add-entry__suggestion">
          <span>This file is in a git repo. Teammates would need this URL instead:</span>
          <code>{{ suggestion.suggestion }}</code>
          <span class="add-entry__suggestion-actions">
            <button type="button" @click="applySuggestion">Use this URL</button>
            <button type="button" class="ghost" @click="suggestion = null">Keep the path</button>
          </span>
        </span>
        <span v-else-if="suggestion" class="add-entry__hint">
          No shareable URL for this file: {{ suggestion.reason }}. That is fine for a catalog
          only you use.
        </span>
      </label>

      <label class="add-entry__field">
        <span>Destination catalog</span>
        <select v-model="catalogId">
          <option v-for="option in destinations" :key="option.id" :value="option.id">
            {{ option.id }} · {{ option.write_mode }}
          </option>
        </select>
        <span v-if="contributed.length" class="add-entry__hint">
          {{ contributedNames }}
          {{ contributed.length > 1 ? "are shared catalogs" : "is a shared catalog" }}, so entries
          there are contributed through the repository itself rather than from here — that way the
          change goes through the same review as any other.
        </span>
        <span v-for="shared in contributed" :key="shared.id" class="add-entry__where">
          {{ shared.location }}
        </span>
      </label>

      <fieldset v-if="available.length" class="add-entry__requires">
        <legend>Requires</legend>
        <div class="add-entry__requires-list">
          <label v-for="ref in available" :key="ref" class="add-entry__check">
            <input v-model="requires" type="checkbox" :value="ref" />
            <span>{{ ref }}</span>
          </label>
        </div>
      </fieldset>

      <button type="submit" :disabled="!canSubmit">Add to {{ catalogId }}</button>
      <Busy v-if="submitting" inline label="Writing the catalog…" />
    </form>

    <pre v-if="failure" class="add-entry__failure">{{ failure }}</pre>
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
.add-entry__suggestion {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 0.35rem;
  padding: 0.6rem 0.7rem;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.1);
  font-size: 0.75rem;
  line-height: 1.4;
  opacity: 1;
}
.add-entry__suggestion code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  overflow-wrap: anywhere;
  user-select: all;
}
.add-entry__suggestion-actions {
  display: flex;
  gap: 0.4rem;
}
.add-entry__suggestion-actions button {
  padding: 0.3rem 0.6rem;
  font-size: 0.75rem;
}
.add-entry__added {
  max-width: 34rem;
  margin: 0 0 1.5rem;
  padding: 0.9rem 1rem;
  border: 1px solid rgba(34, 197, 94, 0.35);
  border-radius: 8px;
  background: rgba(34, 197, 94, 0.1);
}
.add-entry__added-line {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
.add-entry__added-where {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.6rem 0 0;
  font-size: 0.75rem;
  opacity: 0.75;
}
.add-entry__added-where code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
}
.add-entry__added-where button {
  flex: none;
  padding: 0.25rem 0.55rem;
  font-size: 0.72rem;
}
.add-entry__check {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.8rem;
  line-height: 1.4;
  opacity: 0.85;
}
/* Deliberately not a flex container: WKWebView drops a <legend> entirely when its
   fieldset is `display: flex`, so the group label silently disappears. The list inside
   carries the layout, which also keeps the label still while the list scrolls. */
.add-entry__requires {
  margin: 0;
  padding: 0.5rem 0.75rem 0.6rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 8px;
}
.add-entry__requires legend {
  padding: 0 0.3rem;
  font-size: 0.78rem;
  opacity: 0.7;
}
.add-entry__requires-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-height: 12rem;
  overflow-y: auto;
}
.add-entry__check code {
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
.add-entry__where {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
  overflow-wrap: anywhere;
}
</style>
