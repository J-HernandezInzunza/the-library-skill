<script setup lang="ts">
/**
 * The one way this app reports how a command turned out.
 *
 * Success and failure of the same action must appear in the same place, or the user
 * learns two habits for one question. The add form shipped with its confirmation above
 * the form and its error below it, so a refused add looked like nothing happening at all
 * — the failure was a screen's worth of scrolling away from where the success appears.
 *
 * Placement is the caller's job, and the rule is: the top of the surface that owns the
 * command. A full view puts it under its header; a panel puts it at the top of the panel.
 * Never under the control that was clicked.
 */
defineProps<{
  kind: "success" | "error";
  /**
   * Preformatted text, for a CLI failure. Rendered verbatim in a `<pre>` because stderr
   * is surfaced as-is (R1.4) and its line breaks carry meaning.
   */
  detail?: string;
}>();
</script>

<template>
  <div
    class="status-banner fade-in"
    :class="`status-banner--${kind}`"
    :role="kind === 'error' ? 'alert' : 'status'"
    aria-live="polite"
  >
    <slot />
    <pre v-if="detail" class="status-banner__detail">{{ detail }}</pre>
  </div>
</template>

<style scoped>
.status-banner {
  /* No max-width: it sits above full-width cards, and a narrower banner reads as a
     misaligned box rather than as a measure. `.app` already caps the line length. */
  margin: 0 0 1.25rem;
  padding: 0.9rem 1rem;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 0.9rem;
  line-height: 1.45;
}
.status-banner--success {
  border-color: rgba(34, 197, 94, 0.35);
  background: rgba(34, 197, 94, 0.1);
}
.status-banner--error {
  border-color: rgba(220, 38, 38, 0.3);
  background: rgba(220, 38, 38, 0.08);
  color: #b91c1c;
}
@media (prefers-color-scheme: dark) {
  .status-banner--error {
    color: #fca5a5;
  }
}
.status-banner__detail {
  margin: 0;
  font-size: 0.8rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
/* Only when it follows content, so a detail-only banner keeps its own padding. */
.status-banner__detail:not(:first-child) {
  margin-top: 0.6rem;
}
</style>
