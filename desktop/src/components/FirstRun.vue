<script setup lang="ts">
import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeAppError, type BootstrapReport } from "../types";

defineProps<{ toolDir: string }>();
const emit = defineEmits<{ ready: [] }>();

const running = ref(false);
const failure = ref("");
const report = ref<BootstrapReport | null>(null);

async function setUp() {
  running.value = true;
  failure.value = "";
  try {
    report.value = await invoke<BootstrapReport>("bootstrap_tool");
    emit("ready");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}
</script>

<template>
  <section class="first-run">
    <h2 class="first-run__title">Let's set up your library</h2>

    <p class="first-run__lead">
      The tool at <code>{{ toolDir }}</code> hasn't been prepared yet, so it can't read your
      catalog. Setting it up creates a private Python environment inside that folder and
      installs the one package the CLI needs.
    </p>
    <p class="first-run__note">
      Nothing outside that folder is touched, and running it again later is harmless.
    </p>

    <button type="button" class="first-run__action" :disabled="running" @click="setUp">
      {{ running ? "Setting up…" : "Set up the library" }}
    </button>

    <pre v-if="failure" class="first-run__failure">{{ failure }}</pre>

    <dl v-if="report" class="first-run__report">
      <dt>Python</dt>
      <dd>{{ report.venv_python }}</dd>
      <dt>CLI</dt>
      <dd>{{ report.wrapper }}</dd>
      <dt>Config</dt>
      <dd>{{ report.config_path }}</dd>
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
  margin: 0 0 0.5rem;
  line-height: 1.5;
  opacity: 0.85;
}
.first-run__lead code {
  font-size: 0.85em;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.15);
}
.first-run__note {
  margin: 0 0 1.5rem;
  font-size: 0.85rem;
  opacity: 0.6;
}
.first-run__action:disabled {
  opacity: 0.6;
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
