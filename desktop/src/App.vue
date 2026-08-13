<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { describeAppError } from "./types";

/** One catalog entry as emitted by `library list --json`. */
interface Entry {
  type: string;
  name: string;
  description: string;
  source: string;
  requires: string[];
  installed?: boolean;
  scopes?: string[];
  catalog: string;
  overridden_by?: string | null;
}

const entries = ref<Entry[]>([]);
const query = ref("");
const loading = ref(false);
const error = ref("");

/** Load the full catalog once; search filters this in-memory. */
async function load() {
  loading.value = true;
  error.value = "";
  try {
    entries.value = await invoke<Entry[]>("library_list");
  } catch (e) {
    error.value = describeAppError(e);
    entries.value = [];
  } finally {
    loading.value = false;
  }
}

/** Case-insensitive filter over name + description, computed client-side. */
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return entries.value;
  return entries.value.filter(
    (e) =>
      e.name.toLowerCase().includes(q) ||
      e.description.toLowerCase().includes(q),
  );
});

const installedCount = computed(
  () => filtered.value.filter((e) => e.installed).length,
);

onMounted(load);
</script>

<template>
  <main class="app">
    <header class="topbar">
      <h1>The Library</h1>
      <form class="searchbar" @submit.prevent>
        <input
          v-model="query"
          type="search"
          placeholder="Search skills, agents, prompts…"
        />
        <button type="button" class="ghost" @click="load()">Refresh</button>
      </form>
    </header>

    <p v-if="!loading && !error" class="summary">
      {{ filtered.length }} of {{ entries.length }} entries ·
      {{ installedCount }} installed
    </p>

    <p v-if="loading" class="state">Loading…</p>
    <pre v-else-if="error" class="state error">{{ error }}</pre>
    <p v-else-if="!filtered.length" class="state">No matching entries.</p>

    <ul v-else class="entries">
      <li v-for="e in filtered" :key="`${e.catalog}:${e.type}:${e.name}`" class="entry">
        <div class="entry-head">
          <span class="name">{{ e.name }}</span>
          <span class="type">{{ e.type }}</span>
          <span class="catalog">{{ e.catalog }}</span>
          <span v-if="e.installed" class="badge installed">
            installed{{ e.scopes?.length ? ` · ${e.scopes.join(", ")}` : "" }}
          </span>
          <span v-else class="badge missing">not installed</span>
          <span v-if="e.overridden_by" class="badge overridden">
            overridden by {{ e.overridden_by }}
          </span>
        </div>
        <p class="desc">{{ e.description }}</p>
        <p v-if="e.requires?.length" class="requires">
          requires: {{ e.requires.join(", ") }}
        </p>
      </li>
    </ul>
  </main>
</template>

<style>
:root {
  color-scheme: light dark;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, sans-serif;
}
body {
  margin: 0;
  background: #f6f6f7;
  color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
  body {
    background: #1e1e20;
    color: #e6e6e6;
  }
}
</style>

<style scoped>
.app {
  max-width: 860px;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 3rem;
}
.topbar {
  position: sticky;
  top: 0;
  padding-bottom: 0.75rem;
  backdrop-filter: blur(8px);
}
h1 {
  margin: 0 0 0.75rem;
  font-size: 1.5rem;
}
.searchbar {
  display: flex;
  gap: 0.5rem;
}
.searchbar input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.95rem;
}
button {
  padding: 0.5rem 0.9rem;
  border-radius: 8px;
  border: 1px solid transparent;
  background: #3b82f6;
  color: #fff;
  font-weight: 500;
  cursor: pointer;
}
button.ghost {
  background: transparent;
  color: inherit;
  border-color: rgba(128, 128, 128, 0.4);
}
.summary {
  font-size: 0.85rem;
  opacity: 0.7;
  margin: 0.5rem 0 1rem;
}
.state {
  padding: 2rem 0;
  text-align: center;
  opacity: 0.8;
}
.state.error {
  text-align: left;
  color: #dc2626;
  white-space: pre-wrap;
  background: rgba(220, 38, 38, 0.08);
  padding: 1rem;
  border-radius: 8px;
}
.entries {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.entry {
  padding: 0.85rem 1rem;
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.08);
  border: 1px solid rgba(128, 128, 128, 0.15);
}
.entry-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.name {
  font-weight: 600;
}
.type,
.catalog {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.6;
}
.badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
}
.badge.installed {
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
}
.badge.missing {
  background: rgba(128, 128, 128, 0.18);
  opacity: 0.8;
}
.badge.overridden {
  background: rgba(234, 179, 8, 0.18);
  color: #b45309;
}
.desc {
  margin: 0.4rem 0 0;
  font-size: 0.88rem;
  line-height: 1.4;
  opacity: 0.85;
}
.requires {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
