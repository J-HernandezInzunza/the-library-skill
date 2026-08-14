<script setup lang="ts">
/**
 * The one header every full-screen view uses.
 *
 * Five views had each grown their own: two put the back button above the title and three
 * beside it, with three different margins, so the button visibly jumped as you navigated
 * between them. Consistency here is not styling — a control that moves between screens is
 * one you have to re-find every time.
 */
defineProps<{
  title: string;
  /** Where Back returns to, in words. Omit for a view with nowhere to go back to. */
  back?: string;
}>();
defineEmits<{ back: [] }>();
</script>

<template>
  <header class="page-head">
    <button v-if="back" type="button" class="ghost page-head__back" @click="$emit('back')">
      ← {{ back }}
    </button>
    <h2 class="page-head__title">{{ title }}</h2>
    <!-- Badges and status chips that belong to the title, not to the page. -->
    <slot />
  </header>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 1.25rem;
}
.page-head__back {
  /* Fixed against the title's baseline rather than the text length, so the button lands
     in the same place on every view regardless of how long its label is. */
  flex: none;
}
.page-head__title {
  margin: 0;
  font-size: 1.15rem;
}
</style>
