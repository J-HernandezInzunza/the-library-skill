<script setup lang="ts">
import { computed, ref } from "vue";
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
const branch = ref("main");
const wins = ref(true);
const protectedRemote = ref(true);
const submitting = ref(false);
const failure = ref("");
const report = ref<RegistrationReport | null>(null);

const isRemote = computed(() => mode.value === "remote");

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
const canSubmit = computed(() => filled.value && !takenId.value && !submitting.value);

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
    <StatusBanner v-if="failure" kind="error" :detail="failure" />
    <StatusBanner v-else-if="report" kind="success">
      <p class="register__done">
        <template v-if="report.created">Created and registered</template>
        <template v-else>Registered</template>
        <strong>{{ report.id }}</strong> — {{ report.entries }}
        {{ report.entries === 1 ? "entry" : "entries" }}, precedence {{ report.precedence }} of
        {{ report.registered }}.
      </p>
      <p class="register__done-detail">{{ report.location }}</p>
    </StatusBanner>

    <div class="register__modes">
      <label><input v-model="mode" type="radio" value="existing" /> Register an existing catalog</label>
      <label><input v-model="mode" type="radio" value="create" /> Create a new empty one</label>
      <label><input v-model="mode" type="radio" value="remote" /> Add a shared repository</label>
    </div>

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
          <input v-model="branch" type="text" v-bind="RAW_TEXT" />
        </label>
        <label class="register__check">
          <input v-model="protectedRemote" type="checkbox" />
          <span>
            Changes to this catalog go through a pull request. Leave this on for anything your
            team shares; turning it off means a write commits straight to
            {{ branch || "the branch" }}.
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
</style>
