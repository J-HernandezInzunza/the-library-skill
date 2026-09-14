import { createApp } from "vue";
// Before the app, so `:root` is populated before any component's scoped styles are
// evaluated against it. Side-effect import: the file declares custom properties and
// exports nothing.
import "./assets/tokens.css";
import App from "./App.vue";

createApp(App).mount("#app");
