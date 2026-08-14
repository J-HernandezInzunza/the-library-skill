<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import { describePush } from "../catalog";
import { withActivity } from "../commandActivity";
import { recentProjects, rememberProject } from "../recentProjects";
import { describeAppError, type PushPreview, type PushReport } from "../types";
import { RAW_TEXT } from "../rawText";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** Scopes with a copy on disk, from `entry.scopes`. Nothing to push without one. */
  scopes: string[];
}>();

const scope = ref("");
const projectDir = ref("");
const message = ref("");
const preview = ref<PushPreview | null>(null);
const report = ref<PushReport | null>(null);
const running = ref(false);
const failure = ref("");
const recents = ref(recentProjects());

/**
 * `--from project` resolves against `LIBRARY_CWD`, so a project push needs the directory
 * before it can even be previewed. The same rule as a project install (T3.3), and the same
 * picker — nothing is preselected, so a stale recent costs a click rather than pushing
 * from the wrong repository.
 */
const needsDir = computed(() => scope.value === "project" && !projectDir.value);
const project = computed(() => (scope.value === "project" ? projectDir.value : undefined));

const outcome = computed(() => (report.value ? describePush(report.value) : null));

async function pickDir() {
  const picked = await open({ directory: true, title: "Which project holds the copy to push?" });
  if (typeof picked !== "string") return;
  useDir(picked);
}

function useDir(dir: string) {
  projectDir.value = dir;
  recents.value = rememberProject(dir);
  preview.value = null;
}

async function runPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  try {
    preview.value = await withActivity(`checking what pushing ${props.name} would send…`, () =>
      invoke<PushPreview>("entry_push_preview", {
        name: props.name,
        scope: scope.value,
        project: project.value,
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
    report.value = await withActivity(`pushing ${props.name}…`, () =>
      invoke<PushReport>("entry_push", {
        name: props.name,
        scope: scope.value,
        project: project.value,
        message: message.value.trim() || undefined,
      }),
    );
    preview.value = null;
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

/** The PR or compare URL, opened in the real browser rather than in the app's WebView. */
async function follow(url: string) {
  try {
    await openUrl(url);
  } catch (e) {
    failure.value = `Could not open ${url}: ${e}`;
  }
}

// A changed scope or directory describes a different local copy, so the plan built from
// the previous one is not merely stale — it is about something else.
watch([scope, projectDir], () => {
  preview.value = null;
});

watch(
  () => props.name,
  () => {
    scope.value = "";
    projectDir.value = "";
    message.value = "";
    preview.value = null;
    report.value = null;
    failure.value = "";
  },
);
</script>

<template>
  <section v-if="scopes.length" class="push">
    <h3 class="push__heading">Send local edits back to the source</h3>

    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="outcome" kind="success">
      <p class="push__done">{{ outcome.headline }}</p>
      <p v-if="outcome.detail" class="push__done-detail">{{ outcome.detail }}</p>
      <p v-if="outcome.link" class="push__done-detail">
        <button type="button" @click="follow(outcome.link.url)">{{ outcome.link.label }}</button>
      </p>
    </StatusBanner>

    <p class="push__note">
      This sends the copy on disk to the entry's source, which is where everyone installs
      from. It does not change the catalog entry.
    </p>

    <label class="push__field">
      <span>Which copy to push</span>
      <select v-model="scope">
        <option value="" disabled>Choose a copy…</option>
        <option v-for="option in scopes" :key="option" :value="option">{{ option }}</option>
      </select>
    </label>

    <template v-if="scope === 'project'">
      <div class="push__field">
        <span>Project directory</span>
        <div class="push__row">
          <code v-if="projectDir" class="push__dir">{{ projectDir }}</code>
          <button type="button" :class="{ ghost: !!projectDir }" @click="pickDir">
            {{ projectDir ? "Change…" : "Choose directory…" }}
          </button>
        </div>
        <ul v-if="recents.length && !projectDir" class="push__recents">
          <li v-for="dir in recents" :key="dir">
            <button type="button" class="ghost" @click="useDir(dir)">{{ dir }}</button>
          </li>
        </ul>
      </div>
    </template>

    <div class="push__actions">
      <button type="button" :disabled="!scope || needsDir || running" @click="runPreview()">
        Preview what would be sent
      </button>
    </div>
    <p v-if="needsDir" class="push__blocked">
      A project copy is found relative to its directory, so pick one first.
    </p>

    <Busy v-if="running && !preview" inline label="Checking what would be sent…" />

    <div v-if="preview" class="push__confirm fade-in">
      <!-- The CLI writes this to stderr, where --json sends it nowhere a GUI can read.
           Shown before the push, because afterwards it is a post-mortem. -->
      <p v-if="preview.note" class="push__provenance">{{ preview.note }}</p>

      <p v-if="!preview.would_change" class="push__question">
        Nothing to send — this copy already matches its source.
      </p>
      <template v-else>
        <p class="push__question">
          <template v-if="preview.branch">
            This opens a pull request against {{ name }}'s source repository.
          </template>
          <template v-else>
            This overwrites {{ name }}'s source on this machine. There is no pull request and
            no review.
          </template>
        </p>

        <p v-if="preview.dest" class="push__dest"><code>{{ preview.dest }}</code></p>
        <pre v-if="preview.diff" class="push__diff">{{ preview.diff }}</pre>

        <label v-if="preview.branch" class="push__field">
          <span>Message — the commit, and the pull request's title</span>
          <input
            v-model="message"
            type="text"
            :placeholder="`library: updated ${name}`"
            v-bind="RAW_TEXT"
          />
        </label>
      </template>

      <div class="push__actions">
        <button type="button" class="ghost" @click="preview = null">Cancel</button>
        <button
          v-if="preview.would_change"
          type="button"
          :disabled="running"
          @click="confirm()"
        >
          {{ preview.branch ? "Push and open a pull request" : "Overwrite the source" }}
        </button>
      </div>
      <Busy v-if="running" inline label="Sending…" />
    </div>
  </section>
</template>

<style scoped>
.push {
  margin-top: 1.75rem;
}
.push__heading {
  margin: 0 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.push__note {
  margin: 0 0 0.75rem;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.7;
}
.push__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.6rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.push__field select,
.push__field input {
  padding: 0.4rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
}
.push__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.push__dir {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}
.push__recents {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.push__recents button {
  padding: 0.2rem 0.5rem;
  font-size: 0.72rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.push__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.push__blocked {
  margin: 0.4rem 0 0;
  font-size: 0.75rem;
  opacity: 0.6;
}
.push__confirm {
  margin-top: 0.75rem;
  padding: 0.85rem;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.08);
  border-left: 3px solid #3b82f6;
}
.push__provenance {
  margin: 0 0 0.6rem;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  font-size: 0.8rem;
  line-height: 1.45;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
}
.push__question {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.45;
  font-weight: 600;
}
.push__dest {
  margin: 0.5rem 0 0;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}
.push__diff {
  margin: 0.6rem 0 0;
  padding: 0.6rem 0.7rem;
  border-radius: 6px;
  max-height: 18rem;
  overflow: auto;
  background: rgba(128, 128, 128, 0.12);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  line-height: 1.5;
  white-space: pre;
}
.push__done {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
.push__done-detail {
  margin: 0.4rem 0 0;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.85;
  overflow-wrap: anywhere;
}
.push__done-detail button {
  padding: 0.3rem 0.6rem;
  font-size: 0.78rem;
}
</style>
