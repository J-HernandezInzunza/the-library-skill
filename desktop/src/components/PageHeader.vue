<script setup lang="ts">
import { useTemplateRef } from "vue";

/**
 * The one header every full-screen view uses, and the scrolling body under it.
 *
 * It renders **both chrome rows and the body between them** because D22 put the two rows on
 * opposite sides of the scroll boundary: the back row is the view's head row and never moves,
 * while the title is the page's first line and scrolls with it. A component that rendered only
 * one of them would leave every view repeating the other half plus the body wrapper — the
 * duplication five views had already drifted apart on once (D19).
 *
 * So a view is `<section class="view"><PageHeader …>page content</PageHeader></section>`, plus a
 * `.view__foot` sibling where it has bottom chrome of its own.
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

const body = useTemplateRef<HTMLElement>("body");

/**
 * The scrolling element, for the two views that have to move it themselves: the walkthrough
 * following its transcript, and the add form bringing its outcome banner into view.
 *
 * Exposed rather than found with a selector, so renaming the class cannot silently break them.
 */
defineExpose({ body });
</script>

<template>
  <!-- The head row: chrome, outside the one thing that scrolls, so the way out of a page is on
       screen at every scroll position in it (D22). Its own row, so the back button's position
       does not depend on the title's length or on how many actions the view has. -->
  <header v-if="back" class="page-head view__head column">
    <button type="button" class="ghost" @click="$emit('back')">← {{ back }}</button>
  </header>

  <div ref="body" class="view__body column">
    <div class="page-title">
      <h2 class="page-title__heading">{{ title }}</h2>
      <!-- Badges that describe the title, so they sit beside it. -->
      <slot name="badges" />
      <!-- What this page can do, always right-aligned. -->
      <div class="page-title__actions"><slot name="actions" /></div>
    </div>

    <slot />
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin-bottom: 1.25rem;
}
.page-title__heading {
  margin: 0;
  font-size: 1.15rem;
}
.page-title__actions {
  /* Pushed right even when the title is short, so actions are found in one place. */
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
</style>
