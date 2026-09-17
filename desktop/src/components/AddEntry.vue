<script setup lang="ts">
import { computed, ref, useTemplateRef } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { addConsequences, requirableRefs } from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type AddReport,
  type Catalog,
  type Entry,
  type InstallDirHit,
  type SourceSuggestion,
} from "../types";
import { RAW_TEXT } from "../rawText";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import PageHeader from "./PageHeader.vue";

const props = defineProps<{
  /**
   * The catalog being added to.
   *
   * A prop rather than a dropdown: this form is reached from inside a catalog, so the
   * destination is already answered by where the user is. The dropdown existed only
   * because the form used to open from the topbar, with no context at all.
   */
  catalogId: string;
  /** The registry, for the precedence comparison behind the override warnings. */
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
/**
 * The install directory the chosen source sits in, when it does.
 *
 * Held apart from `suggestion` even though one lookup answers both: the URL offer is
 * dismissible and this is not. "Keep the path" declines a suggestion, it does not make
 * a source that erases itself acceptable.
 */
const installDir = ref<InstallDirHit | null>(null);
const failure = ref("");
const report = ref<AddReport | null>(null);

/**
 * The entry this form is about, which cannot be one of its own requirements.
 *
 * While typing, that is the name in the field. After a successful add the field is cleared
 * but the banner still names what was added, so the form still reads as being about that
 * entry — and the reload has just put it in the picker. Typing the next name moves the
 * exclusion onto it and frees the one just added to be depended on.
 */
const selfRef = computed(() => {
  const typed = name.value.trim();
  if (typed) return `${type.value}:${typed}`;
  return report.value ? `${report.value.added.type}:${report.value.added.name}` : "";
});

/** Only this catalog's entries: a ref into another catalog would dangle. */
const available = computed(() => requirableRefs(props.entries, props.catalogId, selfRef.value));

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
  installDir.value = null;
  try {
    const answer = await withActivity("looking up the source URL…", () =>
      invoke<SourceSuggestion>("source_suggestion", { path }),
    );
    suggestion.value = answer;
    installDir.value = answer.install_dir;
  } catch {
    // A suggestion is an optional convenience, so a failure here must not look like a
    // failure of the form. The typed path stays exactly as the user left it. The
    // install-dir warning rides the same call and is lost with it, which is survivable
    // only because the same refusal exists behind the submit.
    suggestion.value = null;
    installDir.value = null;
  }
}

/**
 * Re-run the lookup for a source the user typed instead of picking.
 *
 * `pickSource` covers the file picker, and typing the path by hand is exactly how
 * content already sitting in `.claude` gets named. Only paths are sent: a URL has no
 * local answer, and asking anyway would put a failing command in the log for every
 * pasted link. The leading-`/`-or-`~` test is the same one that sorts paths from URLs
 * on the other side, not a second opinion about what the source is.
 */
async function recheckTypedSource() {
  const typed = source.value.trim();
  const isPath = typed.startsWith("/") || typed.startsWith("~");
  if (!isPath) {
    suggestion.value = null;
    installDir.value = null;
    return;
  }
  await suggestFor(typed);
}

function applySuggestion() {
  if (suggestion.value?.suggestion) source.value = suggestion.value.suggestion;
  suggestion.value = null;
  // The source is a URL now, which installs through a clone and cannot land on itself.
  installDir.value = null;
}

/**
 * Whether this source would be destroyed by installing the entry that names it.
 *
 * A global install dir is fixed, so every install of the entry overwrites the source.
 * A project dir only collides when the entry is installed into that same project, which
 * is a caution rather than a refusal — the verdict is the backend's, read off `scope`.
 */
const sourceBlocked = computed(() => installDir.value?.scope === "global");

/**
 * What this name would do to the copies that already exist (R4.3).
 *
 * Shown while typing rather than after submitting: `add` reports both of these on stderr,
 * which is invisible under `--json`, so without this the collision surfaces as a failed
 * command and the override does not surface at all.
 */
const consequences = computed(() =>
  addConsequences(props.entries, props.catalogs, name.value, props.catalogId),
);

const filled = computed(
  () => !!name.value.trim() && !!description.value.trim() && !!source.value.trim(),
);
const canSubmit = computed(
  () => filled.value && !consequences.value.blocked && !sourceBlocked.value && !submitting.value,
);

/**
 * Clear what described the entry just added, keep what describes the next one.
 *
 * Type survives: registering several entries in a row is the normal use and they are
 * almost always the same kind. The destination is not resettable at all any more — it is
 * the catalog you are standing in.
 */
function resetForm() {
  name.value = "";
  description.value = "";
  source.value = "";
  requires.value = [];
  suggestion.value = null;
  installDir.value = null;
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
          catalog: props.catalogId,
        },
      }),
    );
    resetForm();
    emit("added");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    scrollToBanner();
    submitting.value = false;
  }
}

/** This view's scrolling body — the only thing in the view that scrolls (D22). */
/** The header, for the one thing this view needs from it: the body element it scrolls. */
const header = useTemplateRef<InstanceType<typeof PageHeader>>("header");

/**
 * Bring the confirmation into view.
 *
 * The banner sits above a form tall enough to scroll, so on a long requires list the
 * outcome lands off-screen and the add reads as though nothing happened. Both outcomes,
 * because both render in the same place — the earlier version scrolled only on success,
 * which was correct only while the error still sat beside the submit button.
 */
function scrollToBanner() {
  // This view's body, not the window: the document does not scroll (D22), so `window.scrollTo`
  // moved nothing and the banner stayed off-screen.
  const box = header.value?.body;
  if (!box) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  box.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
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
  <section class="view">
    <PageHeader ref="header" :title="`Add an entry to ${catalogId}`" :back="catalogId" @back="emit('close')">

      <StatusBanner v-if="failure" kind="error" :detail="failure" />

      <StatusBanner v-else-if="report" kind="success">
        <p class="add-entry__added-line">
          Added <strong>{{ report.added.name }}</strong> to
          <strong>{{ report.catalog }}</strong>, under {{ report.added.section }}.
        </p>
        <p v-if="report.path" class="add-entry__added-where">
          <button type="button" class="ghost btn-sm" @click="reveal(report.path)">Show in Finder</button>
          <code>{{ report.path }}</code>
        </p>
        <p v-if="report.pushed" class="add-entry__added-where">
          Committed and pushed to {{ report.branch }}.
        </p>
        <p v-else-if="report.committed" class="add-entry__added-where">
          Committed to {{ report.branch }}; the push did not happen.
        </p>
      </StatusBanner>

      <form class="add-entry__form" @submit.prevent="submit">
        <label class="add-entry__field">
          <span>Name</span>
          <input
            v-model="name"
            type="text"
            placeholder="bug-investigator"
            autofocus
            v-bind="RAW_TEXT"
          />

          <span v-if="consequences.blocked" class="add-entry__conflict">
            <strong>{{ catalogId }}</strong> already has an entry called
            <code>{{ name.trim() }}</code>. Adding it again is refused — change the existing
            entry instead, or pick a different name.
          </span>
          <span v-else-if="consequences.overrides.length" class="add-entry__consequence">
            <code>{{ name.trim() }}</code> also exists in
            <strong>{{ consequences.overrides.join(", ") }}</strong>. Your copy in
            {{ catalogId }} takes precedence, so it becomes the one that installs.
          </span>
          <span v-else-if="consequences.overriddenBy.length" class="add-entry__consequence">
            <code>{{ name.trim() }}</code> also exists in
            <strong>{{ consequences.overriddenBy.join(", ") }}</strong>, which takes precedence.
            Your copy in {{ catalogId }} would be added but never installed.
          </span>
        </label>

        <label class="add-entry__field">
          <span>Type</span>
          <select v-model="type">
            <option v-for="option in TYPES" :key="option" :value="option">{{ option }}</option>
          </select>
        </label>

        <label class="add-entry__field">
          <span>Description</span>
          <input
            v-model="description"
            type="text"
            placeholder="What it does, in one line, this is what will show in the catalog entry"
            v-bind="RAW_TEXT"
          />
        </label>

        <label class="add-entry__field">
          <span>Source</span>
          <span class="add-entry__row">
            <input
              v-model="source"
              type="text"
              placeholder="https://github.com/your-team/repo/blob/main/bug-investigator/SKILL.md"
              v-bind="RAW_TEXT"
              @change="recheckTypedSource"
            />
            <button type="button" class="ghost" @click="pickSource">Choose file…</button>
          </span>
          <span class="add-entry__hint">{{ sourceHint }}</span>

          <span v-if="sourceBlocked && installDir" class="add-entry__conflict">
            <code>{{ installDir.path }}</code> is where the app installs
            {{ installDir.section }}. An entry sourced from there is overwritten by its own
            install, which erases the copy you just picked. Keep the content somewhere you
            version control and point the source at it there. Installing is what puts a copy
            under <code>.claude</code>, not the other way round.
          </span>
          <span v-else-if="installDir" class="add-entry__consequence">
            <code>{{ installDir.path }}</code> is where the app installs
            {{ installDir.section }} for that project. Installing this entry back into that
            same project would overwrite its own source. Installing it anywhere else is fine.
          </span>

          <span v-if="suggestion?.suggestion" class="add-entry__suggestion">
            <span>This file is in a git repo. Teammates would need this URL instead:</span>
            <code>{{ suggestion.suggestion }}</code>
            <span class="add-entry__suggestion-actions">
              <button type="button" class="btn-sm" @click="applySuggestion">Use this URL</button>
              <button type="button" class="ghost btn-sm" @click="suggestion = null">Keep the path</button>
            </span>
          </span>
          <span v-else-if="suggestion" class="add-entry__hint">
            No shareable URL for this file: {{ suggestion.reason }}. That is fine for a catalog
            only you use.
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
    </PageHeader>
  </section>
</template>

<style scoped>
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
  border: 1px solid var(--border-control);
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
.add-entry__conflict,
.add-entry__consequence {
  margin-top: 0.35rem;
  padding: 0.5rem 0.65rem;
  border-radius: 8px;
  font-size: 0.75rem;
  line-height: 1.45;
}
.add-entry__conflict {
  color: var(--status-danger-ink);
  background: var(--status-danger-tint);
}
.add-entry__consequence {
  background: var(--status-override-tint);
}
.add-entry__conflict code,
.add-entry__consequence code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.95em;
}
.add-entry__suggestion {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 0.35rem;
  padding: 0.6rem 0.7rem;
  border-radius: 8px;
  background: var(--accent-tint);
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
  gap: 0.5rem;
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
  border: 1px solid var(--border-control);
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
  background: var(--surface-hover);
}
.add-entry__where {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
  overflow-wrap: anywhere;
}
</style>
