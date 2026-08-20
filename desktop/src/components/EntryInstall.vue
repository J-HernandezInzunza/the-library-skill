<script setup lang="ts">
/**
 * Installing an entry, and what it needs before it runs — one page, one subject.
 *
 * Split out of the detail page (D23), which had grown into a page about an entry *and* a page for
 * acting on it: the install panel and the readiness card sat between "what you have on this
 * machine" and "which catalogs hold this name", so reading down the page crossed from facts into
 * controls and back out again. Install and setup are the same job — you install a copy, then find
 * out what it wants — and they are the only two things here.
 */
import InstallPreview from "./InstallPreview.vue";
import PageHeader from "./PageHeader.vue";
import SetupReadiness from "./SetupReadiness.vue";

defineProps<{
  name: string;
  /**
   * A copy is on this machine, which changes what both panels are about: adding versus
   * refreshing, and whether the setup manifest can be read at all.
   *
   * Passed in rather than read here, from the catalog the app already holds, so it stays true
   * after an install on this very page — a snapshot taken on the way in would leave the readiness
   * card hidden until you navigated out and back.
   */
  installed: boolean;
  /** The title of the page Back returns to. */
  backTo: string;
}>();
defineEmits<{ close: []; installed: []; walkthrough: [] }>();
</script>

<template>
  <section class="view">
    <PageHeader title="Install and set up" :back="backTo" @back="$emit('close')">
      <!-- The entry is the subject, so it is a badge on the title rather than part of it: the
           title stays the same length on every entry, and so does the label of every back
           button pointing here. -->
      <template #badges>
        <span class="entry-install__name">{{ name }}</span>
      </template>

      <InstallPreview :name="name" :installed="installed" @installed="$emit('installed')" />

      <!-- Below the install panel, not above it: it reports on the copy that panel puts there,
           and on a machine with nothing installed there is nothing for it to read yet. -->
      <SetupReadiness :name="name" :installed="installed" @walkthrough="$emit('walkthrough')" />
    </PageHeader>
  </section>
</template>

<style scoped>
.entry-install__name {
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: rgba(128, 128, 128, 0.14);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.8rem;
}
</style>
