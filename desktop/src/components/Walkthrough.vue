<script setup lang="ts">
/**
 * The guided setup conversation (R5.2, R5.5).
 *
 * A panel over one agent session: the transcript, the commands it ran, the field it asks for a
 * credential in, and a box to reply. Everything it shows arrives on Tauri events as the stream is
 * read, so the transcript fills in during a turn rather than at the end of one — a turn runs for
 * tens of seconds, and a panel that stayed blank for that long is indistinguishable from a hang.
 *
 * **It owns the walkthrough's lifetime.** Closing it calls `walkthrough_end`, which retires the
 * MCP token, forgets every collected value, and deletes the agent's config files. That is why the
 * view is the thing that ends a walkthrough rather than the backend timing one out: the user
 * leaving is the signal, and there is no other.
 */
import { computed, onUnmounted, ref, nextTick, useTemplateRef } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { applyEvent, applySaid, type Turn } from "../walkthrough";
import { RAW_TEXT } from "../rawText";
import { describeAppError, type AgentEvent } from "../types";
import PageHeader from "./PageHeader.vue";
import SecretPrompt from "./SecretPrompt.vue";
import StatusBanner from "./StatusBanner.vue";

const props = defineProps<{
  /** The skill being set up. Named in the header, and what turn 1's prompt is built around. */
  skill: string;
  /** The title of the page Back returns to. */
  backTo: string;
}>();
const emit = defineEmits<{ close: [] }>();

const turns = ref<Turn[]>([]);
const running = ref(false);
const error = ref("");
const reply = ref("");
/** Set from the first `init`. Its absence after a turn is what says the session never opened. */
const session = ref("");
const ended = ref(false);
const foot = useTemplateRef<HTMLElement>("foot");
/**
 * Whether the intro has been left behind.
 *
 * A latch, not a derivation. Deriving it from "are there turns yet" put the intro back on screen
 * the moment a turn finished having produced none — which is exactly what a first turn that
 * failed its preflight gate produces, so the one case where the user needs the error was the one
 * case the error was replaced by a Start button.
 */
const begun = ref(false);
/** A reply can only continue a session that exists (R5.4). */
const canReply = computed(() => !running.value && !ended.value && session.value !== "");

const unlisten: Array<() => void> = [];
for (const channel of [
  "agent://init",
  "agent://text",
  "agent://tool",
  "agent://tool_result",
  "agent://rate_limit",
  "agent://done",
]) {
  listen<AgentEvent>(channel, (event) => receive(event.payload)).then((off) => unlisten.push(off));
}

/**
 * Fold one event in, and keep the two things the panel needs that the transcript does not carry.
 *
 * The session id is read here rather than from the command's return value because it arrives on
 * `init`, at the *start* of a turn — a reply box that waits for the turn to finish before knowing
 * the session exists is a reply box disabled for the whole turn it could have been queued during.
 */
function receive(event: AgentEvent) {
  if (event.kind === "init" && event.session_id) session.value = event.session_id;
  turns.value = applyEvent(turns.value, event);
  scrollToEnd();
}

/**
 * Follow the transcript, but only from the bottom.
 *
 * The page scrolls, not a box inside it: every other full-screen view flows down the window
 * (D19), and a chat with its own scroll region inside a scrolling page gives the user two
 * scrollbars for one conversation.
 *
 * Only when they are already at the bottom. Scrolling on every event would yank the view out from
 * under someone reading back through what the agent did — which, during a long turn, is exactly
 * when they are doing it.
 */
function scrollToEnd() {
  const atBottom =
    document.documentElement.scrollHeight -
      window.scrollY -
      document.documentElement.clientHeight <
    120;
  if (!atBottom) return;
  nextTick(() => {
    // Feature-checked because `scrollIntoView` is a browser API with no jsdom implementation,
    // and following the transcript is a nicety — a component that throws when it cannot do it
    // takes the whole panel down for the sake of a scroll position.
    const end = foot.value;
    if (typeof end?.scrollIntoView === "function") end.scrollIntoView({ block: "end" });
  });
}

async function start() {
  begun.value = true;
  running.value = true;
  error.value = "";
  try {
    await invoke("walkthrough_start", { skill: props.skill });
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

async function send() {
  const message = reply.value.trim();
  if (!message || !canReply.value) return;
  reply.value = "";
  turns.value = applySaid(turns.value, message);
  scrollToEnd();

  running.value = true;
  error.value = "";
  try {
    await invoke("walkthrough_say", { message });
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    running.value = false;
  }
}

/**
 * End the walkthrough. Called on close *and* on unmount, deliberately.
 *
 * `walkthrough_end` is idempotent, and the cost of calling it twice is a lock and a `None`. The
 * cost of not calling it is a live tool-endpoint token and a set of collected credentials
 * outliving the panel that collected them, so this errs toward the side that is merely wasteful.
 */
function finish() {
  ended.value = true;
  invoke("walkthrough_end").catch(() => {
    // Nothing useful to say: the panel is going away, and the backend forgets the walkthrough on
    // the next `start` regardless.
  });
}

function close() {
  finish();
  emit("close");
}

onUnmounted(() => {
  for (const off of unlisten) off();
  finish();
});
</script>

<template>
  <section class="view">
    <PageHeader :title="`Set up ${skill}`" :back="backTo" @back="close" />

    <!-- R7.6: one place for how this turned out, at the top of the surface that owns it. -->
    <StatusBanner v-if="error" kind="error" :detail="error" />

    <div v-if="!begun" class="walkthrough__intro card">
      <p class="walkthrough__lede">
        An assistant will read <strong>{{ skill }}</strong
        >'s own documentation, tell you what it needs, and run the setup commands the skill itself
        declares.
      </p>
      <!-- Stated before the walkthrough starts, not after a field appears. This is the one
           promise the user is being asked to rely on, and the moment to read it is while
           deciding, not while holding a token. -->
      <p class="walkthrough__assurance">
        If a credential is needed you will type it into a field in this window. The assistant is
        told only that a value arrived — never the value, its length, or any part of it.
      </p>
      <p class="walkthrough__assurance">
        It can read this skill's files and run the commands the skill declares. It cannot run a
        shell, and it cannot change your catalog.
      </p>
      <button type="button" class="walkthrough__start" @click="start">Start setup</button>
    </div>

    <template v-else>
      <div class="walkthrough__transcript">
        <div
          v-for="turn in turns"
          :key="turn.id"
          class="turn"
          :class="[`turn--${turn.kind}`, { 'turn--nested': 'subagent' in turn && turn.subagent }]"
        >
          <p v-if="turn.kind === 'said'" class="turn__said">{{ turn.text }}</p>
          <p v-else-if="turn.kind === 'text'" class="turn__text">{{ turn.text }}</p>
          <p v-else-if="turn.kind === 'notice'" class="turn__notice">{{ turn.text }}</p>

          <!-- What it did, then what came back. The command log alongside carries the exact
               argv for the calls that spawn a process; this is the narration (R5.5). -->
          <template v-else>
            <p class="turn__tool">
              <span class="turn__tool-mark" aria-hidden="true">›</span>
              <code>{{ turn.label }}</code>
              <span v-if="turn.result === null" class="turn__running">running…</span>
            </p>
            <pre
              v-if="turn.result !== null"
              class="turn__result"
              :class="{ 'turn__result--failed': turn.failed }"
              >{{ turn.result }}</pre
            >
          </template>
        </div>

        <p v-if="running" class="walkthrough__thinking">Working…</p>
      </div>

      <div ref="foot"></div>

      <!-- Mounted inside the walkthrough because the ask belongs to it, and above the reply box
           because it is what the user should answer rather than the chat. -->
      <SecretPrompt />

      <form class="walkthrough__reply" @submit.prevent="send">
        <textarea
          v-model="reply"
          v-bind="RAW_TEXT"
          class="walkthrough__input"
          rows="2"
          :disabled="!canReply"
          :placeholder="canReply ? 'Reply…' : 'Waiting for the assistant…'"
          @keydown.enter.exact.prevent="send"
        />
        <button type="submit" class="walkthrough__send" :disabled="!canReply || !reply.trim()">
          Send
        </button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.walkthrough__intro {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  align-items: flex-start;
}

.walkthrough__lede {
  margin: 0;
  font-size: 0.95rem;
}

.walkthrough__assurance {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.55;
  opacity: 0.75;
}

.walkthrough__start,
.walkthrough__send {
  padding: 0.4rem 0.9rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.35rem;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.walkthrough__send:disabled {
  opacity: 0.5;
  cursor: default;
}

.walkthrough__transcript {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.5rem 0.15rem 1rem;
}

.turn--nested {
  /* A subagent's work, set apart rather than interleaved: read inline it sounds like the
     assistant contradicting itself. */
  margin-left: 1rem;
  padding-left: 0.75rem;
  border-left: 2px solid rgba(128, 128, 128, 0.25);
  opacity: 0.8;
}

.turn__said,
.turn__text,
.turn__notice {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.6;
  /* The agent writes in paragraphs and lists; collapsing its newlines makes a wall. */
  white-space: pre-wrap;
}

.turn__said {
  align-self: flex-end;
  max-width: 80%;
  padding: 0.4rem 0.7rem;
  border-radius: 0.6rem;
  background: rgba(128, 128, 128, 0.14);
}

.turn__notice {
  padding: 0.4rem 0.6rem;
  border-radius: 0.35rem;
  background: rgba(234, 179, 8, 0.12);
  font-size: 0.85rem;
}

.turn__tool {
  display: flex;
  gap: 0.4rem;
  align-items: baseline;
  margin: 0;
  font-size: 0.82rem;
}

.turn__tool-mark {
  opacity: 0.5;
}

.turn__tool code {
  font-size: 0.82rem;
  opacity: 0.85;
}

.turn__running {
  font-size: 0.75rem;
  opacity: 0.6;
}

.turn__result {
  margin: 0.25rem 0 0 1rem;
  padding: 0.4rem 0.55rem;
  max-height: 14rem;
  border-radius: 0.35rem;
  background: rgba(128, 128, 128, 0.1);
  font-size: 0.78rem;
  line-height: 1.5;
  overflow: auto;
  /* stderr's line breaks carry meaning (R1.4), and a tool result is often stderr. */
  white-space: pre-wrap;
  word-break: break-word;
}

.turn__result--failed {
  background: rgba(220, 38, 38, 0.1);
}

.walkthrough__thinking {
  margin: 0;
  font-size: 0.82rem;
  opacity: 0.6;
}

.walkthrough__reply {
  /* Sticky rather than pinned to a fixed-height column: the page scrolls, so this stays
     reachable without the view having to own a viewport of its own. */
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
  padding: 0.6rem 0 0.75rem;
  border-top: 1px solid rgba(128, 128, 128, 0.25);
  /* The app's own sticky surface, the one the catalog toolbar uses: a sticky element
     composites over scrolling content, and a transparent one leaves the transcript
     showing through the reply box. */
  background: var(--app-bg-sticky);
  backdrop-filter: blur(6px);
}

.walkthrough__input {
  flex: 1;
  min-width: 0;
  padding: 0.4rem 0.55rem;
  border: 1px solid rgba(128, 128, 128, 0.4);
  border-radius: 0.35rem;
  background: transparent;
  color: inherit;
  font: inherit;
  resize: none;
}

.walkthrough__input:disabled {
  opacity: 0.6;
}
</style>
