// Shared symbol basket between Markets, Orders, and Portfolio, persisted to
// localStorage so it survives navigation between pages (each page mounts
// fresh - see App.jsx) and page reloads. Markets and Orders both add to it
// (SymbolPicker, "Your positions" chips, a deep-link symbol); Portfolio
// reads it to show a watchlist row for anything you're tracking but don't
// hold yet, so a symbol added on Markets or Orders shows up on Portfolio
// without any extra step.
const KEY = "vantik_watchlist_v1";
const MAX = 12;

export function loadWatchlist() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (Array.isArray(saved) && saved.length) return saved;
  } catch { /* corrupt/old value - treat as empty */ }
  return [];
}

export function saveWatchlist(symbols) {
  const deduped = [...new Set((symbols || []).filter(Boolean).map((s) => s.toUpperCase()))].slice(0, MAX);
  localStorage.setItem(KEY, JSON.stringify(deduped));
  return deduped;
}
