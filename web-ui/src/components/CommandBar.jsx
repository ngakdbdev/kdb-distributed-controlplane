import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Global search - instruments (real GET /symbols/search, same source
// SymbolPicker uses) and pages (Nav.jsx's own role-filtered page index,
// see allPagesForRole) in one box, since a trader and an operator are
// both typically looking for "the thing," not two separate searches.
// Deliberately doesn't search orders/commands yet (no backend endpoint
// to search across a user's own orders by free text) - instruments and
// pages are the two real, working result types today.
const DEBOUNCE_MS = 150;

export default function CommandBar({ pages, onNavigate }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [symbols, setSymbols] = useState([]);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!q.trim()) { setSymbols([]); return; }
    let closed = false;
    const id = setTimeout(() => {
      api.searchSymbols(q.trim()).then((r) => {
        if (!closed) setSymbols((r.symbols || []).slice(0, 6));
      }).catch(() => { if (!closed) setSymbols([]); });
    }, DEBOUNCE_MS);
    return () => { closed = true; clearTimeout(id); };
  }, [q]);

  useEffect(() => {
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const needle = q.trim().toLowerCase();
  const pageMatches = needle ? pages.filter((p) => p.label.toLowerCase().includes(needle)).slice(0, 6) : [];
  const hasResults = symbols.length > 0 || pageMatches.length > 0;

  function go(id, params) {
    onNavigate(id, params);
    setQ("");
    setOpen(false);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") { setOpen(false); e.currentTarget.blur(); }
    else if (e.key === "Enter") {
      if (symbols[0]) go("markets", { symbol: symbols[0].symbol });
      else if (pageMatches[0]) go(pageMatches[0].id);
    }
  }

  return (
    <div className="cmd-bar" ref={boxRef}>
      <span className="cmd-bar-icon" aria-hidden="true">⌕</span>
      <input
        className="cmd-bar-input"
        placeholder="Search instruments, pages, commands…"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="Global search"
      />
      {open && needle && (
        <div className="cmd-bar-results">
          {!hasResults && <div className="cmd-bar-empty">No matches for "{q.trim()}".</div>}
          {symbols.length > 0 && (
            <div className="cmd-bar-group">
              <div className="cmd-bar-group-label">Instruments</div>
              {symbols.map((s) => (
                <button key={s.symbol} className="cmd-bar-result" onClick={() => go("markets", { symbol: s.symbol })}>
                  <span className="cmd-bar-result-main">
                    <strong>{s.symbol}</strong>
                    <span className="muted">{s.name}</span>
                  </span>
                  <span className={`live-pill sm ${s.source === "live" ? "on" : "off"}`}>{s.source === "live" ? "live" : "seed"}</span>
                </button>
              ))}
            </div>
          )}
          {pageMatches.length > 0 && (
            <div className="cmd-bar-group">
              <div className="cmd-bar-group-label">Go to</div>
              {pageMatches.map((p) => (
                <button key={p.id} className="cmd-bar-result" onClick={() => go(p.id)}>
                  <span className="cmd-bar-result-main">{p.label}</span>
                  <span className="muted">{p.group}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
