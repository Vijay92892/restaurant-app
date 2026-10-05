import { start } from "./app.ts";
import { startReset } from "./reset.ts";
import "./styles.css";

const root = document.getElementById("app");
if (root) {
  // Staff password reset links land here too (same origin as the API).
  if (location.pathname === "/reset-password") startReset(root);
  else void start(root);
}
