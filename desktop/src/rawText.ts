/**
 * Attributes for an input whose value is machine-readable, not prose.
 *
 * macOS capitalises the first letter of a text field by default, and an entry name, a
 * branch, a repo URL, and a file path are all case-significant — `Grilling` is a
 * different entry from `grilling` to the CLI, which matches names exactly. `autocorrect`
 * and `spellcheck` go with it: a red squiggle under every skill name is noise, and
 * substitution can rewrite a path.
 *
 * Bound with `v-bind` rather than repeated inline so a new field cannot quietly opt out
 * of it by being written without them.
 */
export const RAW_TEXT = {
  autocapitalize: "off",
  autocorrect: "off",
  spellcheck: "false",
} as const;
