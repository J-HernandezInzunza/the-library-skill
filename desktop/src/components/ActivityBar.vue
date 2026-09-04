<script setup lang="ts">
import { useCommandActivity } from "../commandActivity";

// Driven by the command events rather than by any view's own flag, so it covers every
// command the backend runs — including ones added later, which is the point.
const { busy, label } = useCommandActivity();
</script>

<template>
  <Transition name="activity">
    <div v-if="busy" class="activity" role="status" aria-live="polite">
      <div class="activity__track"><div class="activity__bar" /></div>
      <span class="activity__label">{{ label }}</span>
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
  background: rgba(59, 130, 246, 0.18);
}
.activity__bar {
  width: 40%;
  height: 100%;
  background: #3b82f6;
  animation: activity-slide 1.1s ease-in-out infinite;
}
.activity__label {
  position: absolute;
  top: 0.4rem;
  right: 0.75rem;
  /* Bounded to one line, always. This is an absolutely-positioned element with no layout
     parent to constrain it, so a long label does not wrap into a corner — it paints across
     the whole window, over the view, at the opacity of a watermark. */
  max-width: 40vw;
  overflow: hidden;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--app-bg-sticky);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 0.68rem;
  white-space: nowrap;
  text-overflow: ellipsis;
  opacity: 0.6;
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
