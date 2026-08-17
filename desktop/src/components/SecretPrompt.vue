<script setup lang="ts">
/**
 * The native field a credential is typed into — the whole of D7 on the front end.
 *
 * It opens because an agent asked for a value by key, and it exists so that the asking and the
 * typing happen in different places: the agent's tool call is suspended while this is on screen,
 * and what it gets back says only that a value arrived. Nothing here ever sends the value
 * anywhere but `submit_secret`, and nothing renders it.
 *
 * Mounted once, high in the tree, rather than per panel: the ask belongs to the walkthrough, and
 * a field that unmounted when you navigated away would strand a suspended tool call behind a
 * view you had closed.
 */
import { onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";
import { RAW_TEXT } from "../rawText";
import { describeAppError, type SecretRequest } from "../types";
import StatusBanner from "./StatusBanner.vue";

const ask = ref<SecretRequest | null>(null);
const value = ref("");
const busy = ref(false);
const error = ref("");

const unlisten: Array<() => void> = [];

listen<SecretRequest>("secret://requested", (event) => {
  ask.value = event.payload;
  value.value = "";
  error.value = "";
}).then((off) => unlisten.push(off));

// The backend closes the ask on its own — a walkthrough that ends, or an ask that times out —
// so the field takes its cue from the store rather than assuming it owns the lifecycle.
listen<string>("secret://resolved", (event) => {
  if (ask.value?.key === event.payload) dismiss();
}).then((off) => unlisten.push(off));

onUnmounted(() => {
  for (const off of unlisten) off();
});

/** Forget the ask *and* the typed value. Both, always: see `submit`. */
function dismiss() {
  ask.value = null;
  value.value = "";
  busy.value = false;
}

async function submit() {
  if (!ask.value || !value.value) return;
  busy.value = true;
  error.value = "";
  try {
    await invoke("submit_secret", { key: ask.value.key, value: value.value });
    // Cleared on the way out rather than left for the next ask to overwrite: a value sitting in
    // a reactive ref is a value that can end up in a devtools snapshot or a component dump.
    dismiss();
  } catch (e) {
    error.value = describeAppError(e);
    busy.value = false;
  }
}

async function decline() {
  if (!ask.value) return;
  busy.value = true;
  const { key } = ask.value;
  try {
    await invoke("decline_secret", { key });
    dismiss();
  } catch (e) {
    error.value = describeAppError(e);
    busy.value = false;
  }
}
</script>

<template>
  <!-- No teleport, no overlay of its own: this sits where it is mounted and the walkthrough view
       decides the surrounding chrome. -->
  <section v-if="ask" class="secret" aria-live="polite">
    <h3 class="secret__title">The walkthrough needs a value</h3>

    <!-- The key is shown as code, because it is a config path and not a sentence. -->
    <p class="secret__key"><code>{{ ask.key }}</code></p>

    <!-- The skill author's words, rendered verbatim. A paraphrased scope list is a support
         ticket, so this is never summarised, truncated, or reworded. -->
    <p v-if="ask.guidance" class="secret__guidance">{{ ask.guidance }}</p>

    <p v-if="ask.url" class="secret__where">
      <button type="button" class="secret__link" @click="openUrl(ask.url)">
        {{ ask.url }}
      </button>
    </p>

    <StatusBanner v-if="error" kind="error" :detail="error" />

    <form class="secret__form" @submit.prevent="submit">
      <!-- `type="password"` and no autocomplete: the browser's own credential store is not the
           skill's, and an offer to save this would put a copy somewhere nobody asked for. -->
      <input
        v-model="value"
        v-bind="RAW_TEXT"
        class="secret__field"
        type="password"
        autocomplete="off"
        :aria-label="ask.key"
        :disabled="busy"
      />
      <div class="secret__actions">
        <button type="submit" class="secret__submit" :disabled="busy || !value">
          Submit
        </button>
        <!-- Declining is a real answer, not a cancel: the agent is told, and it stops asking. -->
        <button type="button" class="secret__decline" :disabled="busy" @click="decline">
          Not now
        </button>
      </div>
    </form>

    <p class="secret__assurance">
      Typed here, never in the chat. The assistant is told that a value arrived and nothing else.
    </p>
  </section>
</template>

<style scoped>
.secret {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.85rem 1rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.5rem;
  /* Tinted rather than plain, because this is the one panel in the app the user should not
     mistake for ordinary chrome while it is on screen. */
  background: rgba(59, 130, 246, 0.08);
}

.secret__title {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
}

.secret__key,
.secret__guidance,
.secret__where,
.secret__assurance {
  margin: 0;
  font-size: 0.82rem;
  line-height: 1.5;
}

.secret__guidance {
  /* The author's text can be several sentences, and a clamped scope list is the one thing this
     panel must not do. */
  white-space: pre-wrap;
  opacity: 0.85;
}

.secret__link {
  padding: 0;
  border: none;
  background: none;
  color: #2563eb;
  font: inherit;
  text-align: left;
  text-decoration: underline;
  cursor: pointer;
}

.secret__form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.15rem;
}

.secret__field {
  min-width: 0;
  padding: 0.4rem 0.5rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.35rem;
  background: transparent;
  color: inherit;
  font: inherit;
}

.secret__actions {
  display: flex;
  gap: 0.5rem;
}

.secret__submit,
.secret__decline {
  padding: 0.35rem 0.75rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.35rem;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.secret__submit:disabled,
.secret__decline:disabled {
  opacity: 0.5;
  cursor: default;
}

.secret__assurance {
  opacity: 0.65;
}
</style>
