<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { describePrerequisite, describeSetup, unmetPrerequisites } from "../setup";
import { withActivity } from "../commandActivity";
import { describeAppError, type SetupReport } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  name: string;
  /** On disk somewhere. The manifest belongs to the installed copy, so nothing is
   *  knowable until there is one — and asking costs a subprocess. */
  installed: boolean;
}>();

const report = ref<SetupReport | null>(null);
const loading = ref(false);
const error = ref("");

const summary = computed(() => (report.value ? describeSetup(report.value) : null));
const unmet = computed(() => (report.value ? unmetPrerequisites(report.value) : []));
const met = computed(() => report.value?.prerequisites.filter((pre) => pre.met) ?? []);
const secrets = computed(() => report.value?.manifest?.secrets ?? []);

/**
 * A skill that declares no setup says so by staying quiet.
 *
 * The common case by a wide margin, and a "No setup needed" card on every entry page
 * is noise that trains you to skip the section on the entries where it matters.
 */
const worthShowing = computed(() => summary.value !== null && summary.value.state !== "none");

/** What a collected value does, so "delivery" is not jargon on screen. */
function describeDelivery(delivery: string): string {
  if (delivery === "manual") return "you enter this yourself; the app never sees it";
  if (delivery === "env") return "used for this walkthrough only, never saved";
  if (delivery === "config-file") return "saved to the skill's config file";
  return delivery;
}

async function load(name: string) {
  report.value = null;
  error.value = "";
  // Nothing to ask about: `setup` would answer `installed: false` and cost a
  // subprocess to do it. The panel renders nothing until there is a copy.
  if (!props.installed) return;

  loading.value = true;
  try {
    report.value = await withActivity(`checking what ${name} needs…`, () =>
      invoke<SetupReport>("entry_setup", { name }),
    );
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    loading.value = false;
  }
}

watch([() => props.name, () => props.installed], () => load(props.name), { immediate: true });
</script>

<template>
  <section v-if="loading || error || worthShowing" class="setup">
    <h3 class="setup__heading">Setup</h3>
    <div class="card">
      <StatusBanner v-if="error" kind="error" :detail="error" />
      <Busy v-else-if="loading" inline :label="`Checking what ${name} needs…`" />

      <template v-else-if="summary">
        <p class="setup__headline" :class="`setup__headline--${summary.tone}`">
          {{ summary.headline }}
        </p>
        <p class="setup__detail">{{ summary.detail }}</p>

        <p v-if="report?.manifest?.summary" class="setup__summary">
          {{ report.manifest.summary }}
        </p>

        <!-- Verbatim from the validator. These are the sentences that say what to fix,
             and they belong to whoever maintains the skill. -->
        <template v-if="report?.problems.length">
          <h4 class="setup__section">What is wrong with it</h4>
          <ul class="setup__problems">
            <li v-for="problem in report.problems" :key="problem"><code>{{ problem }}</code></li>
          </ul>
        </template>

        <template v-if="unmet.length">
          <h4 class="setup__section">Not met yet</h4>
          <ul class="setup__prereqs">
            <li v-for="pre in unmet" :key="describePrerequisite(pre)" class="setup__prereq--unmet">
              <span class="setup__prereq-name">{{ describePrerequisite(pre) }}</span>
              <!-- The CLI's own words: "not on PATH", "not set", "not installed",
                   or the version it found. Nothing here can say it better. -->
              <span class="setup__prereq-detail">{{ pre.detail }}</span>
            </li>
          </ul>
        </template>

        <template v-if="met.length">
          <h4 class="setup__section">Already in place</h4>
          <ul class="setup__prereqs">
            <li v-for="pre in met" :key="describePrerequisite(pre)">
              <span class="setup__prereq-name">{{ describePrerequisite(pre) }}</span>
              <span class="setup__prereq-detail">{{ pre.detail }}</span>
            </li>
          </ul>
        </template>

        <!-- A plan, never a value: this says what will be asked for, and nothing is
             collected or stored here. -->
        <template v-if="secrets.length">
          <h4 class="setup__section">What it will ask you for ({{ secrets.length }})</h4>
          <ul class="setup__secrets">
            <li v-for="secret in secrets" :key="secret.key" class="setup__secret">
              <div class="setup__secret-head">
                <strong>{{ secret.label ?? secret.key }}</strong>
                <span v-if="secret.optional" class="setup__optional">optional</span>
              </div>
              <p class="setup__delivery">{{ describeDelivery(secret.delivery) }}</p>
              <!-- Verbatim. A paraphrased token-scope list is a support ticket. -->
              <p v-if="secret.guidance" class="setup__guidance">{{ secret.guidance }}</p>
              <button
                v-if="secret.url"
                type="button"
                class="ghost setup__link"
                @click="openUrl(secret.url)"
              >
                Where to get it
              </button>
            </li>
          </ul>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.setup__heading {
  margin: 1.75rem 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.setup__headline {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
}
.setup__headline--ready {
  color: #16a34a;
}
.setup__headline--attention {
  color: #b45309;
}
.setup__headline--problem {
  color: #dc2626;
}
.setup__detail,
.setup__summary {
  margin: 0.35rem 0 0;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.8;
}
.setup__summary {
  margin-top: 0.7rem;
  padding-left: 0.7rem;
  border-left: 3px solid rgba(128, 128, 128, 0.35);
  opacity: 0.95;
}
.setup__section {
  margin: 1.1rem 0 0.4rem;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.5;
}
.setup__problems,
.setup__prereqs,
.setup__secrets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.setup__problems code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  line-height: 1.45;
  overflow-wrap: anywhere;
  color: #dc2626;
}
.setup__prereqs li {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  flex-wrap: wrap;
  font-size: 0.8rem;
  opacity: 0.7;
}
.setup__prereq--unmet {
  opacity: 1;
}
.setup__prereq-name {
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.setup__prereq-detail {
  opacity: 0.7;
}
.setup__prereq--unmet .setup__prereq-detail {
  color: #b45309;
}
.setup__secret {
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.setup__secret-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
}
.setup__optional {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.55;
}
.setup__delivery {
  margin: 0.2rem 0 0;
  font-size: 0.75rem;
  opacity: 0.6;
}
.setup__guidance {
  margin: 0.35rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
}
.setup__link {
  margin-top: 0.45rem;
  padding: 0.22rem 0.5rem;
  font-size: 0.72rem;
}
</style>
