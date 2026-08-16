<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import {
  catalogHue,
  dependencies,
  dependents,
  editableCopies,
  installStatus,
  installedCopies,
  isOnDisk,
} from "../catalog";
import { withActivity } from "../commandActivity";
import { describeAppError, type Catalog, type Entry, type EntryDetail } from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import InstallPreview from "./InstallPreview.vue";
import InstalledCopies from "./InstalledCopies.vue";
import PageHeader from "./PageHeader.vue";
import SetupReadiness from "./SetupReadiness.vue";

const props = defineProps<{
  name: string;
  /** The entry Back returns to; null means the catalog. */
  backTo: string | null;
  catalogs: Catalog[];
  entries: Entry[];
}>();
const emit = defineEmits<{
  close: [];
  open: [name: string];
  installed: [];
  /** Hand off to the catalog manager, focused on this copy. */
  manage: [payload: { catalog: string; name: string }];
}>();

/** Both views hold state the write just invalidated, so both re-read it. */
async function afterWrite() {
  emit("installed");
  await load(props.name);
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
 * The catalogs whose copy of this name the app will edit (R4.4): those on this machine.
 *
 * Only used to decide whether to offer the hand-off. The forms themselves live in the
 * catalog manager — editing a catalog is a different job from installing an entry, and
 * mixing them is what made this page read as two pages stapled together.
 */
const editableIds = computed(
  () => new Set(editableCopies(detail.value?.copies ?? [], props.catalogs).map((c) => c.catalog)),
);

/**
 * Every copy on this machine, and the one place scope is decided (D21).
 *
 * Built from `scopes` and `installs[]` together because neither is a superset: one is what
 * is on disk at destinations this app resolves, the other is what the tool recorded.
 */
const copies = computed(() =>
  detail.value ? installedCopies(detail.value.entry.scopes, detail.value.installs) : [],
);

/**
 * The same badge the list shows, from the same function.
 *
 * The page used to show none, so an entry the list had just labelled `installed · global`
 * opened with a panel headed "Install" and nothing contradicting it until eight sections
 * further down.
 */
const status = computed(() => (detail.value ? installStatus(detail.value.entry) : null));

watch(() => props.name, load, { immediate: true });
</script>

<template>
  <section class="view">
    <!-- Titled from the prop, not from the payload: the header must be in place before
         the command returns, or it lands late and shifts everything under it. -->
    <PageHeader :title="name" :back="backTo ?? 'The Library'" @back="$emit('close')">
      <span v-if="detail" class="entry-detail__type">{{ detail.entry.type }}</span>
      <span
        v-if="status"
        class="entry-detail__status"
        :class="`entry-detail__status--${status.tone}`"
      >
        {{ status.status }}
      </span>
      <span v-if="detail?.has_setup" class="entry-detail__setup">guided setup available</span>
    </PageHeader>

    <Busy v-if="loading" :label="`Reading ${name}…`" />
    <StatusBanner v-else-if="error" kind="error" :detail="error" />

    <template v-else-if="detail">
      <p class="entry-detail__desc">{{ detail.entry.description }}</p>

      <!-- What you have, before what you could do: the page is most often opened about an
           entry that is already installed, and that was the fact it never stated. -->
      <InstalledCopies
        :name="detail.name"
        :copies="copies"
        :source="detail.source"
        :affected="affected.map((user) => user.entry.name)"
        @changed="afterWrite()"
      />

      <!-- Directly under the install panel, because "it is installed" and "it still
           needs a token before it works" are one thought, and the second is the half
           that a chip in the header cannot carry. -->
      <SetupReadiness :name="detail.name" :installed="copies.length > 0" />

      <InstallPreview
        :name="detail.name"
        :installed="copies.length > 0"
        @installed="afterWrite()"
      />

      <h3 class="entry-detail__section">Source</h3>
      <div class="card">
        <p class="entry-detail__origin">{{ origin }}</p>
        <p v-if="detail.source.file_path" class="entry-detail__path">
          {{ detail.source.file_path }}
        </p>
      </div>

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
          <!-- A pointer, not a form: the edit itself belongs with the other catalog
               management, but noticing a wrong description happens here. -->
          <button
            v-if="editableIds.has(copy.catalog)"
            type="button"
            class="ghost entry-detail__manage"
            @click="emit('manage', { catalog: copy.catalog, name: detail.name })"
          >
            Edit this entry in {{ copy.catalog }}
          </button>
        </li>
      </ul>

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

    </template>
  </section>
</template>

<style scoped>
.entry-detail__state,
.entry-detail__none {
  opacity: 0.7;
  font-size: 0.88rem;
}
.entry-detail__type {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.6;
}
.entry-detail__status {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.18);
  color: #16a34a;
}
.entry-detail__status--absent {
  background: rgba(128, 128, 128, 0.2);
  color: inherit;
  opacity: 0.75;
}
.entry-detail__status--attention {
  background: rgba(245, 158, 11, 0.2);
  color: #b45309;
}
.entry-detail__status--overridden {
  background: rgba(128, 128, 128, 0.2);
  color: inherit;
  opacity: 0.75;
}
.entry-detail__setup {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.18);
  color: #2563eb;
}
.entry-detail__desc {
  margin: 0;
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
.entry-detail__requires {
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
.entry-detail__manage {
  margin-top: 0.5rem;
  padding: 0.25rem 0.55rem;
  font-size: 0.72rem;
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
</style>
