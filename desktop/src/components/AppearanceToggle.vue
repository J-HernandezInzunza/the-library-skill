<script setup lang="ts">
import { computed, ref } from "vue";
import { applyAppearance, nextAppearance, storedAppearance, type Appearance } from "../theme";

const LABELS: Record<Appearance, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

const appearance = ref(storedAppearance());

const label = computed(() => {
  const next = nextAppearance(appearance.value);
  return `Appearance: ${LABELS[appearance.value]}. Switch to ${LABELS[next]}.`;
});

async function cycle() {
  const next = nextAppearance(appearance.value);
  await applyAppearance(next);
  // After, so a rejected call leaves the icon showing what the window actually is.
  appearance.value = next;
}
</script>

<template>
  <button
    type="button"
    class="ghost appearance-toggle"
    :aria-label="label"
    :title="label"
    @click="cycle"
  >
    <!-- The icon shows the current appearance, like the menu-bar extras on macOS. -->
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <template v-if="appearance === 'system'">
        <rect x="2" y="3" width="12" height="8" rx="1.25" />
        <path d="M6 14h4M8 11v3" />
      </template>
      <template v-else-if="appearance === 'light'">
        <circle cx="8" cy="8" r="2.75" />
        <path
          d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"
        />
      </template>
      <path v-else d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" />
    </svg>
  </button>
</template>

<style scoped>
.appearance-toggle {
  display: flex;
  padding: 0.35rem;
}
.appearance-toggle svg {
  width: 1rem;
  height: 1rem;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
