<script setup lang="ts">
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeAppError, type BootstrapReport, type InitReport } from "../types";

const props = defineProps<{
  /** Which half of setup is missing. */
  state: "not_bootstrapped" | "not_configured";
  /** The tool directory to prepare, or the config file that is missing. */
  path: string;
}>();
const emit = defineEmits<{ ready: [] }>();

const running = ref(false);
const failure = ref("");
const report = ref<BootstrapReport | null>(null);

/**
 * Bootstrapping can succeed and still leave the tool unusable, because a venv and a
 * catalog registration are separate problems. So the screen advances to the second
 * stage rather than handing back an empty catalog.
 */
const stage = computed(() => {
  if (props.state === "not_configured") return "configure";
  if (report.value && !report.value.config_exists) return "configure";
  return "bootstrap";
});

const configPath = computed(() => report.value?.config_path ?? props.path);

const repo = ref("");
const branch = ref("main");
const registering = ref(false);
const canRegister = computed(() => !!repo.value.trim() && !!branch.value.trim());

/** Shown before it runs, so the form is never a black box (D5). */
const initCommand = computed(
  () =>
    `library init --repo ${repo.value.trim() || "<url>"} --branch ${branch.value.trim() || "<branch>"}`,
);

async function register() {
  registering.value = true;
  failure.value = "";
  try {
    await invoke<InitReport>("catalog_init", {
      repo: repo.value.trim(),
      branch: branch.value.trim(),
    });
    emit("ready");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    registering.value = false;
  }
}

async function setUp() {
  running.value = true;
  failure.value = "";
  try {
    const result = await invoke<BootstrapReport>("bootstrap_tool");
    report.value = result;
    if (result.config_exists) emit("ready");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}
</script>

<template>
  <section class="first-run">
    <template v-if="stage === 'bootstrap'">
      <h2 class="first-run__title">Let's set up your library</h2>
      <p class="first-run__lead">
        The tool at <code>{{ path }}</code> hasn't been prepared yet, so it can't read your
        catalog. Setting it up creates a private Python environment inside that folder and
        installs the one package the CLI needs.
      </p>
      <p class="first-run__note">
        Nothing outside that folder is touched, and running it again later is harmless.
      </p>

      <button type="button" class="first-run__action" :disabled="running" @click="setUp">
        {{ running ? "Setting up…" : "Set up the library" }}
      </button>
    </template>

    <template v-else>
      <h2 class="first-run__title">Point it at your catalog</h2>
      <p class="first-run__lead">
        The tool is ready to run, but it doesn't know which catalog to read. Your team's catalog
        is a git repository with a <code>library.yaml</code> at its root.
      </p>

      <form class="first-run__form" @submit.prevent="register">
        <label class="first-run__field">
          <span>Catalog repository</span>
          <input
            v-model="repo"
            type="text"
            placeholder="git@github.com:your-team/agent-library.git"
            autofocus
          />
        </label>

        <label class="first-run__field">
          <span>Branch</span>
          <input v-model="branch" type="text" placeholder="main" />
        </label>

        <p class="first-run__preview">{{ initCommand }}</p>

        <button type="submit" class="first-run__action" :disabled="!canRegister || registering">
          {{ registering ? "Cloning catalog…" : "Connect catalog" }}
        </button>
      </form>

      <p class="first-run__note">
        Registering writes <code>{{ configPath }}</code> and clones the catalog, which can take a
        few seconds.
      </p>
    </template>

    <pre v-if="failure" class="first-run__failure">{{ failure }}</pre>

    <dl v-if="report" class="first-run__report">
      <dt>Python</dt>
      <dd>{{ report.venv_python }}</dd>
      <dt>CLI</dt>
      <dd>{{ report.wrapper }}</dd>
    </dl>
  </section>
</template>

<style scoped>
.first-run {
  max-width: 34rem;
  margin: 3rem auto;
  text-align: center;
}
.first-run__title {
  margin: 0 0 0.75rem;
  font-size: 1.25rem;
}
.first-run__lead {
  margin: 0 0 0.75rem;
  line-height: 1.5;
  opacity: 0.85;
}
.first-run__lead code,
.first-run__note code {
  font-size: 0.85em;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.15);
}
.first-run__command {
  margin: 0 0 0.75rem;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  text-align: left;
  overflow-x: auto;
  background: rgba(128, 128, 128, 0.12);
  font-size: 0.82rem;
  user-select: all;
}
.first-run__note {
  margin: 0 0 1.5rem;
  font-size: 0.85rem;
  opacity: 0.6;
}
.first-run__action:disabled {
  opacity: 0.5;
  cursor: default;
}
.first-run__form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  text-align: left;
  margin-bottom: 1rem;
}
.first-run__field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.first-run__field input {
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.first-run__preview {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.72rem;
  opacity: 0.5;
  overflow-wrap: anywhere;
}
.first-run__failure {
  margin: 1.5rem 0 0;
  padding: 1rem;
  border-radius: 8px;
  text-align: left;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.first-run__report {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.3rem 0.75rem;
  margin: 1.5rem 0 0;
  text-align: left;
  font-size: 0.78rem;
  opacity: 0.7;
}
.first-run__report dt {
  font-weight: 600;
}
.first-run__report dd {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
}
</style>
