<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import {
  describeDestState,
  describeInstallAction,
  installPlan,
  summarizeChanges,
} from "../catalog";
import { withActivity } from "../commandActivity";
import { forgetProject, recentProjects, rememberProject } from "../recentProjects";
import {
  describeAppError,
  type InstallSource,
  type Pin,
  type UsePreview,
  type UseReport,
} from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** Already on this machine somewhere, so this panel is about adding or refreshing. */
  installed: boolean;
  /** The catalogs this name could come from; empty when only one defines it. */
  sources: InstallSource[];
}>();
const emit = defineEmits<{ installed: [] }>();

const preview = ref<UsePreview | null>(null);
const report = ref<UseReport | null>(null);
const loading = ref(false);
const installing = ref(false);
const error = ref("");
/** Ticked by hand when the plan would discard local edits. */
const acknowledged = ref(false);

/**
 * The catalog to install from, "" meaning whatever resolves.
 *
 * Kept as the empty string rather than pre-filled with the resolving catalog's id so the
 * default install runs the exact command it ran before this picker existed — `--catalog`
 * is only ever added once someone has actually chosen against the default.
 */
const source = ref("");
/** Ticked to write the picked source as a pin, so the next install agrees with this one. */
const remember = ref(false);

/** The catalog a plain install would fetch, which is what the picker starts on. */
const resolving = computed(() => props.sources.find((s) => s.resolves)?.catalog ?? "");
/** True once the picked source is not the one that would resolve on its own. */
const overriding = computed(() => !!source.value && source.value !== resolving.value);

const scope = ref<"global" | "project">("global");
/** The directory this install goes into, chosen for this install alone. */
const projectDir = ref<string | null>(null);
const recents = ref(recentProjects());

/** What the backend sends as `project`: absent for a global install. */
const project = computed(() => (scope.value === "project" ? projectDir.value : null));
const needsDirectory = computed(() => scope.value === "project" && !projectDir.value);

async function pickDirectory() {
  const picked = await open({ directory: true, title: "Install into which project?" });
  if (typeof picked !== "string") return;

  chooseDirectory(picked);
  recents.value = rememberProject(picked);
}

function forget(dir: string) {
  recents.value = forgetProject(dir);
}

function chooseDirectory(dir: string) {
  projectDir.value = dir;
  // The plan named a destination in a different directory, so it no longer describes
  // what would happen.
  preview.value = null;
  acknowledged.value = false;
}

const plan = computed(() => {
  if (!preview.value) return null;
  return installPlan(preview.value, props.name);
});

/** What the button says and what it warns about, both derived from the plan. */
const action = computed(() =>
  plan.value
    ? describeInstallAction(plan.value, scope.value)
    : { label: "Install", caution: null },
);

/** The catalog `overrides` refers to — the requested entry's, never a dependency's. */
const winningCatalog = computed(
  () => plan.value?.items.find((item) => item.target)?.install.catalog ?? "",
);

const canInstall = computed(() => {
  if (!plan.value || installing.value || needsDirectory.value) return false;
  return !plan.value.blocked || acknowledged.value;
});

/** Installed, but the main file the type expects is not there. */
const unverified = computed(() => report.value?.installed.filter((item) => !item.verified) ?? []);

async function runPreview() {
  loading.value = true;
  error.value = "";
  report.value = null;
  try {
    preview.value = await withActivity("resolving the destination…", () =>
      invoke<UsePreview>("entry_use_preview", {
        names: [props.name],
        project: project.value,
        catalog: overriding.value ? source.value : null,
      }),
    );
  } catch (e) {
    error.value = describeAppError(e);
    preview.value = null;
  } finally {
    loading.value = false;
  }
}

async function install() {
  installing.value = true;
  error.value = "";
  try {
    // The pin goes first, and a failure here stops the install: it is the cheap,
    // reversible half, and writing the files against a choice that did not stick would
    // leave the next refresh quietly pulling the other copy back.
    if (remember.value && overriding.value) {
      await withActivity(`pinning ${props.name} to ${source.value}…`, () =>
        invoke<Pin>("entry_pin", { name: props.name, catalog: source.value }),
      );
      remember.value = false;
    }
    report.value = await withActivity(`installing ${props.name}…`, () =>
      invoke<UseReport>("entry_use", {
        names: [props.name],
        project: project.value,
        catalog: overriding.value ? source.value : null,
      }),
    );
    // The plan described the disk as it was before the write, so it is now a lie.
    preview.value = null;
    acknowledged.value = false;
    emit("installed");
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    installing.value = false;
  }
}

// A plan resolved for one entry, scope, or source says nothing about another.
watch([() => props.name, scope, source], () => {
  preview.value = null;
  report.value = null;
  error.value = "";
  acknowledged.value = false;
});

// Nothing to remember once the picked source is the one that resolves anyway, and a tick
// left over from a previous selection would write a pin the user is no longer asking for.
watch(overriding, (against) => {
  if (!against) remember.value = false;
});

// The picker starts wherever the catalog currently resolves, including after a pin made
// elsewhere changes that under us.
watch(
  () => props.sources,
  () => {
    source.value = "";
  },
);
</script>

<template>
  <section class="install-preview">
    <!-- Titled by state. "Install" above an entry the list had just badged
         `installed · global` was the page contradicting itself in its first two lines. -->
    <h3 class="install-preview__heading">
      {{ installed ? "Install elsewhere, or refresh a copy" : "Install" }}
    </h3>
    <div class="card">

      <StatusBanner v-if="error" kind="error" :detail="error" />

      <!-- Only when there is a choice: one catalog holding the name makes this a control
           with a single option, which reads as a setting you are failing to use. Above
           scope because it decides *what* gets installed, not where it lands. -->
      <div v-if="sources.length > 1" class="install-preview__sources">
        <p class="install-preview__label">Install from</p>
        <div class="install-preview__source-row">
          <label v-for="option in sources" :key="option.catalog">
            <input
              v-model="source"
              type="radio"
              :value="option.resolves ? '' : option.catalog"
            />
            {{ option.catalog }}
            <span v-if="option.pinned" class="install-preview__source-note">pinned</span>
            <span v-else-if="option.resolves" class="install-preview__source-note">
              by catalog order
            </span>
          </label>
        </div>
        <label v-if="overriding" class="install-preview__remember">
          <input v-model="remember" type="checkbox" />
          <span>
            Always use {{ source }} for {{ name }}. Without this the choice applies to this
            install only, and the next refresh goes back to
            {{ resolving || "whatever the catalog order resolves" }}.
          </span>
        </label>
      </div>

      <div class="install-preview__scopes">
        <label><input v-model="scope" type="radio" value="global" /> Globally</label>
        <label><input v-model="scope" type="radio" value="project" /> Into a project</label>
      </div>

      <div v-if="scope === 'project'" class="install-preview__project">
        <button
          type="button"
          :class="{ ghost: !needsDirectory }"
          @click="pickDirectory()"
        >
          {{ projectDir ? "Choose another…" : "Choose a directory…" }}
        </button>
        <template v-if="projectDir">
          <p class="install-preview__label">Installing into</p>
          <code class="install-preview__dir">{{ projectDir }}</code>
        </template>

        <template v-if="recents.length">
          <p class="install-preview__label">Recent directories</p>
          <ul class="install-preview__recents">
            <li v-for="dir in recents" :key="dir" class="install-preview__recent-row">
              <button
                type="button"
                class="install-preview__recent"
                :class="{ 'install-preview__recent--current': dir === projectDir }"
                @click="chooseDirectory(dir)"
              >
                {{ dir }}
              </button>
              <button
                type="button"
                class="install-preview__forget"
                :title="`Forget ${dir}`"
                :aria-label="`Forget ${dir}`"
                @click="forget(dir)"
              >
                &times;
              </button>
            </li>
          </ul>
        </template>
      </div>

      <button
        type="button"
        class="ghost"
        :disabled="loading || needsDirectory"
        @click="runPreview()"
      >
        {{ plan ? "Re-check" : "Preview install" }}
      </button>
      <!-- A disabled control with no stated reason reads as a broken one. -->
      <p v-if="needsDirectory" class="install-preview__blocked">
        Choose a directory first — a project install resolves against it, so there is no
        destination to preview yet.
      </p>

      <Busy v-if="loading" inline label="Resolving the destination…" />

      <template v-if="plan">
        <!-- Per state, not a blanket warning: "this overwrites your edits" is false for a
             clean copy, and a warning that cries wolf stops being read. -->
        <p v-if="action.caution" class="install-preview__caution">{{ action.caution }}</p>

        <p v-if="plan.blocked" class="install-preview__warning">
          Installing overwrites local edits that the tool did not make. The edited copies
          are marked below; they cannot be recovered afterwards.
        </p>

        <p class="install-preview__scope">
          {{ preview?.scope }} · nothing has been written
          <span v-if="preview?.overrides.length">
            · installing the {{ winningCatalog }} copy, over {{ preview.overrides.join(", ") }}
          </span>
        </p>

        <ul class="install-preview__plan fade-in">
          <li
            v-for="item in plan.items"
            :key="item.install.dest"
            class="install-preview__item"
            :class="{ 'install-preview__item--drifted': item.drifted }"
          >
            <span class="install-preview__item-head">
              <strong>{{ item.install.name }}</strong>
              <span v-if="!item.target" class="install-preview__role">dependency</span>
              <span
                class="install-preview__state"
                :class="{ 'install-preview__state--drifted': item.drifted }"
              >
                {{ describeDestState(item.install.state) }}
              </span>
            </span>
            <code class="install-preview__dest">{{ item.install.dest }}</code>
          </li>
        </ul>

        <label v-if="plan.blocked" class="install-preview__ack">
          <input v-model="acknowledged" type="checkbox" />
          Overwrite {{ plan.drifted.length }} locally edited
          {{ plan.drifted.length === 1 ? "copy" : "copies" }}, discarding those edits.
        </label>

        <button
          type="button"
          class="install-preview__go"
          :disabled="!canInstall"
          @click="install()"
        >
          {{ action.label }}
        </button>
        <Busy v-if="installing" inline label="Fetching and writing files…" />
      </template>

      <template v-if="report">
        <p class="install-preview__done fade-in">
          Installed {{ report.installed.length }}
          {{ report.installed.length === 1 ? "item" : "items" }}.
        </p>

        <p v-if="unverified.length" class="install-preview__warning">
          {{ unverified.map((item) => item.name).join(", ") }} landed, but the main file the
          catalog expects is not there. The copy is on disk; the catalog entry needs fixing.
        </p>

        <ul class="install-preview__plan fade-in">
          <li
            v-for="item in report.installed"
            :key="item.dest"
            class="install-preview__item"
          >
            <span class="install-preview__item-head">
              <strong>{{ item.name }}</strong>
              <span class="install-preview__state">{{ summarizeChanges(item.changes) }}</span>
            </span>
            <code class="install-preview__dest">{{ item.dest }}</code>
            <ul v-if="!item.changes.new_install" class="install-preview__files">
              <li v-for="file in item.changes.modified" :key="`~${file}`">~ {{ file }}</li>
              <li v-for="file in item.changes.added" :key="`+${file}`">+ {{ file }}</li>
              <li v-for="file in item.changes.removed" :key="`-${file}`">- {{ file }}</li>
            </ul>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.install-preview {
  margin-top: 1.75rem;
}
.install-preview__heading {
  margin: 0 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.install-preview__caution {
  margin: 0.5rem 0 0;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.75;
}
.install-preview__warning {
  margin: 0.75rem 0 0;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.install-preview__scope {
  margin: 0.75rem 0 0.5rem;
  font-size: 0.75rem;
  opacity: 0.6;
}
.install-preview__sources {
  margin-bottom: 0.9rem;
}
.install-preview__source-row {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
}
.install-preview__source-row label {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.install-preview__source-note {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.5;
}
.install-preview__remember {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  margin-top: 0.55rem;
  font-size: 0.76rem;
  line-height: 1.45;
  opacity: 0.85;
}
.install-preview__scopes {
  display: flex;
  gap: 1rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
}
.install-preview__scopes label {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.install-preview__project {
  margin-bottom: 0.75rem;
}
.install-preview__dir {
  display: block;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}
.install-preview__label {
  margin: 0.6rem 0 0.2rem;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.install-preview__recents {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  align-items: flex-start;
}
.install-preview__recent-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.install-preview__recent {
  padding: 0.15rem 0.35rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  font-weight: normal;
  opacity: 0.6;
  cursor: pointer;
}
.install-preview__recent:hover {
  background: var(--surface-hover);
  opacity: 1;
}
.install-preview__recent--current {
  opacity: 1;
  font-weight: 600;
}
.install-preview__forget {
  padding: 0 0.3rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
  line-height: 1;
  opacity: 0.35;
  cursor: pointer;
}
.install-preview__forget:hover {
  background: var(--surface-hover);
  opacity: 1;
}
.install-preview__plan {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.install-preview__item {
  padding: 0.5rem 0.85rem;
  border-radius: 8px;
  background: var(--surface-raised);
}
.install-preview__item--drifted {
  border-left: 3px solid var(--status-attention-ink);
}
.install-preview__item-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
}
.install-preview__role {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.55;
}
.install-preview__state {
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: var(--surface-sunken);
}
.install-preview__state--drifted {
  background: var(--status-attention-tint);
  color: var(--status-attention-ink);
  font-weight: 600;
}
.install-preview__dest {
  display: block;
  margin-top: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.65;
  overflow-wrap: anywhere;
}
.install-preview__files {
  list-style: none;
  margin: 0.35rem 0 0;
  padding: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.6;
}
.install-preview__ack {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  margin: 0.75rem 0 0;
  font-size: 0.82rem;
  line-height: 1.4;
}
.install-preview__go {
  margin-top: 0.75rem;
}
.install-preview__blocked {
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  opacity: 0.7;
}
.install-preview__done {
  margin: 0.75rem 0 0.5rem;
  font-size: 0.85rem;
  color: var(--status-ok-ink);
  font-weight: 600;
}
</style>
