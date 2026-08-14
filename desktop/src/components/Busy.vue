<script setup lang="ts">
withDefaults(defineProps<{ label?: string; inline?: boolean }>(), {
  label: "Working…",
  inline: false,
});
</script>

<template>
  <p class="busy" :class="{ 'busy--inline': inline }" role="status" aria-live="polite">
    <span class="busy__spinner" />
    <span class="busy__label">{{ label }}</span>
  </p>
</template>

<style scoped>
.busy {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 2rem 0;
  font-size: 0.85rem;
  opacity: 0.7;
}
.busy--inline {
  justify-content: flex-start;
  padding: 0.75rem 0 0;
}
.busy__spinner {
  width: 0.85rem;
  height: 0.85rem;
  border-radius: 50%;
  border: 2px solid rgba(128, 128, 128, 0.3);
  border-top-color: #3b82f6;
  animation: busy-spin 0.7s linear infinite;
}

@keyframes busy-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .busy__spinner {
    animation: none;
    border-top-color: rgba(128, 128, 128, 0.3);
    /* Without the spin the ring says nothing, so the dot carries the state instead. */
    background: #3b82f6;
  }
}
</style>
