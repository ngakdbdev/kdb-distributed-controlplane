import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const BROWSE_REFRESH_MS = 15000;

// Type-ahead symbol picker. `value` is an array of symbol strings; `onChange`
// gets the new array. Users can type to search the reference (or a market) and
// click results, or type a symbol and press Enter to add it directly.
//
// With nothing typed yet, this still shows a starting menu rather than an
// empty dropdown - control-api's /symbols/search returns the FULL reference
// (static seed + whatever symbol_discovery.py's poll loop has actually seen
// trading, tagged source="live") and ranks live ones first for an empty/
// non-discriminating query (see app/symbols.py's search()), so "browse with
// nothing typed" surfaces symbols with a REAL price behind them - not a
// seed entry from an exchange no feed here covers, which would look
// identical in the list but never produce a fillable order. Kept fresh
// while open (symbol_discovery polls every 60s server-side) rather than a
// one-shot fetch, so a symbol that starts trading mid-session appears
// without closing and reopening the picker.
export default function SymbolPicker({ value = [], onChange, placeholder = "search symbols…" }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    function pull() {
      api.searchSymbols(q).then((d) => { if (live) setResults(d.symbols || []); }).catch(() => {});
    }
    pull();
    const id = setInterval(pull, BROWSE_REFRESH_MS);
    return () => { live = false; clearInterval(id); };
  }, [q, open]);

  useEffect(() => {
    function onDoc(e) { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const add = (sym) => {
    const s = sym.trim().toUpperCase();
    if (s && !value.includes(s)) onChange([...value, s]);
    setQ(""); setResults([]); setOpen(false);
  };
  const remove = (sym) => onChange(value.filter((v) => v !== sym));

  return (
    <div className="symbol-picker" ref={boxRef}>
      <div className="symbol-chips">
        {value.map((s) => (
          <span className="symbol-chip" key={s}>{s}<button onClick={() => remove(s)}>×</button></span>
        ))}
        <input
          value={q}
          placeholder={value.length ? "" : placeholder}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) { e.preventDefault(); add(q); } }}
        />
      </div>
      {open && results.length > 0 && (
        <div className="symbol-dropdown">
          {results.map((r) => (
            <button key={`${r.market}:${r.symbol}`} className="symbol-option" onClick={() => add(r.symbol)}>
              <span className="symbol-option-sym">{r.symbol}</span>
              <span className="symbol-option-name">{r.name}</span>
              <span className="symbol-option-market">{r.market}</span>
              <span className={`live-pill sm ${r.source === "live" ? "on" : "off"}`}
                    title={r.source === "live" ? "actually trading on this deployment" : "reference only - no feed here covers it"}>
                {r.source === "live" ? "LIVE" : "seed"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
