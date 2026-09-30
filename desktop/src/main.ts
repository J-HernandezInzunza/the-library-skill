import { createApp } from "vue";
// Before the app, so `:root` is populated before any component's scoped styles are
// evaluated against it. Side-effect import: the file declares custom properties and
// exports nothing.
import "./assets/tokens.css";
import App from "./App.vue";
import { applyAppearance, storedAppearance } from "./theme";

// Here rather than in the toggle, which lives on one view and may not be the first shown.
void applyAppearance(storedAppearance());

createApp(App).mount("#app");
