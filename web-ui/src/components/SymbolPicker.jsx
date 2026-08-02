import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Type-ahead symbol picker. `value` is an array of symbol strings; `onChange`
// gets the new array. Users can type to search the reference (or a market) and
// click results, or type a symbol and press Enter to add it directly.
export default function SymbolPicker({ value = [], onChange, placeholder = "search symbols…" }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    let live = true;
    if (!q.trim()) { setResults([]); return; }
    api.searchSymbols(q).then((d) => { if (live) setResults(d.symbols || []); }).catch(() => {});
    return () => { live = false; };
  }, [q]);

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
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
