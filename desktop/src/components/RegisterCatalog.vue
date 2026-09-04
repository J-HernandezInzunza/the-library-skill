<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { withActivity } from "../commandActivity";
import { describeAppError, type Catalog, type RegistrationReport } from "../types";
import { RAW_TEXT } from "../rawText";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The registry, to catch a duplicate id before the CLI refuses one. */
  catalogs: Catalog[];
}>();
const emit = defineEmits<{ registered: []; close: [] }>();

/**
 * The three things you can be doing, which are genuinely different acts.
 *
 * `create` scaffolds a file that does not exist yet — the answer for a teammate with no
 * catalog of their own, and the reason the registry's empty state has somewhere to point.
 */
type Mode = "existing" | "create" | "remote";
const mode = ref<Mode>("existing");

const id = ref("");
const path = ref("");
const repo = ref("");
const branch = ref("");
const wins = ref(true);
const protectedRemote = ref(true);
/** Required only when `protectedRemote` is off — the deliberate second step for
 * choosing a write mode with no review, not a checkbox that trades away review by
 * itself. */
const directPushAck = ref(false);
const submitting = ref(false);
const failure = ref("");
const report = ref<RegistrationReport | null>(null);
/** Whichever catalog held precedence 1 when submit was pressed, captured before the
 * registry can change out from under it — used only to report a "wins" registration
 * bumping it down, never to decide whether the registration itself is allowed. */
const displacedTop = ref<Catalog | null>(null);

const isRemote = computed(() => mode.value === "remote");
const needsDirectPushAck = computed(() => isRemote.value && !protectedRemote.value);

// Re-checking the PR box retracts the acknowledgment, so turning it off a second time
// asks again rather than trading on a tick left over from earlier in the same visit.
watch(protectedRemote, (on) => {
  if (on) directPushAck.value = false;
});

/**
 * The CLI refuses a duplicate id, so the form says so first.
 *
 * Case-sensitive, because the registry is: two ids differing only in case are two
 * catalogs, and being helpfully lenient here would promise a collision the CLI will not
 * report. Same reasoning as the add form's name check (T4.3).
 */
const takenId = computed(() =>
  props.catalogs.some((catalog) => catalog.id === id.value.trim()),
);

const filled = computed(() => {
  if (!id.value.trim()) return false;
  return isRemote.value ? !!repo.value.trim() && !!branch.value.trim() : !!path.value.trim();
});
const canSubmit = computed(
  () => filled.value && !takenId.value && !submitting.value && (!needsDirectPushAck.value || directPushAck.value),
);

/** A one-line description of what picking this mode means, since the bare radio
 * labels don't say what "existing" vs. "remote" cashes out to until you've already
 * committed to one and seen the fields change underneath it. */
const modeHint = computed(() => {
  switch (mode.value) {
    case "existing":
      return "Point at a library.yaml already on this machine — usually a repo you already have cloned.";
    case "create":
      return "Scaffold an empty library.yaml at a location you choose, then register it.";
    case "remote":
      return "Clone a git repository this machine doesn't have yet — how you add a team's shared catalog.";
    default:
      return "";
  }
});

/** 1st / 2nd / 3rd / 4th…, only ever applied to small counts (the number of
 * registered catalogs), so no locale-aware pluralization is warranted. */
function ordinal(n: number): string {
  const suffixes = ["th", "st", "nd", "rd"];
  const mod100 = n % 100;
  return `${n}${suffixes[(mod100 - 20) % 10] ?? suffixes[mod100] ?? suffixes[0]}`;
}

/** Precedence in plain language, same reasoning as the "wins" checkbox above it:
 * "precedence 1 of 2" means nothing without already knowing what precedence controls. */
const precedenceLine = computed(() => {
  if (!report.value) return "";
  const { precedence, registered } = report.value;
  if (registered <= 1) return "the only catalog registered — nothing else for it to collide with.";
  if (precedence === 1) {
    return `checked first of ${registered} catalogs — its copies win if another catalog defines the same name.`;
  }
  return `checked ${ordinal(precedence)} of ${registered} catalogs — a catalog checked earlier wins on a name collision.`;
});

/**
 * Says so when "wins" just bumped a previous front-runner down, since `precedenceLine`
 * only describes the new catalog's own slot and a silent demotion is exactly the
 * surprise a "which catalog wins" checkbox exists to prevent.
 *
 * `--position first` is a plain insert-at-0, no other tiebreak, so any catalog that
 * held precedence 1 before this one registered is now one slot further back — this
 * names it rather than leaving that to be discovered on the next collision.
 */
const displacementLine = computed(() => {
  if (!report.value || !displacedTop.value) return "";
  if (displacedTop.value.id === report.value.id) return "";
  return `This also moved ${displacedTop.value.id} down to precedence 2.`;
});

/** Back to a blank form, for registering a second catalog without leaving the panel. */
function registerAnother() {
  report.value = null;
  displacedTop.value = null;
  id.value = "";
  path.value = "";
  repo.value = "";
  branch.value = "";
  directPushAck.value = false;
}

async function pickPath() {
  const picked = await open({
    directory: true,
    title:
      mode.value === "create"
        ? "Where should the new catalog live?"
        : "Which directory holds the library.yaml?",
  });
  if (typeof picked === "string") path.value = picked;
}

async function submit() {
  submitting.value = true;
  failure.value = "";
  report.value = null;
  displacedTop.value = wins.value ? props.catalogs.find((c) => c.precedence === 1) ?? null : null;
  try {
    report.value = await withActivity(`registering ${id.value.trim()}…`, () =>
      invoke<RegistrationReport>("registry_add", {
        request: {
          id: id.value.trim(),
          path: isRemote.value ? null : path.value.trim(),
          repo: isRemote.value ? repo.value.trim() : null,
          branch: isRemote.value ? branch.value.trim() : null,
          wins: wins.value,
          protected: isRemote.value && protectedRemote.value,
          create: mode.value === "create",
        },
      }),
    );
    emit("registered");
  } catch (e) {
    failure.value = describeAppError(e);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="register card">
    <!-- A successful register collapses to just the outcome. Leaving the form visible
         and re-validated against its own submitted values is what produced the "already
         registered" error appearing right next to the success banner it was reporting. -->
    <template v-if="report">
      <StatusBanner kind="success">
        <p class="register__done">
          <template v-if="report.created">Created and registered </template>
          <template v-else>Registered </template>
          <strong>{{ report.id }}</strong> — {{ report.entries }}
          {{ report.entries === 1 ? "entry" : "entries" }}, {{ precedenceLine }}
        </p>
        <p v-if="displacementLine" class="register__done-displaced">{{ displacementLine }}</p>
        <p class="register__done-detail">{{ report.location }}</p>
      </StatusBanner>
      <div class="register__actions">
        <button type="button" @click="registerAnother">Register another…</button>
        <button type="button" class="ghost" @click="emit('close')">Done</button>
      </div>
    </template>

    <template v-else>
      <StatusBanner v-if="failure" kind="error" :detail="failure" />

      <div class="register__modes">
        <label><input v-model="mode" type="radio" value="existing" /> Register an existing catalog</label>
        <label><input v-model="mode" type="radio" value="create" /> Create a new empty one</label>
        <label><input v-model="mode" type="radio" value="remote" /> Add a shared repository</label>
      </div>
      <p class="register__hint register__mode-hint">{{ modeHint }}</p>

      <form class="register__form" @submit.prevent="submit">
        <label class="register__field">
          <span>Id — how you refer to this catalog</span>
          <input v-model="id" type="text" placeholder="work" v-bind="RAW_TEXT" />
          <span v-if="takenId" class="register__conflict">
            <code>{{ id.trim() }}</code> is already registered. Ids have to be unique, and the
            CLI refuses a duplicate.
          </span>
        </label>

        <template v-if="!isRemote">
          <div class="register__field">
            <span>{{ mode === "create" ? "Where to create it" : "Where it is" }}</span>
            <div class="register__row">
              <code v-if="path" class="register__path">{{ path }}</code>
              <button type="button" :class="{ ghost: !!path }" @click="pickPath">
                {{ path ? "Change…" : "Choose directory…" }}
              </button>
            </div>
            <span class="register__hint">
              <template v-if="mode === 'create'">
                A <code>library.yaml</code> is written here, with empty skill, agent, and prompt
                sections. The CLI refuses to overwrite one that already exists.
              </template>
              <template v-else>
                A <code>library.yaml</code>, or a directory holding one.
              </template>
            </span>
          </div>
        </template>

        <template v-else>
          <label class="register__field">
            <span>Clone URL</span>
            <input
              v-model="repo"
              type="text"
              placeholder="git@github.com:acme/agentics.git"
              v-bind="RAW_TEXT"
            />
          </label>
          <label class="register__field">
            <span>Branch</span>
            <input v-model="branch" type="text" placeholder="main" v-bind="RAW_TEXT" />
            <span class="register__hint">
              The repository's own default branch may not be called <code>main</code> —
              check before typing it.
            </span>
          </label>
          <label class="register__check">
            <input v-model="protectedRemote" type="checkbox" />
            <span>
              Changes to this catalog go through a pull request. Leave this on for anything your
              team shares; turning it off means a write commits straight to
              {{ branch || "the branch" }}, with no review.
            </span>
          </label>
          <label v-if="needsDirectPushAck" class="register__check register__check--warn">
            <input v-model="directPushAck" type="checkbox" />
            <span>
              I understand: with the box above unchecked, edits to this catalog push directly to
              {{ branch || "the branch" }} — no pull request, no review step. This has to be
              checked to register with direct pushes on.
            </span>
          </label>
          <span class="register__hint">
            Registering clones the repository, so this one needs the network.
          </span>
        </template>

        <!-- Precedence in plain language. `--position first` is the CLI's default and gets it
             backwards silently, so the form states the consequence rather than the flag. -->
        <label class="register__check">
          <input v-model="wins" type="checkbox" />
          <span>
            When another catalog defines the same name, use this catalog's copy. That is
            usually right for a catalog of your own and wrong for one you are only reading.
          </span>
        </label>

        <div class="register__actions">
          <button type="submit" :disabled="!canSubmit">
            {{ mode === "create" ? "Create and register" : "Register" }}
          </button>
          <button type="button" class="ghost" @click="emit('close')">Done</button>
        </div>
        <Busy v-if="submitting" inline label="Checking the catalog is usable…" />
      </form>
    </template>
  </section>
</template>

<style scoped>
.register {
  margin-bottom: 1rem;
}
.register__modes {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 0.9rem;
  font-size: 0.8rem;
}
.register__modes label {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.register__mode-hint {
  margin: -0.5rem 0 0.9rem;
}
.register__form {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.register__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.register__field input[type="text"] {
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.register__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.register__path {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}
.register__hint {
  font-size: 0.72rem;
  line-height: 1.45;
  opacity: 0.6;
}
.register__check {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  font-size: 0.76rem;
  line-height: 1.45;
  opacity: 0.85;
}
.register__check--warn {
  padding: 0.5rem 0.6rem;
  border-radius: 6px;
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
}
.register__conflict {
  margin-top: 0.3rem;
  padding: 0.45rem 0.6rem;
  border-radius: 6px;
  font-size: 0.75rem;
  line-height: 1.45;
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.1);
}
.register__actions {
  display: flex;
  gap: 0.5rem;
}
.register__done {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}
.register__done-detail {
  margin: 0.35rem 0 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.75rem;
  opacity: 0.75;
  overflow-wrap: anywhere;
}
.register__done-displaced {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.8;
}
</style>
