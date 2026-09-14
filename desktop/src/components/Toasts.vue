<script setup lang="ts">
import { activeToasts, dismiss, hold, release } from "../toasts";
</script>

<template>
  <!-- A grid item in the view's row rather than a `position: fixed` block, so it is pinned
       to the bottom of the view by the layout instead of by a number that has to be kept
       equal to the command bar's height. `pointer-events: none` on the stack and `auto` on
       each notice keeps the list underneath clickable in the gaps between them. -->
  <div class="toasts" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="toast in activeToasts"
        :key="toast.id"
        class="toasts__item"
        :class="`toasts__item--${toast.kind}`"
        :role="toast.kind === 'error' ? 'alert' : 'status'"
        @mouseenter="hold(toast.id)"
        @mouseleave="release(toast.id)"
      >
        <div class="toasts__body">
          <p class="toasts__message">{{ toast.message }}</p>
          <pre v-if="toast.detail" class="toasts__detail">{{ toast.detail }}</pre>
        </div>

        <button
          type="button"
          class="toasts__close"
          :aria-label="`Dismiss: ${toast.message}`"
          @click="dismiss(toast.id)"
        >
          <!-- Drawn rather than a glyph: × sits off-centre in most UI faces, and the
               button is small enough that it shows. -->
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  /* Row 1 is the view, so this is pinned to the bottom of the view by the layout rather
     than by a number kept equal to the command bar's height.

     `absolute` is load-bearing, not decoration: as an in-flow grid item the stack's own
     width fed the implicit column's sizing and squeezed the whole view to 494px. Out of
     flow it contributes nothing, and an absolutely-positioned grid child with a definite
     row is positioned against its *grid area*, so it still lands on the view's box. */
  position: absolute;
  grid-row: 1;
  align-self: end;
  justify-self: end;
  z-index: 25;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  /* Wide enough for a sentence, short of the column the list is read in. */
  width: min(30rem, calc(100vw - 2.5rem));
  margin: 0 1.25rem 1rem;
  pointer-events: none;
}
.toasts__item {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  padding: 0.7rem 0.8rem;
  border: 1px solid transparent;
  border-radius: 8px;
  /* Opaque, unlike the inline banner this replaces: it has the list behind it now, and a
     tinted-transparent panel over text is unreadable. */
  background: var(--surface-page);
  box-shadow: 0 6px 20px rgb(0 0 0 / 18%);
  font-size: 0.85rem;
  line-height: 1.45;
  pointer-events: auto;
}
.toasts__item--success {
  border-color: var(--status-ok-edge);
}
.toasts__item--error {
  border-color: var(--status-danger-edge);
  color: var(--status-danger-ink);
}
.toasts__body {
  flex: 1;
  min-width: 0;
}
.toasts__message {
  margin: 0;
}
.toasts__detail {
  margin: 0.4rem 0 0;
  max-height: 7rem;
  overflow: auto;
  font-size: 0.78rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  opacity: 0.8;
}
.toasts__close {
  flex: none;
  width: 1.4rem;
  height: 1.4rem;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: inherit;
  opacity: 0.55;
}
.toasts__close:hover {
  background: var(--surface-hover);
  opacity: 1;
}
.toasts__close svg {
  width: 0.85rem;
  height: 0.85rem;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
}

/* In from the right, out by collapsing its own height so the notices below it slide up
   rather than jumping. `position: absolute` during leave takes the departing notice out of
   the flow, which is what lets the rest move smoothly instead of snapping. */
.toast-enter-active,
.toast-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(0.75rem);
}
.toast-leave-active {
  position: absolute;
  right: 0;
  left: 0;
}
.toast-move {
  transition: transform 0.18s ease;
}

@media (prefers-reduced-motion: reduce) {
  .toast-enter-active,
  .toast-leave-active,
  .toast-move {
    transition: none;
  }
}
</style>
