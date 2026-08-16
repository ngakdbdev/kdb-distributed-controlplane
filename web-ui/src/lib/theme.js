// theme.js - light/dark/system mode for the portal. A single localStorage
// key is the source of truth; index.html applies it inline (before React/
// CSS paint, see that file's inline <script>) to avoid a flash of the
// wrong theme, and this module is what the in-app ThemeToggle reads/
// writes so both stay in sync without duplicating the storage key.
//
// Two related but distinct ideas:
//   MODE  - what the user picked: "light" | "dark" | "system". Stored as-is.
//   THEME - what's actually painted: always "light" or "dark". When mode
//           is "system", theme tracks the OS/browser preference live (see
//           watchSystemTheme) - not just resolved once at load.
const KEY = "vantik_theme_v1";
const MODES = ["light", "dark", "system"];

export function getStoredMode() {
  const v = localStorage.getItem(KEY);
  return MODES.includes(v) ? v : "system";
}

export function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function themeForMode(mode) {
  return mode === "system" ? systemTheme() : mode;
}

// Paints a mode without persisting it - used by watchSystemTheme to react
// live to an OS change while mode="system", where there's nothing new to
// save (the stored mode is still "system").
export function applyMode(mode) {
  document.documentElement.setAttribute("data-theme", themeForMode(mode));
  document.documentElement.setAttribute("data-theme-mode", mode);
}

export function setMode(mode) {
  localStorage.setItem(KEY, mode);
  applyMode(mode);
}

export function currentMode() {
  return document.documentElement.getAttribute("data-theme-mode") || getStoredMode();
}

// Call once (e.g. on ThemeToggle mount); returns an unsubscribe function.
// Only repaints when the CURRENT mode is "system" at the time the OS
// preference changes - if the user has explicitly picked light/dark this
// is a no-op, exactly like index.html's own bootstrap script treats an
// explicit stored choice as final.
export function watchSystemTheme() {
  if (!window.matchMedia) return () => {};
  const mq = window.matchMedia("(prefers-color-scheme: light)");
  const handler = () => {
    if (getStoredMode() === "system") applyMode("system");
  };
  if (mq.addEventListener) mq.addEventListener("change", handler);
  else mq.addListener(handler); // Safari <14
  return () => {
    if (mq.removeEventListener) mq.removeEventListener("change", handler);
    else mq.removeListener(handler);
  };
}
