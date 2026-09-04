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
const emit = defineEmits<{
  /** Start a guided walkthrough for this entry. The view above owns the panel. */
  walkthrough: [];
}>();

const report = ref<SetupReport | null>(null);
/**
 * Whether `claude` resolves (R7.2).
 *
 * Asked once per panel rather than held globally: it costs one `--version` and the answer changes
 * when the user installs it, which is precisely when a stale global `false` would be wrong.
 * `null` while unknown, so the button does not flicker in and then out.
 */
const agentReady = ref<boolean | null>(null);
const loading = ref(false);
const error = ref("");

const summary = computed(() => (report.value ? describeSetup(report.value) : null));
const unmet = computed(() => (report.value ? unmetPrerequisites(report.value) : []));
const met = computed(() => report.value?.prerequisites.filter((pre) => pre.met) ?? []);

/**
 * What the manifest says about each value, joined to whether it is on disk.
 *
 * Two lists keyed by the same `key`: the manifest half carries the label, guidance, and
 * url, the report half carries `present`. Joined here rather than in the CLI because the
 * manifest reaches the app whole anyway, and duplicating the author's prose into the
 * status list would give it two places to be wrong.
 */
const secrets = computed(() =>
  (report.value?.manifest?.secrets ?? []).map((secret) => ({
    ...secret,
    present: report.value?.secrets.find((state) => state.key === secret.key)?.present ?? null,
  })),
);

/** Expanded when something is waiting on somebody, or when asked for (R5.1c). */
const opened = ref(false);
const expanded = computed(() => summary.value?.outstanding === true || opened.value);

/** The chip beside a value: what the CLI found, in three words rather than a path. */
function describePresence(present: boolean | null): string {
  if (present === true) return "stored";
  if (present === false) return "not stored yet";
  return "never stored";
}

/**
 * A skill that declares no setup says so by staying quiet.
 *
 * The common case by a wide margin, and a "No setup needed" card on every entry page
 * is noise that trains you to skip the section on the entries where it matters.
 */
const worthShowing = computed(() => summary.value !== null && summary.value.state !== "none");

/**
 * Whether to offer a walkthrough at all.
 *
 * Only for a skill whose manifest validates and whose prerequisites are met — `ready` is the
 * CLI's own verdict on exactly that (R5.1). Offering one over a defective manifest or an unmet
 * prerequisite would start a conversation whose first act is to report the thing the panel is
 * already showing.
 *
 * A `configured` skill still gets the offer, worded as re-running: values drift, tokens expire,
 * and "you already did this" is a reason to make it a secondary action rather than to hide it.
 */
const canWalk = computed(() => agentReady.value === true && report.value?.ready === true);

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
    // After the report, not alongside it: nothing is offered over a skill that is not ready, so
    // an unready one never pays for the check.
    if (report.value.ready) await checkAgent();
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    loading.value = false;
  }
}

/**
 * Whether `claude` is there, asked in a way that cannot take the panel down with it.
 *
 * Its own try/catch rather than sharing `load`'s: this panel's subject is what the skill needs,
 * and the agent is an enhancement (R7.2). A probe that failed inside `load` replaced the whole
 * readiness report — prerequisites, values, everything — with an error about `claude`, which is
 * the one thing on this screen the user did not ask about.
 */
async function checkAgent() {
  try {
    agentReady.value = await invoke<boolean>("agent_available");
  } catch {
    // Indistinguishable from absent, as far as anything here can offer.
    agentReady.value = false;
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
        <!-- A settled panel is one clickable line. Nothing is hidden that is waiting on
             anyone: `outstanding` forces it open, so the toggle only ever appears over a
             skill in good standing (R5.1c). -->
        <button
          v-if="!summary.outstanding"
          type="button"
          class="setup__toggle"
          :aria-expanded="expanded"
          @click="opened = !opened"
        >
          <span class="setup__caret" aria-hidden="true">{{ expanded ? "▾" : "▸" }}</span>
          <span class="setup__headline" :class="`setup__headline--${summary.tone}`">
            {{ summary.headline }}
          </span>
          <!-- Only when there is an exception. An unqualified headline says everything
               there is to say, and silence in this slot means nothing to do. -->
          <span v-if="summary.qualifier" class="setup__qualifier">{{ summary.qualifier }}</span>
        </button>
        <p v-else class="setup__headline" :class="`setup__headline--${summary.tone}`">
          {{ summary.headline }}
        </p>

        <template v-if="expanded">
        <p class="setup__detail">{{ summary.detail }}</p>

        <!-- The offer sits directly under what it would act on, and only over a skill the CLI
             calls ready: starting a conversation whose first act is to report the problem this
             panel is already showing helps nobody. -->
        <button v-if="canWalk" type="button" class="setup__walk" @click="emit('walkthrough')">
          {{ report?.configured ? "Run setup again" : "Set this up" }}
        </button>
        <!-- R7.2: the agent is an enhancement, so a missing one is a fact stated next to the
             thing it would have done, not an error interrupting the page. -->
        <p v-else-if="agentReady === false" class="setup__no-agent">
          Guided setup needs Claude Code installed and signed in. Everything else here works
          without it.
        </p>

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

        <!-- A plan and a state, never a value: this says what will be asked for and
             whether it is already there. Nothing is collected or stored here. -->
        <template v-if="secrets.length">
          <h4 class="setup__section">What it needs ({{ secrets.length }})</h4>
          <ul class="setup__secrets">
            <li v-for="secret in secrets" :key="secret.key" class="setup__secret">
              <div class="setup__secret-head">
                <strong>{{ secret.label ?? secret.key }}</strong>
                <span v-if="secret.optional" class="setup__optional">optional</span>
                <span
                  class="setup__presence"
                  :class="{
                    'setup__presence--stored': secret.present === true,
                    'setup__presence--missing': secret.present === false,
                  }"
                >
                  {{ describePresence(secret.present) }}
                </span>
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
      </template>
    </div>
  </section>
</template>

<style scoped>
.setup__walk {
  align-self: flex-start;
  margin-top: 0.15rem;
  padding: 0.35rem 0.8rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.35rem;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.setup__no-agent {
  margin: 0;
  font-size: 0.82rem;
  line-height: 1.5;
  opacity: 0.7;
}
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
.setup__toggle {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.setup__caret {
  /* The only affordance on the row, so it is sized like one. At 0.7rem/0.5 it read as a
     bullet — decoration rather than the thing to click. */
  display: inline-block;
  width: 0.75rem;
  font-size: 1.25rem;
  opacity: 0.75;
  transition: transform 0.12s ease, opacity 0.12s ease;
}
.setup__toggle:hover .setup__caret {
  opacity: 1;
}
.setup__qualifier {
  /* Pushed right, so the headline reads as the answer and this as the footnote on it. */
  margin-left: auto;
  padding-left: 1rem;
  font-size: 0.75rem;
  opacity: 0.6;
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
.setup__presence {
  /* Right-aligned, apart from the name and the optional tag, because it is the only part
     of the row that changes after the skill is installed. */
  margin-left: auto;
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  white-space: nowrap;
  background: rgba(128, 128, 128, 0.18);
  opacity: 0.8;
}
.setup__presence--stored {
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
  opacity: 1;
}
.setup__presence--missing {
  background: rgba(245, 158, 11, 0.2);
  color: #b45309;
  font-weight: 600;
  opacity: 1;
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
