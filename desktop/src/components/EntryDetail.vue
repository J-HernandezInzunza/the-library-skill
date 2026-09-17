<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import {
  archivedPath,
  catalogHue,
  dependencies,
  dependents,
  editableCopies,
  installStatus,
  installedCopies,
  isOnDisk,
  SESSION_TIMING,
} from "../catalog";
import { withActivity } from "../commandActivity";
import {
  describeAppError,
  type Catalog,
  type CatalogCopy,
  type Entry,
  type EntryDetail,
  type EntryRef,
  type PinResult,
  type SwitchAssessment,
  type UnpinReport,
  type UseReport,
} from "../types";
import Busy from "./Busy.vue";
import StatusBanner from "./StatusBanner.vue";
import InstalledCopies from "./InstalledCopies.vue";
import PageHeader from "./PageHeader.vue";

const props = defineProps<{
  name: string;
  /**
   * Which catalog's copy this page is about; null means whichever resolves.
   *
   * Without it `show` resolves by name and hands back the winner, so opening the
   * overridden row in the list showed the copy from the row above it.
   */
  catalog: string | null;
  /** The entry Back returns to; null means the catalog. */
  backTo: string | null;
  catalogs: Catalog[];
  entries: Entry[];
}>();
const emit = defineEmits<{
  close: [];
  open: [ref: EntryRef];
  installed: [];
  /** Hand off to the catalog manager, focused on this copy. */
  manage: [payload: { catalog: string; name: string }];
  /** Hand off to the install and setup page for this entry. */
  install: [name: string];
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
      invoke<EntryDetail>("entry_show", { name, catalog: props.catalog }),
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
 * What the install page is for, from here: adding a copy, or maintaining the one you have.
 *
 * Two sentences rather than one for every state. The states the panels themselves distinguish —
 * drifted, stale, untracked — are their subject and are stated on that page; a hand-off only has
 * to say which of the two jobs is waiting, or the page grows a second account of the same fact.
 */
const handoff = computed(() =>
  copies.value.length
    ? "Add another copy or refresh this one, and see what this skill needs before it runs."
    : "Put a copy on this machine, and see what it needs before it runs.",
);

/**
 * The same badge the list shows, from the same function.
 *
 * The page used to show none, so an entry the list had just labelled `installed · global`
 * opened with a panel headed "Install" and nothing contradicting it until eight sections
 * further down.
 */
const status = computed(() => (detail.value ? installStatus(detail.value.entry) : null));

/** The catalog id being pinned or unpinned, which is also "a write is in flight". */
const pinning = ref("");
const pinFailure = ref("");
/**
 * What the last pin meant for the copies already on disk, and what was done about it.
 *
 * Held after the write rather than derived, because it describes the machine as it was
 * *before* the reconcile — once the switch runs, nothing on the reloaded page still says
 * what was overwritten, which is the one thing the user needs told.
 */
const switched = ref<{ assessment: SwitchAssessment; installed: boolean } | null>(null);
/**
 * A pin the user has asked for and not yet confirmed.
 *
 * Pinning can replace files that are already installed, and being told that afterwards is
 * being told too late. The assessment reads the registry and the disk rather than the pin,
 * so it can be answered while both are still untouched.
 */
const proposal = ref<{ catalog: string; assessment: SwitchAssessment } | null>(null);

/** The catalog this name is pinned to, or "" when catalog order is deciding. */
const pinnedTo = computed(() => detail.value?.copies.find((copy) => copy.pinned)?.catalog ?? "");

/**
 * Whether where this comes from is a question at all.
 *
 * One catalog holding the name leaves nothing to choose between, and a chooser showing a
 * single option reads as a setting the user is failing to use. The section still renders —
 * which catalog an entry came from is worth stating — it just stops offering a choice.
 */
const choosable = computed(() => (detail.value?.copies.length ?? 0) > 1);

/**
 * Point this name at one catalog's copy, ahead of the registry order.
 *
 * Reloads through `afterWrite` rather than patching the copy in place: a pin flips
 * `wins` and both override directions on *every* copy of the name, and the list behind
 * this page renders the same facts.
 */
/**
 * Ask what pinning to `catalog` would do, before anything is written.
 *
 * Goes straight through when there is nothing installed under the name: the pin is then
 * the whole change, and a confirmation step for it would be a dialog that only ever says
 * "yes, that is what you clicked".
 */
async function pinTo(catalog: string) {
  pinning.value = catalog;
  pinFailure.value = "";
  switched.value = null;
  proposal.value = null;
  try {
    const preview = await withActivity(`checking what pinning ${props.name} would replace…`, () =>
      invoke<PinResult>("entry_pin_preview", { name: props.name, catalog }),
    );
    if (preview.switch.switchable) {
      proposal.value = { catalog, assessment: preview.switch };
    } else {
      await applyPin(catalog, false);
    }
  } catch (e) {
    pinFailure.value = describeAppError(e);
  } finally {
    pinning.value = "";
  }
}

/** Write the pin, and switch the installed copies over only when that was asked for. */
async function applyPin(catalog: string, install: boolean) {
  pinning.value = catalog;
  pinFailure.value = "";
  try {
    const result = await withActivity(`pinning ${props.name} to ${catalog}…`, () =>
      invoke<PinResult>("entry_pin", { name: props.name, catalog }),
    );
    if (install) {
      await withActivity(`installing the ${catalog} copy of ${props.name}…`, () =>
        // No `catalog`: the pin is written, so a plain install now resolves to it.
        invoke<UseReport>("entry_use", { names: [props.name], project: null, catalog: null }),
      );
    }
    proposal.value = null;
    switched.value = result.switch.switchable
      ? { assessment: result.switch, installed: install }
      : null;
    await afterWrite();
  } catch (e) {
    pinFailure.value = describeAppError(e);
  } finally {
    pinning.value = "";
  }
}

/**
 * The pin control a copy row offers, or null when it offers none.
 *
 * One control described by data rather than three near-identical buttons: they differ
 * only in their label and which way they move the pin, and repeating the attributes per
 * branch is where a disabled state drifts out of step with its siblings.
 */
function pinAction(copy: CatalogCopy): { label: string; busy: string; hint: string } | null {
  if (copy.pinned) return { label: "Clear pin", busy: "Clearing…", hint: "" };
  if (!choosable.value) return null;
  if (copy.wins) {
    return {
      label: "Pin to this",
      busy: "Pinning…",
      hint: "Already what installs. Pinning keeps it that way if the catalog order changes.",
    };
  }
  return { label: "Use this one", busy: "Checking…", hint: "" };
}

/** Each copy paired with its control, so the template reads one row, one button. */
const copyRows = computed(() =>
  (detail.value?.copies ?? []).map((copy) => ({ copy, action: pinAction(copy) })),
);

/** True while this row's own action is the one in flight. */
function pinBusy(copy: CatalogCopy): boolean {
  return copy.pinned ? !!pinning.value : pinning.value === copy.catalog;
}

/** Whichever way this row moves the pin: clearing it, or moving it here. */
function runPinAction(copy: CatalogCopy) {
  if (copy.pinned) clearPin();
  else pinTo(copy.catalog);
}

/** Hand the name back to catalog order. */
async function clearPin() {
  pinning.value = pinnedTo.value;
  pinFailure.value = "";
  switched.value = null;
  proposal.value = null;
  try {
    await withActivity(`unpinning ${props.name}…`, () =>
      invoke<UnpinReport>("entry_unpin", { name: props.name }),
    );
    await afterWrite();
  } catch (e) {
    pinFailure.value = describeAppError(e);
  } finally {
    pinning.value = "";
  }
}

/** Content is on the machine but parked out of the agent's reach. */
const switchedOff = computed(() => detail.value?.entry.state === "disabled");

/**
 * Where a switched-off copy's content is parked.
 *
 * This page is where the path lives now: the list's badge used to carry it, at a width
 * that pushed the badge onto its own line, and an elided absolute path could not be read
 * there anyway.
 */
const parked = computed(() => (detail.value ? archivedPath(detail.value.entry) : null));

// Keyed on the copy, not the name: switching from one catalog's copy to another's is a
// different page with the same title, and watching the name alone would not reload it.
watch(() => [props.name, props.catalog], () => load(props.name), { immediate: true });
</script>

<template>
  <section class="view">
    <!-- Titled from the prop, not from the payload: the header must be in place before
         the command returns, or it lands late and shifts everything under it. -->
    <PageHeader :title="name" :back="backTo ?? 'The Library'" @back="$emit('close')">
      <template #badges>
        <span v-if="detail" class="entry-detail__type">{{ detail.entry.type }}</span>
        <!-- Which catalog's copy this page is about, in the same chip the list row uses
             and the same colour, so arriving here from a row reads as the same record
             rather than as a page that happens to share its title. -->
        <span
          v-if="detail && catalogs.length > 1"
          class="entry-detail__origin-badge"
          :style="{ '--catalog-hue': hueByCatalog.get(detail.entry.catalog) ?? 220 }"
        >
          {{ detail.entry.catalog }}
        </span>
        <span
          v-if="status"
          class="entry-detail__status"
          :class="`entry-detail__status--${status.tone}`"
        >
          {{ status.status }}
        </span>
        <span v-if="detail?.has_setup" class="entry-detail__setup">guided setup available</span>
      </template>

      <Busy v-if="loading" :label="`Reading ${name}…`" />
      <StatusBanner v-else-if="error" kind="error" :detail="error" />

      <template v-else-if="detail">
        <p class="entry-detail__desc">{{ detail.entry.description }}</p>

        <!-- The page with room for the sentence the card can only fit in a tooltip. -->
        <p v-if="switchedOff" class="entry-detail__timing">
          Switched off, so nothing loads it. {{ SESSION_TIMING }}
          <span v-if="parked" class="entry-detail__parked">Parked at {{ parked }}</span>
        </p>

        <h3 class="entry-detail__section">Source</h3>
        <div class="card">
          <p class="entry-detail__origin">{{ origin }}</p>
          <p v-if="detail.source.file_path" class="entry-detail__path">
            {{ detail.source.file_path }}
          </p>
        </div>

        <!-- What you have, before what you could do: the page is most often opened about an
             entry that is already installed, and that was the fact it never stated. -->
        <InstalledCopies
          :name="detail.name"
          :copies="copies"
          :source="detail.source"
          :subject="detail.entry.catalog"
          :affected="affected.map((user) => user.entry.name)"
          @changed="afterWrite()"
        />

        <!-- A pointer, not the panels themselves (D23). Installing a copy and reading what it
             needs are actions on this entry, and they had grown into two controls sitting between
             the facts above and the facts below, so reading down the page crossed out of "what is
             true about this" and back into it. -->
        <h3 class="entry-detail__section">Install and set up</h3>
        <div class="card entry-detail__handoff">
          <p class="entry-detail__handoff-lede">{{ handoff }}</p>
          <button
            type="button"
            class="ghost"
            @click="emit('install', detail.name)"
          >
            Install and set up
          </button>
        </div>

        <h3 class="entry-detail__section">
          <template v-if="choosable">
            Where this comes from ({{ detail.copies.length }} catalogs)
          </template>
          <template v-else>Catalog holding this name</template>
        </h3>
        <!-- The one place the choice is made, because it is the one place every copy of
             the name is already on screen with what beats what. -->
        <p v-if="choosable" class="entry-detail__copies-lead">
          <template v-if="pinnedTo">
            Pinned to <strong>{{ pinnedTo }}</strong>, so its copy installs whatever the
            catalog order says.
          </template>
          <template v-else>
            Decided by catalog order. Pin one to settle it for this name alone, without
            reordering the registry for everything else.
          </template>
          Copies already on this machine keep the source they came from until you install again.
        </p>
        <StatusBanner v-if="pinFailure" kind="error" :detail="pinFailure" />

        <!-- Asked before either half changes: the assessment reads the registry and the
             disk, never the pin, so this is a question rather than a report. -->
        <div v-if="proposal" class="entry-detail__propose fade-in">
          <p class="entry-detail__propose-lede">
            Pinning to <strong>{{ proposal.catalog }}</strong> affects
            {{ proposal.assessment.stale.length }} installed
            {{ proposal.assessment.stale.length === 1 ? "copy" : "copies" }}:
          </p>
          <ul class="entry-detail__switch-list">
            <li v-for="copy in proposal.assessment.stale" :key="copy.dest">
              <code>{{ copy.dest }}</code> — {{ copy.state }}, from
              {{ copy.from || "an unrecorded source" }}
            </li>
          </ul>
          <p
            v-if="proposal.assessment.new_dependencies.length"
            class="entry-detail__switch-note"
          >
            Switching also installs
            {{ proposal.assessment.new_dependencies.join(", ") }}, which the
            {{ proposal.catalog }} copy requires.
          </p>
          <p v-if="proposal.assessment.dependents.length" class="entry-detail__switch-note">
            Still expected by
            {{ [...new Set(proposal.assessment.dependents.map((d) => d.name))].join(", ") }} —
            each resolves {{ name }} within its own catalog, so after a switch what is on
            disk is no longer the copy it names.
          </p>

          <template v-if="proposal.assessment.blockers.length">
            <p class="entry-detail__switch-note">
              These cannot be switched over without a decision:
            </p>
            <ul class="entry-detail__switch-list">
              <li v-for="why in proposal.assessment.blockers" :key="why">{{ why }}</li>
            </ul>
          </template>

          <div class="entry-detail__propose-actions">
            <button
              v-if="proposal.assessment.simple"
              type="button"
              :disabled="!!pinning"
              @click="applyPin(proposal.catalog, true)"
            >
              {{ pinning ? "Switching…" : "Pin and switch the files over" }}
            </button>
            <button
              type="button"
              class="ghost"
              :disabled="!!pinning"
              @click="applyPin(proposal.catalog, false)"
            >
              Pin only, leave the files
            </button>
            <button type="button" class="ghost" :disabled="!!pinning" @click="proposal = null">
              Cancel
            </button>
          </div>
        </div>

        <!-- What the pin meant for what was already installed. Shown after the write
             because it describes the machine as it was *before* the reconcile, which
             nothing on the reloaded page still records. -->
        <StatusBanner
          v-if="switched"
          :kind="switched.installed ? 'success' : 'warning'"
        >
          <template v-if="switched.installed">
            <p class="entry-detail__switch-lede">
              Switched {{ switched.assessment.stale.length }}
              {{ switched.assessment.stale.length === 1 ? "copy" : "copies" }} over to
              {{ pinnedTo }}, overwriting what was installed.
            </p>
            <ul class="entry-detail__switch-list">
              <li v-for="copy in switched.assessment.stale" :key="copy.dest">
                <code>{{ copy.dest }}</code> — was {{ copy.from || "from an unrecorded source" }}
              </li>
            </ul>
            <p
              v-if="switched.assessment.new_dependencies.length"
              class="entry-detail__switch-note"
            >
              Also installed {{ switched.assessment.new_dependencies.join(", ") }}, which the
              {{ pinnedTo }} copy requires.
            </p>
          </template>
          <template v-else>
            <p class="entry-detail__switch-lede">
              The pin is saved, but the
              {{ switched.assessment.stale.length === 1 ? "copy" : "copies" }} already
              installed {{ switched.assessment.stale.length === 1 ? "was" : "were" }} left
              alone:
            </p>
            <ul class="entry-detail__switch-list">
              <li v-for="why in switched.assessment.blockers" :key="why">{{ why }}</li>
            </ul>
            <p class="entry-detail__switch-note">
              Nothing was overwritten. Settle those, then install from this page to switch over.
            </p>
          </template>
          <!-- Named in both outcomes: these resolve the name inside their own catalog, so
               after a switch the files they get are no longer the ones they name. -->
          <p
            v-if="switched.assessment.dependents.length"
            class="entry-detail__switch-note"
          >
            Still expected by
            {{ [...new Set(switched.assessment.dependents.map((d) => d.name))].join(", ") }} —
            each resolves {{ name }} within its own catalog, so what is on disk is no longer
            the copy it names.
          </p>
        </StatusBanner>
        <ul class="entry-detail__copies">
          <li
            v-for="{ copy, action } in copyRows"
            :key="copy.catalog"
            class="entry-detail__copy"
            :class="{ 'entry-detail__copy--wins': copy.wins }"
            :style="{ '--catalog-hue': hueByCatalog.get(copy.catalog) ?? 220 }"
          >
            <div class="entry-detail__copy-head">
              <span class="entry-detail__origin-chip">{{ copy.catalog }}</span>
              <!-- Pinned and winning-by-order are separated on purpose: they look the
                   same from the outside and are undone by different things. -->
              <span v-if="copy.pinned" class="entry-detail__pinned">
                pinned — this is what installs
              </span>
              <span v-else-if="copy.wins" class="entry-detail__wins">
                first by catalog order — this is what installs
              </span>
              <span v-else class="entry-detail__loses">
                overridden by {{ copy.overridden_by.join(", ") }}
              </span>

              <!-- The card's own verb, top-right: it acts on this copy, while the
                   hand-off below leaves the page. Every copy has one, the winner
                   included — pinning that changes nothing today and everything the day
                   the registry is reordered, which a chooser offering only the losers
                   could not express without pinning the wrong copy first. -->
              <button
                v-if="action"
                type="button"
                class="ghost entry-detail__copy-action entry-detail__choose"
                :disabled="!!pinning"
                :title="action.hint || undefined"
                @click="runPinAction(copy)"
              >
                {{ pinBusy(copy) ? action.busy : action.label }}
              </button>
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
              class="ghost entry-detail__copy-action entry-detail__manage"
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
              <button type="button" class="entry-detail__dep" @click="$emit('open', { name: dep.entry.name, catalog: dep.entry.catalog })">
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
              <button type="button" class="entry-detail__dep" @click="$emit('open', { name: dep.entry.name, catalog: dep.entry.catalog })">
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
              <button type="button" class="entry-detail__dep" @click="$emit('open', { name: user.entry.name, catalog: user.entry.catalog })">
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
    </PageHeader>
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
  background: var(--status-ok-tint);
  color: var(--status-ok-ink);
}
.entry-detail__status--absent {
  background: var(--surface-sunken);
  color: inherit;
  opacity: 0.75;
}
.entry-detail__status--disabled {
  /* Matches the list's badge: violet reads as neither the green of a loading skill nor
     the grey of one that was never installed. Without this the base rule's green would
     put "disabled" in the colour of a skill that is loading. */
  background: var(--status-disabled-tint);
  color: var(--status-disabled-ink);
}
.entry-detail__status--attention {
  background: var(--status-attention-tint);
  color: var(--status-attention-ink);
}
.entry-detail__status--overridden {
  background: var(--surface-sunken);
  color: inherit;
  opacity: 0.75;
}
.entry-detail__setup {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--accent-tint);
  color: var(--accent-ink);
}
.entry-detail__handoff {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.entry-detail__handoff-lede {
  flex: 1;
  min-width: 14rem;
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.8;
}
.entry-detail__desc {
  margin: 0;
  line-height: 1.5;
  opacity: 0.85;
}
.entry-detail__timing {
  margin: 0.6rem 0 0;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border-left: 3px solid var(--status-disabled-edge);
  background: var(--status-disabled-tint);
  font-size: 0.82rem;
  line-height: 1.5;
}
.entry-detail__parked {
  /* Its own line inside the note, because an absolute path wrapped mid-sentence reads as
     part of the sentence. */
  display: block;
  margin-top: 0.4rem;
  font-size: 0.78rem;
  font-family: ui-monospace, SFMono-Regular, monospace;
  overflow-wrap: anywhere;
  opacity: 0.7;
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
  border-left: 3px solid var(--catalog-edge);
  background: var(--surface-raised);
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
  background: var(--catalog-fill);
  color: var(--text-on-accent);
  font-size: 0.7rem;
  font-weight: 600;
}
.entry-detail__origin-chip--muted {
  background: var(--surface-strong);
  color: inherit;
}
.entry-detail__wins {
  font-size: 0.72rem;
  color: var(--status-ok-ink);
  font-weight: 600;
}
/* Tinted rather than just coloured, so a pin reads as something someone chose and a
   precedence win reads as the default it is. */
.entry-detail__origin-badge {
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--catalog-fill);
  color: var(--text-on-accent);
  font-size: 0.7rem;
  font-weight: 600;
}
.entry-detail__pinned {
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--status-ok-ink);
  background: var(--status-ok-tint);
}
.entry-detail__propose {
  margin: 0 0 0.9rem;
  padding: 0.75rem 0.9rem;
  border-radius: 8px;
  border-left: 3px solid var(--status-attention-ink);
  background: var(--status-attention-tint);
}
.entry-detail__propose-lede {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.45;
}
.entry-detail__propose-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.entry-detail__switch-lede {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.45;
}
.entry-detail__switch-list {
  margin: 0.4rem 0 0;
  padding-left: 1.1rem;
  font-size: 0.78rem;
  line-height: 1.5;
}
.entry-detail__switch-list code {
  font-size: 0.74rem;
  overflow-wrap: anywhere;
}
.entry-detail__switch-note {
  margin: 0.45rem 0 0;
  font-size: 0.78rem;
  line-height: 1.45;
  opacity: 0.85;
}
.entry-detail__copies-lead {
  margin: -0.4rem 0 0.7rem;
  font-size: 0.78rem;
  line-height: 1.5;
  opacity: 0.7;
}
/* One size for every action on a copy card, so the pin control and the hand-off under
   it do not read as two different kinds of button. */
.entry-detail__copy-action {
  padding: 0.25rem 0.55rem;
  font-size: 0.72rem;
}
/* Hard right of the head row whatever the label's width, and whatever sits to its left. */
.entry-detail__choose {
  margin-left: auto;
}
.entry-detail__loses,
.entry-detail__chain {
  margin: 0 0 0.3rem;
  font-size: 0.72rem;
  opacity: 0.7;
}
.entry-detail__requires li {
  border-radius: 8px;
  background: var(--surface-raised);
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
  background: var(--surface-hover);
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
  background: var(--status-ok-tint);
  color: var(--status-ok-ink);
}
.entry-detail__dep-state--missing {
  background: var(--surface-sunken);
  color: inherit;
  opacity: 0.7;
}
.entry-detail__manage {
  margin-top: 0.5rem;
}
.entry-detail__indirect {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.5;
}
.entry-detail__section--broken {
  color: var(--status-danger-ink);
  opacity: 0.85;
}
.entry-detail__broken {
  padding: 0.5rem 0.85rem;
  border-left: 3px solid var(--status-danger-ink);
}
.entry-detail__broken-why {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  color: var(--status-danger-ink);
}
.entry-detail__req-desc {
  margin: 0.25rem 0 0;
  font-size: 0.78rem;
  opacity: 0.7;
}
</style>
