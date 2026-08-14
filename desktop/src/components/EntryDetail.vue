<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { catalogHue, dependencies } from "../catalog";
import { describeAppError, type Catalog, type Entry, type EntryDetail } from "../types";

const props = defineProps<{ name: string; catalogs: Catalog[]; entries: Entry[] }>();
defineEmits<{ close: []; open: [name: string] }>();

const detail = ref<EntryDetail | null>(null);
const loading = ref(false);
const error = ref("");

const hueByCatalog = computed(
  () => new Map(props.catalogs.map((catalog) => [catalog.id, catalogHue(catalog.precedence)])),
);

/**
 * Dependencies split by whether the entry actually declares them.
 *
 * `show` flattens the transitive closure into one list, so without this the view would
 * claim an entry asks for everything its dependencies drag in.
 */
const declared = computed(() => deps.value.filter((dep) => dep.declared));
const inherited = computed(() => deps.value.filter((dep) => !dep.declared));
const deps = computed(() => {
  if (!detail.value) return [];
  return dependencies(detail.value, props.entries);
});

/** How an unresolved ref failed, in words rather than an enum. */
function brokenBecause(reason: string): string {
  if (reason === "not_found") return "no catalog defines it";
  if (reason === "malformed") return "not a valid type:name reference";
  if (reason === "cycle") return "circular dependency";
  return reason;
}

/** The source's origin, as the CLI parsed it. A local path has no host or branch. */
const origin = computed(() => {
  const source = detail.value?.source;
  if (!source) return null;
  if (!source.repo) return source.raw;

  const repo = [source.org, source.repo].filter(Boolean).join("/");
  return `${source.kind} · ${repo}${source.branch ? ` (${source.branch})` : ""}`;
});

async function load(name: string) {
  loading.value = true;
  error.value = "";
  detail.value = null;
  try {
    detail.value = await invoke<EntryDetail>("entry_show", { name });
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    loading.value = false;
  }
}

watch(() => props.name, load, { immediate: true });
</script>

<template>
  <section class="entry-detail">
    <button type="button" class="entry-detail__back ghost" @click="$emit('close')">
      ← Back to catalog
    </button>

    <p v-if="loading" class="entry-detail__state">Loading…</p>
    <pre v-else-if="error" class="entry-detail__error">{{ error }}</pre>

    <template v-else-if="detail">
      <header class="entry-detail__head">
        <h2 class="entry-detail__name">{{ detail.name }}</h2>
        <span class="entry-detail__type">{{ detail.entry.type }}</span>
        <span v-if="detail.has_setup" class="entry-detail__setup">guided setup available</span>
      </header>
      <p class="entry-detail__desc">{{ detail.entry.description }}</p>

      <h3 class="entry-detail__section">Source</h3>
      <p class="entry-detail__origin">{{ origin }}</p>
      <p v-if="detail.source.file_path" class="entry-detail__path">
        {{ detail.source.file_path }}
      </p>

      <h3 class="entry-detail__section">
        Catalogs holding this name ({{ detail.copies.length }})
      </h3>
      <ul class="entry-detail__copies">
        <li
          v-for="copy in detail.copies"
          :key="copy.catalog"
          class="entry-detail__copy"
          :class="{ 'entry-detail__copy--wins': copy.wins }"
          :style="{ '--catalog-hue': hueByCatalog.get(copy.catalog) ?? 220 }"
        >
          <div class="entry-detail__copy-head">
            <span class="entry-detail__origin-chip">{{ copy.catalog }}</span>
            <span v-if="copy.wins" class="entry-detail__wins">resolves — this is what installs</span>
            <span v-else class="entry-detail__loses">
              overridden by {{ copy.overridden_by.join(", ") }}
            </span>
          </div>
          <p v-if="copy.overrides.length" class="entry-detail__chain">
            overrides {{ copy.overrides.join(", ") }}
          </p>
          <p class="entry-detail__copy-source">{{ copy.source }}</p>
        </li>
      </ul>

      <template v-if="declared.length">
        <h3 class="entry-detail__section">Requires ({{ declared.length }})</h3>
        <ul class="entry-detail__requires">
          <li v-for="dep in declared" :key="dep.entry.name">
            <button type="button" class="entry-detail__dep" @click="$emit('open', dep.entry.name)">
              <span class="entry-detail__dep-head">
                <strong>{{ dep.entry.name }}</strong>
                <span class="entry-detail__origin-chip entry-detail__origin-chip--muted">
                  {{ dep.entry.catalog }}
                </span>
                <span
                  class="entry-detail__dep-state"
                  :class="{ 'entry-detail__dep-state--missing': dep.state !== 'installed' }"
                >
                  {{ dep.state === "installed" ? "installed" : "not installed" }}
                </span>
              </span>
              <span class="entry-detail__req-desc">{{ dep.entry.description }}</span>
            </button>
          </li>
        </ul>
      </template>

      <template v-if="inherited.length">
        <h3 class="entry-detail__section">
          Also installed, via those ({{ inherited.length }})
        </h3>
        <ul class="entry-detail__requires">
          <li v-for="dep in inherited" :key="dep.entry.name">
            <button type="button" class="entry-detail__dep" @click="$emit('open', dep.entry.name)">
              <span class="entry-detail__dep-head">
                <strong>{{ dep.entry.name }}</strong>
                <span
                  class="entry-detail__dep-state"
                  :class="{ 'entry-detail__dep-state--missing': dep.state !== 'installed' }"
                >
                  {{ dep.state === "installed" ? "installed" : "not installed" }}
                </span>
              </span>
            </button>
          </li>
        </ul>
      </template>

      <template v-if="detail.unresolved_requires.length">
        <h3 class="entry-detail__section entry-detail__section--broken">
          Unresolved ({{ detail.unresolved_requires.length }})
        </h3>
        <ul class="entry-detail__requires">
          <li
            v-for="broken in detail.unresolved_requires"
            :key="broken.ref"
            class="entry-detail__broken"
          >
            <code>{{ broken.ref }}</code>
            <span class="entry-detail__broken-why">{{ brokenBecause(broken.reason) }}</span>
            <p class="entry-detail__req-desc">
              Required by {{ broken.required_by }}. This entry will install without it.
            </p>
          </li>
        </ul>
      </template>

      <h3 class="entry-detail__section">Installed copies ({{ detail.installs.length }})</h3>
      <p v-if="!detail.installs.length" class="entry-detail__none">
        Not installed anywhere the tool knows about.
      </p>
      <ul v-else class="entry-detail__installs">
        <li v-for="install in detail.installs" :key="install.dest" class="entry-detail__install">
          <code>{{ install.dest }}</code>
          <p class="entry-detail__install-meta">
            {{ install.scope }} · from {{ install.catalog }} ·
            {{ install.commit.slice(0, 8) }} · {{ install.installed_at }}
          </p>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.entry-detail {
  /* The topbar is not rendered in this view, and it owns the app's top padding. */
  padding: 1.5rem 0 2rem;
}
.entry-detail__back {
  margin-bottom: 1rem;
}
.entry-detail__state,
.entry-detail__none {
  opacity: 0.7;
  font-size: 0.88rem;
}
.entry-detail__error {
  padding: 1rem;
  border-radius: 8px;
  white-space: pre-wrap;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.entry-detail__head {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.entry-detail__name {
  margin: 0;
  font-size: 1.3rem;
}
.entry-detail__type {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.6;
}
.entry-detail__setup {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.18);
  color: #2563eb;
}
.entry-detail__desc {
  margin: 0.5rem 0 0;
  line-height: 1.5;
  opacity: 0.85;
}
.entry-detail__section {
  margin: 1.75rem 0 0.5rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}
.entry-detail__origin,
.entry-detail__path,
.entry-detail__copy-source {
  margin: 0;
  font-size: 0.8rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
  opacity: 0.75;
}
.entry-detail__path {
  opacity: 0.55;
}
.entry-detail__copies,
.entry-detail__requires,
.entry-detail__installs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.entry-detail__copy {
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  border-left: 3px solid hsl(var(--catalog-hue), 65%, 52%);
  background: rgba(128, 128, 128, 0.08);
  opacity: 0.7;
}
.entry-detail__copy--wins {
  opacity: 1;
}
.entry-detail__copy-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.3rem;
}
.entry-detail__origin-chip {
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  background: hsl(var(--catalog-hue, 220), 65%, 50%);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
}
.entry-detail__origin-chip--muted {
  background: rgba(128, 128, 128, 0.35);
  color: inherit;
}
.entry-detail__wins {
  font-size: 0.72rem;
  color: #16a34a;
  font-weight: 600;
}
.entry-detail__loses,
.entry-detail__chain {
  margin: 0 0 0.3rem;
  font-size: 0.72rem;
  opacity: 0.7;
}
.entry-detail__requires li {
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
  font-size: 0.85rem;
}
.entry-detail__dep {
  display: block;
  width: 100%;
  padding: 0.5rem 0.85rem;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.entry-detail__dep:hover {
  background: rgba(128, 128, 128, 0.12);
}
.entry-detail__dep-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.entry-detail__dep-state {
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
}
.entry-detail__dep-state--missing {
  background: rgba(128, 128, 128, 0.2);
  color: inherit;
  opacity: 0.7;
}
.entry-detail__section--broken {
  color: #dc2626;
  opacity: 0.85;
}
.entry-detail__broken {
  padding: 0.5rem 0.85rem;
  border-left: 3px solid #dc2626;
}
.entry-detail__broken-why {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  color: #dc2626;
}
.entry-detail__req-desc {
  margin: 0.25rem 0 0;
  font-size: 0.78rem;
  opacity: 0.7;
}
.entry-detail__install {
  padding: 0.5rem 0.85rem;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.entry-detail__install code {
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}
.entry-detail__install-meta {
  margin: 0.25rem 0 0;
  font-size: 0.72rem;
  opacity: 0.6;
}
</style>
