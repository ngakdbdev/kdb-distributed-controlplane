/**
 * qLanguage.js - shared q/kdb+ vocabulary + CodeMirror wiring for the query
 * workspace. One source of truth for:
 *   - SCHEMA: static table->columns fallback (also used by Query.jsx's
 *     offline plain-English->q generator, independent of CodeMirror)
 *   - Q_BUILTINS: the q built-in vocabulary, for both syntax highlighting
 *     and as the "generic" completion pool
 *   - qStreamLanguage: a lightweight CodeMirror StreamLanguage (comments,
 *     strings, symbols, temporal literals, keywords/builtins) - not a full
 *     parser, just enough for readable highlighting
 *   - createQCompletionSource: a context-aware completion source (mirrors
 *     the ranking rules the old hand-rolled textarea autocomplete used:
 *     columns/tables outrank the general vocabulary right after
 *     from/where/by/select/update/exec) that reads live table/column data
 *     through a ref so it doesn't need reconfiguring as that data loads in
 */
import { StreamLanguage } from "@codemirror/language";

export const SCHEMA = {
  trade: ["time", "sym", "price", "size", "side", "venue", "shard"],
  risk: ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
};

const Q_KEYWORDS = ["select", "from", "where", "by", "update", "delete", "exec", "insert", "upsert"];

// The rest of the q/kdb+ built-in vocabulary (per the kx reference card) -
// every standard built-in function, not a hand-picked shortlist scoped to
// what trade/risk queries happen to need.
const Q_FUNCTIONS = [
  "abs", "acos", "asin", "atan", "avg", "avgs", "ceiling", "cos", "cor", "cov", "scov",
  "deltas", "dev", "sdev", "svar", "var", "div", "mod", "ema", "exp", "floor", "log",
  "max", "maxs", "mavg", "mcount", "mdev", "med", "min", "mins", "mmax", "mmin", "mmu",
  "msum", "neg", "prd", "prds", "rand", "ratios", "reciprocal", "signum", "sin", "sqrt",
  "sum", "sums", "tan", "wavg", "wsum", "xexp", "xlog",
  "all", "and", "any", "not", "or", "within", "like", "in", "null",
  "asc", "desc", "iasc", "idesc", "distinct", "group", "rank", "xrank", "bin", "binr",
  "except", "inter", "union", "cross",
  "aj", "aj0", "ajf", "ajf0", "asof", "ej", "fby", "ij", "ijf", "lj", "ljf", "pj", "uj",
  "ujf", "wj", "wj1", "xgroup", "xkey", "xcol", "xcols", "xasc", "xdesc", "xbar",
  "xprev", "cols", "keys", "key", "fkeys", "meta", "ungroup", "flip", "enlist", "raze",
  "cut", "sublist", "first", "last", "next", "prev", "reverse", "rotate", "differ",
  "fills", "til", "count", "tables", "view", "views",
  "lower", "upper", "ltrim", "rtrim", "trim", "string", "ss", "ssr", "sv", "vs", "csv",
  "parse", "md5",
  "type", "boolean", "byte", "short", "int", "long", "real", "float", "char", "symbol",
  "timestamp", "month", "date", "datetime", "timespan", "minute", "second", "time",
  "get", "set", "show", "value", "getenv", "setenv", "system", "exit", "gc",
  "read0", "read1", "hopen", "hclose", "hcount", "hdel", "hsym", "load", "rload",
  "save", "rsave", "dsave",
  "each", "over", "scan", "peach", "prior", "eval",
];

export const Q_BUILTINS = [...Q_KEYWORDS, ...Q_FUNCTIONS];

const KEYWORD_SET = new Set(Q_KEYWORDS);
const FUNCTION_SET = new Set(Q_FUNCTIONS);

const RE = {
  comment: /^\s*\/.*$/,
  string: /^"(?:[^"\\]|\\.)*"/,
  symbol: /^`(?::[^\s()[\]{};,]*|[A-Za-z0-9_.][A-Za-z0-9_./]*)?/,
  temporal: /^-?\d{4}\.\d{2}\.\d{2}([DT]\d{2}:\d{2}:\d{2}(\.\d+)?)?/,
  time: /^-?\d{2}:\d{2}(:\d{2}(\.\d+)?)?\b/,
  infinity: /^-?0[NW][bhijefmdznuvtc]?\b/,
  number: /^-?\d+(\.\d+)?[a-zA-Z]?\b/,
  word: /^\.?[A-Za-z_][A-Za-z0-9_.]*/,
};

/**
 * A StreamLanguage rather than a full Lezer grammar - q is terse and this
 * only needs to look right for readability, not round-trip a real parser.
 * Comments only start a line here (a bare `/` mid-expression is divide;
 * disambiguating a trailing comment from that needs real parsing, not a
 * token-at-a-time scanner - same limitation as the VS Code extension's
 * TextMate grammar, see vscode-extension/syntaxes/q.tmLanguage.json).
 */
export const qStreamLanguage = StreamLanguage.define({
  token(stream) {
    if (stream.sol() && stream.match(RE.comment)) return "comment";
    if (stream.match(RE.string)) return "string";
    if (stream.match(RE.symbol)) return "atom";
    if (stream.match(RE.temporal)) return "number";
    if (stream.match(RE.time)) return "number";
    if (stream.match(RE.infinity)) return "number";
    if (stream.match(RE.number)) return "number";
    if (stream.match(RE.word)) {
      const word = stream.current().toLowerCase();
      if (KEYWORD_SET.has(word)) return "keyword";
      if (FUNCTION_SET.has(word)) return "builtin";
      return "variableName";
    }
    stream.next();
    return null;
  },
});

const TABLE_HINT_RE = /\bfrom\s+$/i;
const COLUMN_HINT_RE = /\b(where|by|select|update|exec)\b[^;]*$/i;

/**
 * `liveStateRef` is a React ref holding the latest {tables, liveSchema} -
 * the completion source is created once and reads through the ref each time
 * it's invoked, so the editor's extensions never need reconfiguring as live
 * schema data streams in (which would otherwise reset undo history / drop
 * focus on every fetch).
 */
export function createQCompletionSource(liveStateRef) {
  return (context) => {
    const word = context.matchBefore(/[A-Za-z0-9_]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;

    const fullText = context.state.doc.toString();
    const textBeforeCursor = fullText.slice(0, word.from);
    const { tables, liveSchema } = liveStateRef.current;

    const knownTables = [...new Set([...(tables || []), ...Object.keys(SCHEMA)])];
    const mentioned = knownTables.filter((t) => fullText.includes(t));
    const cols = [...new Set(mentioned.flatMap((t) => (liveSchema && liveSchema[t]) || SCHEMA[t] || []))];

    const wantsTable = TABLE_HINT_RE.test(textBeforeCursor);
    const wantsColumn = !wantsTable && COLUMN_HINT_RE.test(textBeforeCursor);

    const tableOpts = knownTables.map((t) => ({ label: t, type: "class", detail: "table" }));
    const colOpts = cols.map((c) => ({ label: c, type: "property", detail: "column" }));
    const builtinOpts = Q_BUILTINS.map((b) => ({
      label: b, type: KEYWORD_SET.has(b) ? "keyword" : "function",
    }));

    const pool = wantsTable ? [...tableOpts, ...colOpts, ...builtinOpts]
      : wantsColumn ? [...colOpts, ...tableOpts, ...builtinOpts]
      : [...builtinOpts, ...tableOpts, ...colOpts];

    const seen = new Set();
    const options = [];
    for (const o of pool) {
      if (seen.has(o.label)) continue;
      seen.add(o.label);
      options.push(o);
    }
    return { from: word.from, options, validFor: /^[A-Za-z0-9_]*$/ };
  };
}
