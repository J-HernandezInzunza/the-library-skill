<script setup lang="ts">
/**
 * The one header every full-screen view uses.
 *
 * Two rows, deliberately. Navigation gets a row to itself so the back button is always in
 * the same place regardless of how long the title is or how many actions the view has —
 * five views had each written their own and the control visibly jumped as you navigated.
 * The title and that page's actions share the second row, actions pushed right, so "where
 * am I" and "what can I do here" read as one line rather than competing with the way out.
 */
defineProps<{
  title: string;
  /**
   * The **title of the page Back returns to**, rendered as-is. Omit where there is no back.
   *
   * A rule, not a caption: the labels had drifted to "Back to catalog", "All catalogs", and
   * a bare catalog id, and "Back to catalog" (the entry list) against "Back to Catalogs"
   * (the registry) differed by one letter. Naming the destination's own title makes the
   * label derivable rather than written, so two screens cannot describe each other
   * differently.
   */
  back?: string;
}>();
defineEmits<{ back: [] }>();
</script>

<template>
  <header class="page-head">
    <div v-if="back" class="page-head__nav">
      <button type="button" class="ghost" @click="$emit('back')">← {{ back }}</button>
    </div>

    <div class="page-head__main">
      <h2 class="page-head__title">{{ title }}</h2>
      <!-- Badges that describe the title, so they sit beside it. -->
      <slot />
      <!-- What this page can do, always right-aligned. -->
      <div class="page-head__actions"><slot name="actions" /></div>
    </div>
  </header>
</template>

<style scoped>
.page-head {
  margin-bottom: 1.25rem;
}
.page-head__nav {
  /* Its own row: the back button's position must not depend on the title's length. */
  margin-bottom: 0.9rem;
}
.page-head__main {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.page-head__title {
  margin: 0;
  font-size: 1.15rem;
}
.page-head__actions {
  /* Pushed right even when the title is short, so actions are found in one place. */
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
</style>
