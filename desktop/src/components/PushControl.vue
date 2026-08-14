<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { describePush, type InstalledCopy } from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type PushPreview, type PushReport, type Source } from "../types";
import { RAW_TEXT } from "../rawText";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** The copy whose edits are being sent. No dropdown: you pressed its button. */
  copy: InstalledCopy;
  /** The entry's source, as the CLI parsed it — the other end of the operation. */
  source: Source;
}>();
const emit = defineEmits<{ close: [] }>();

const message = ref("");
const preview = ref<PushPreview | null>(null);
const report = ref<PushReport | null>(null);
const running = ref(false);
const failure = ref("");

const outcome = computed(() => (report.value ? describePush(report.value) : null));

/**
 * Where the edits are going, named before anything runs.
 *
 * The first version of this panel asked "which copy to push" and named neither end, so
 * "global" read as a destination when it is the source. Both facts are already loaded —
 * the receipt's path and the parsed source — and showing them is the whole fix.
 */
const destination = computed(() => {
  const source = props.source;
  if (source.kind === "local") return source.raw;
  const repo = [source.org, source.repo].filter(Boolean).join("/");
  return `${repo}${source.branch ? ` (${source.branch})` : ""}`;
});

const opensPullRequest = computed(() => props.source.kind !== "local");

async function runPreview() {
  running.value = true;
  failure.value = "";
  report.value = null;
  try {
    preview.value = await withActivity(`checking what pushing ${props.name} would send…`, () =>
      invoke<PushPreview>("entry_push_preview", { name: props.name, from: props.copy.pushFrom }),
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
        from: props.copy.pushFrom,
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

// Mounted only because the button was pressed, so the preview is the first thing to run —
// as with the removal panel, a second "Preview" button would ask the same question twice.
onMounted(runPreview);
</script>

<template>
  <section class="push">
    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="outcome" kind="success">
      <p class="push__done">{{ outcome.headline }}</p>
      <p v-if="outcome.detail" class="push__done-detail">{{ outcome.detail }}</p>
      <p v-if="outcome.link" class="push__done-detail">
        <button type="button" @click="follow(outcome.link.url)">{{ outcome.link.label }}</button>
      </p>
    </StatusBanner>

    <!-- Both ends, named, before anything runs. -->
    <p class="push__route">
      <code>{{ copy.dest ?? `the ${copy.scope} copy` }}</code>
      <span class="push__arrow">→</span>
      <code>{{ destination }}</code>
    </p>
    <p class="push__note">
      <template v-if="opensPullRequest">
        This opens a pull request against the entry's source repository. Nobody's installed
        copy changes until that is merged.
      </template>
      <template v-else>
        The source is a file on this machine, so this overwrites it directly — no pull
        request, no review.
      </template>
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
        <pre v-if="preview.diff" class="push__diff">{{ preview.diff }}</pre>
        <p v-else-if="preview.dest" class="push__dest">
          Would overwrite <code>{{ preview.dest }}</code>
        </p>

        <label v-if="opensPullRequest" class="push__field">
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
        <button type="button" class="ghost" @click="emit('close')">Cancel</button>
        <button v-if="preview.would_change" type="button" :disabled="running" @click="confirm()">
          {{ opensPullRequest ? "Push and open a pull request" : "Overwrite the source" }}
        </button>
      </div>
      <Busy v-if="running" inline label="Sending…" />
    </div>
  </section>
</template>

<style scoped>
.push__route {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin: 0 0 0.4rem;
  font-size: 0.78rem;
}
.push__route code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
}
.push__arrow {
  opacity: 0.5;
}
.push__note {
  margin: 0 0 0.6rem;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.7;
}
.push__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-top: 0.6rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.push__field input {
  padding: 0.4rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
}
.push__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
}
.push__confirm {
  padding: 0.75rem;
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
  font-size: 0.85rem;
  line-height: 1.45;
}
.push__dest {
  margin: 0;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}
.push__dest code {
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.push__diff {
  margin: 0;
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
