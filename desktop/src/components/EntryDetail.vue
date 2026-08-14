<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import {
  catalogHue,
  contributedCatalogs,
  dependencies,
  dependents,
  editableCopies,
  isOnDisk,
} from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type Catalog, type Entry, type EntryDetail } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import InstallPreview from "./InstallPreview.vue";
import UninstallControl from "./UninstallControl.vue";
import EntryEditor from "./EntryEditor.vue";
import EntryRemove from "./EntryRemove.vue";

const props = defineProps<{
  name: string;
  /** The entry Back returns to; null means the catalog. */
  backTo: string | null;
  catalogs: Catalog[];
  entries: Entry[];
}>();
const emit = defineEmits<{ close: []; open: [name: string]; installed: [] }>();

/** Both views hold state the write just invalidated, so both re-read it. */
async function afterWrite() {
  emit("installed");
  await load(props.name);
}

/**
 * After a removal there is no entry left to show.
 *
 * Re-reading would run `show` against a name the catalog no longer has, turning a
 * successful removal into a failed command.
 */
function afterRemove() {
  emit("installed");
  emit("close");
}

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

/** The other direction: what breaks if this entry goes away. */
const users = computed(() => {
  if (!detail.value) return [];
  return dependents(detail.value, props.entries);
});
/** Only a dependent that is actually on disk is broken by removing this copy today. */
const affected = computed(() => users.value.filter((user) => isOnDisk(user.state)));

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
    detail.value = await withActivity(`reading ${name}…`, () =>
      invoke<EntryDetail>("entry_show", { name }),
    );
  } catch (e) {
    error.value = describeAppError(e);
  } finally {
    loading.value = false;
  }
}

/**
 * The copies this app will edit: those in a catalog on this machine (R4.4).
 *
 * Same restriction as the add form, for the same reason — editing a remote catalog's
 * copy pushes a branch to a shared repository, which belongs in that repository's own
 * review workflow.
 */
const editable = computed(() => editableCopies(detail.value?.copies ?? [], props.catalogs));

/**
 * Which copy the edit and remove controls act on.
 *
 * Named explicitly on every call rather than left to precedence: `update` and `remove`
 * resolve through it otherwise and hand back exit 2 for a name two catalogs hold, which
 * is a question the user has already answered by choosing here.
 */
const editing = ref<string | null>(null);
const editingCopy = computed(
  () => editable.value.find((copy) => copy.catalog === editing.value) ?? editable.value[0] ?? null,
);

/** The catalogs holding this name that the app deliberately will not write to. */
const contributedHolders = computed(() => {
  const shared = new Set(contributedCatalogs(props.catalogs).map((catalog) => catalog.id));
  return (detail.value?.copies ?? [])
    .map((copy) => copy.catalog)
    .filter((catalog) => shared.has(catalog));
});

watch(() => props.name, load, { immediate: true });
// A catalog selected for the previous entry says nothing about this one.
watch(detail, () => {
  editing.value = null;
});
</script>

<template>
  <section class="entry-detail">
    <button type="button" class="entry-detail__back ghost" @click="$emit('close')">
      ← Back to {{ backTo ?? "catalog" }}
    </button>

    <Busy v-if="loading" :label="`Reading ${name}…`" />
    <StatusBanner v-else-if="error" kind="error" :detail="error" />

    <template v-else-if="detail">
      <header class="entry-detail__head fade-in">
        <h2 class="entry-detail__name">{{ detail.name }}</h2>
        <span class="entry-detail__type">{{ detail.entry.type }}</span>
        <span v-if="detail.has_setup" class="entry-detail__setup">guided setup available</span>
      </header>
      <p class="entry-detail__desc">{{ detail.entry.description }}</p>

      <InstallPreview :name="detail.name" @installed="afterWrite()" />

      <UninstallControl
        :name="detail.name"
        :scopes="detail.entry.scopes"
        :installs="detail.installs"
        :affected="affected.map((user) => user.entry.name)"
        @uninstalled="afterWrite()"
      />

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

      <h3 class="entry-detail__section">Edit the catalog entry</h3>
      <template v-if="editingCopy">
        <label v-if="editable.length > 1" class="entry-detail__pick">
          <span>Which copy</span>
          <select v-model="editing">
            <option v-for="copy in editable" :key="copy.catalog" :value="copy.catalog">
              {{ copy.catalog }}
            </option>
          </select>
        </label>

        <EntryEditor
          :name="detail.name"
          :copy="editingCopy"
          :entries="entries"
          @saved="afterWrite()"
        />
        <EntryRemove
          :name="detail.name"
          :copy="editingCopy"
          :scopes="detail.entry.scopes"
          :installs="detail.installs"
          @removed="afterRemove()"
        />
      </template>

      <p v-else-if="contributedHolders.length" class="entry-detail__contributed">
        {{ contributedHolders.join(", ") }}
        {{ contributedHolders.length > 1 ? "are shared catalogs" : "is a shared catalog" }}, so
        this entry is changed in the repository itself rather than from here — that way the
        change goes through the same review as any other.
      </p>
      <p v-else class="entry-detail__contributed">
        No catalog on this machine holds this entry, so there is nothing here to edit.
      </p>

      <template v-if="declared.length">
        <h3 class="entry-detail__section">Requires ({{ declared.length }})</h3>
        <ul class="entry-detail__requires">
          <li v-for="dep in declared" :key="dep.entry.name">
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

      <template v-if="users.length">
        <h3 class="entry-detail__section">Required by ({{ users.length }})</h3>
        <ul class="entry-detail__requires">
          <li v-for="user in users" :key="user.entry.name">
            <button type="button" class="entry-detail__dep" @click="$emit('open', user.entry.name)">
              <span class="entry-detail__dep-head">
                <strong>{{ user.entry.name }}</strong>
                <span v-if="!user.entry.direct" class="entry-detail__indirect">
                  via another entry
                </span>
                <span
                  class="entry-detail__dep-state"
                  :class="{ 'entry-detail__dep-state--missing': !isOnDisk(user.state) }"
                >
                  {{ isOnDisk(user.state) ? "installed" : "not installed" }}
                </span>
              </span>
              <span class="entry-detail__req-desc">{{ user.entry.description }}</span>
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
.entry-detail__pick {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
  opacity: 0.85;
}
.entry-detail__pick select {
  padding: 0.35rem 0.5rem;
  border-radius: 8px;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
}
.entry-detail__contributed {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.5;
  opacity: 0.7;
}
.entry-detail__indirect {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.5;
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
