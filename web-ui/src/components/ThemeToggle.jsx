import { useEffect, useState } from "react";
import { currentMode, setMode, watchSystemTheme } from "../lib/theme.js";

const MODES = [
  { id: "light", label: "Light", icon: "☀" },
  { id: "dark", label: "Dark", icon: "☾" },
  { id: "system", label: "Auto", icon: "◐" }, // follows the OS/browser preference, live
];

// Sits in the sidebar next to logout (see Nav.jsx). The sidebar itself is
// theme-aware now too (--side-* tokens flip with data-theme, see
// styles.css's light-theme block) so this reads correctly against either.
export default function ThemeToggle() {
  const [mode, setModeState] = useState(currentMode);

  useEffect(() => watchSystemTheme(), []);

  function choose(next) {
    setMode(next);
    setModeState(next);
  }

  return (
    <div className="theme-modes" role="radiogroup" aria-label="Color theme">
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          role="radio"
          aria-checked={mode === m.id}
          className={`theme-mode-btn ${mode === m.id ? "active" : ""}`}
          onClick={() => choose(m.id)}
          title={m.id === "system" ? "Match your OS/browser setting" : `${m.label} mode`}
        >
          <span aria-hidden="true">{m.icon}</span>
          <span className="sidenav-item-label">{m.label}</span>
        </button>
      ))}
    </div>
  );
}
