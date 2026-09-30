<script setup lang="ts">
import { useCommandActivity } from "../commandActivity";

// Driven by the command events rather than by any view's own flag, so it covers every
// command the backend runs — including ones added later, which is the point.
const { busy } = useCommandActivity();
</script>

<template>
  <Transition name="activity">
    <!-- The label naming the command lives in the command log's bar, not floating here over
         whatever the view has in its top corner. -->
    <div v-if="busy" class="activity">
      <div class="activity__track"><div class="activity__bar" /></div>
    </div>
  </Transition>
</template>

<style scoped>
.activity {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 30;
  pointer-events: none;
}
.activity__track {
  height: 2px;
  overflow: hidden;
  background: var(--accent-tint);
}
.activity__bar {
  width: 40%;
  height: 100%;
  background: var(--accent-bright);
  animation: activity-slide 1.1s ease-in-out infinite;
}

@keyframes activity-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(250%);
  }
}

.activity-enter-active,
.activity-leave-active {
  transition: opacity 0.2s ease;
}
.activity-enter-from,
.activity-leave-to {
  opacity: 0;
}

/* An indeterminate bar that never stops is the worst case for motion sensitivity, so
   it becomes a static fill rather than disappearing: the signal is still needed. */
@media (prefers-reduced-motion: reduce) {
  .activity__bar {
    width: 100%;
    animation: none;
    opacity: 0.5;
  }
}
</style>
