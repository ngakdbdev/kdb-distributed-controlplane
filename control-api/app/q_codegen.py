"""
q_codegen.py - plain language -> a q/kdb+ FUNCTION definition (not a single
query - see nl2q.py for that). Powers POST /query/codegen.

Why this is a separate module from nl2q.py rather than one more mode of it:
nl2q's whole design leans on the output being exactly one line (its _clean()
keeps only the last non-empty line, and its prompt hard-bans anything but a
SELECT-shaped read) - both of those are wrong for a multi-line function
body, so reusing it would either mangle the output or refuse to produce it.

Scope and safety, read this before extending it:
  * The generated function is a DRAFT. It is written into the same query
    editor a plain query would be (see Query.jsx), which means the human
    reviews it and explicitly clicks Run before anything executes - exactly
    the nl2q pattern, just for something riskier to eyeball. This module
    does not add any new execution path: a function definition is just an
    assignment (`name:{...}`), and calling it is just an expression, so the
    EXISTING /query/run + its read-only guard (query_service.check_readonly)
    already covers both without modification.
  * A generated function must never attempt to place a real order itself -
    trade execution in this system goes through control-api's /trading
    endpoints (a reviewed, audited, Python-side path), which a q function
    running inside an RDB/gateway process has no way to reach anyway. The
    right scope for generated code here is compute/decide, not act - e.g. a
    hedging/routing function returns which leg is cheaper and by how much;
    a human or a separate integration decides whether to act on it.
"""
from __future__ import annotations

import re

from . import llm_provider, nl2q

_SYSTEM_PROMPT_TEMPLATE = """You write a q/kdb+ FUNCTION - ANY function, not just trading/market-data ones; \
plain data structures and algorithms are equally in scope - for a live trading system's query workspace, \
from a plain-English description of what it should compute. Output ONLY the q code and NOTHING else: no \
markdown fences, no leading "q:"/"code:" label, and critically no explanation, bullet points, or prose \
AFTER the code either - the response must end with the closing brace of the code, not a paragraph \
describing what it does. The code may and usually should span multiple lines.

# Schema (tables that exist, if the function needs to read live data - most functions won't)
{schema_block}

# What the function must NOT do
- Never place, cancel, or modify an order, or call anything under `.trading`/`.gw.order` or similar - this \
  process has no order-execution capability, and a function that tries to fake one is worse than one that \
  just returns a decision. Compute and return a result (a rate, a decision, a signal) - acting on it is a \
  separate, human-reviewed step outside this box.
- Never use `system`, `set` used as a global-namespace side effect, `delete`/`update`/`upsert` against a \
  real table, `exit`, `hopen`/`hclose`, or any file/socket primitive.
- Never assume it's running with elevated/system access - it runs inside the same read-only q process the \
  query workspace already queries.

# CRITICAL q semantics - get these wrong and the function silently does the wrong thing, not an error
- **q has no `return` statement, and `if[cond;expr]` does NOT stop the function** - unlike C/Python/JS, \
  execution always continues to the next statement after an `if` block regardless of the condition. A \
  function's result is simply whatever its LAST top-level statement evaluates to - nothing more, nothing \
  less. This means `if` is almost never how you should encode a base case or an early exit:
    WRONG:   {{[n] if[n=0; 0]; if[n=1; 1]; n}}     (returns n even when n is 0 or 1 - the ifs change nothing)
    correct: {{[n] $[n=0; 0; $[n=1; 1; n]]}}        (nested $[cond;then;else] IS the returned value)
  Use `if` only for a statement you want to run conditionally for its SIDE EFFECT partway through a \
  function that has more statements after it (rare in this box, since side effects are mostly disallowed \
  above) - never to compute the function's answer.
- **Recursion**: refer to the function from inside itself with `.z.s` (not the function's own name) - it \
  always resolves correctly regardless of how/when the assignment completes, and the base case MUST be the \
  first branch of a `$[...]`, never an early-exit `if`:
    correct: fib:{{[n] $[n<2; n; .z.s[n-1]+.z.s[n-2]]}}
- **Only use q's real built-ins - do not invent a plausible-looking name from another language:**
    stddev      -> WRONG. Use `dev` for standard deviation.
    sort(...)   -> WRONG, no such function. Use `asc`/`desc` (sort a list) or `xasc`/`xdesc` col (sort a \
                   table by column).
    lst[:n] / lst[n:]  -> WRONG, that is Python slicing and is not valid q. Use `n#lst` (take first n), \
                   `-n#lst` (take last n), `n _ lst` (drop first n), `-n _ lst` (drop last n).
    now         -> WRONG, no such variable. Use `.z.p` (current timestamp) if you need "the current time" \
                   independent of any table.
  If you are not certain a name is a real q built-in, prefer a documented one from this prompt over \
  guessing - a wrong but plausible-looking name fails at call time, not while you're writing it.

# q function syntax that matters here
- Define with `name:{{[param1;param2;...] expr1; expr2; ...; lastExprIsTheReturnValue}}`. Statements inside \
  are semicolon-separated.
- Conditional/branching (and the ONLY way to make a function's result depend on a condition): \
  `$[condition; then; else]`, nestable for more branches: `$[c1;v1;$[c2;v2;v3]]`. There is NO `cond?a:b` \
  C-style ternary in q - `?` means something else entirely (find/binary-search/vector-conditional \
  depending on context) - never write `cond?a:b`, always `$[cond;a;b]`.
- Once a parameter is named in `[...]`, use that exact name for the rest of the function - never \
  introduce a shorter ad-hoc name for the same thing partway through (e.g. naming the parameter `sym` and \
  then referring to it as `s` later is an undefined-variable bug, not a stylistic choice).
- Build a dictionary result with `` `k1`k2`k3!(v1;v2;v3) `` - backtick-prefixed keys, matching value list.
- Build a symbol from parts (e.g. two currency codes into one pair symbol) with \
  `` `$(string[a],string[b]) `` - the parens matter, an unparenthesized `` `$string[a],string[b] `` can cast \
  the wrong thing depending on what follows.
- To read the latest known value for something from a table: `last exec col from t where cond` (returns a \
  null, not an error, if nothing matches - always something the caller can check for).
- `each` maps a function over a list: `f each lst`. `raze` flattens one level of nesting.
- Prefer straight-line arithmetic and `$[...]` over recursion for anything expressible that way - q is a \
  vector language and this keeps the function easy for a human to review before running it. Reach for \
  recursion (via `.z.s`, above) only for things that are naturally recursive (tree/graph walks, classic \
  algorithms explicitly asked for) - don't force it where a vector expression would do.

# Worked examples

User: write a function that returns the nth fibonacci number
q:
fib:{{[n] $[n<2; n; .z.s[n-1]+.z.s[n-2]]}}

User: write a function that tells me if a number is in a sorted list, using binary search
q:
binarySearch:{{[lst;val]
  n:count lst;
  $[n=0; 0b;
    [mid:n div 2;
     $[lst[mid]=val; 1b;
       $[val<lst[mid]; .z.s[mid#lst;val]; .z.s[(mid+1)_lst;val]]]]]
  }}

User: I need to convert GBP to JPY. Write something that checks whether going GBP to USD then USD to JPY \
beats converting GBP to JPY directly, and tells me which route to use.
q:
triangularRoute:{{[base;bridge;quote;amount]
  rate:{{[a;b] last exec price from trade where sym=`$(string[a],string[b])}};
  directRate:rate[base;quote];
  leg1Rate:rate[base;bridge];
  leg2Rate:rate[bridge;quote];
  syntheticRate:leg1Rate*leg2Rate;
  viaBridge:syntheticRate>directRate;
  bestRate:$[viaBridge;syntheticRate;directRate];
  `route`directRate`syntheticRate`bestRate`amountOut!(
    $[viaBridge;bridge;`direct];
    directRate;
    syntheticRate;
    bestRate;
    amount*bestRate)
  }}
GBP/JPY here are just the example call's arguments, not anything hardcoded in the function - name \
functions and parameters generically (base/bridge/quote, a/b, sym, ...), NEVER after the specific values \
used in the example that prompted the request. The same function must work unmodified for any other \
symbols the caller passes in. Prefer having the function look up current values itself (as `rate` does \
above, by building the pair symbol and reading the latest matching row) over requiring the caller to \
already know them - that's what makes it reusable instead of a one-off calculation.

User: a function that returns the current bid-ask style spread proxy (max minus min price) for a symbol \
over the trades seen so far
q:
priceSpread:{{[s] select spread:max[price]-min[price] from trade where sym=s}}
"""


class NotConfigured(RuntimeError):
    """No LLM provider configured - there is no offline fallback for code generation."""


_FENCE_RE = re.compile(r"^```(?:q|k)?\s*\n?|\n?```\s*$")
_LABEL_RE = re.compile(r"^\s*(?:q|code)\s*:\s*\n?", re.IGNORECASE)
# Even with "nothing after the code" spelled out in the prompt, a small model
# sometimes still appends an explanation - confirmed empirically. A prose
# line essentially never contains q's own punctuation (: [ ] ; { }), so once
# we've already collected at least one real code line, stop at the first
# line that both looks like prose AND has none of that punctuation - a real
# continuation of the code would have some.
_PROSE_LINE_RE = re.compile(r"^\s*(-|\*|this |the |it |note|here|these|in this|finally|these steps)", re.IGNORECASE)


def _strip_trailing_prose(text: str) -> str:
    kept: list[str] = []
    for ln in text.splitlines():
        if kept and _PROSE_LINE_RE.match(ln) and not any(c in ln for c in ":[];{}"):
            break
        kept.append(ln)
    return "\n".join(kept).rstrip()


def _clean(text: str) -> str:
    """Unlike nl2q._clean, this must NOT collapse to one line - a function
    body is expected to span many. Only strip fences/labels/trailing prose
    around the whole block."""
    text = _FENCE_RE.sub("", text.strip())
    text = _LABEL_RE.sub("", text.strip())
    return _strip_trailing_prose(text.strip())


_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
_BRACKET_CLOSERS = {v: k for k, v in _BRACKET_PAIRS.items()}


def _is_balanced(code: str) -> bool:
    """A generated function that stops mid-expression (confirmed to happen
    with the local 3B model - not a truncated max_tokens, it just stops)
    always leaves brackets open. Not a correctness check - balanced
    brackets doesn't mean the logic is right - but it's a cheap, reliable
    signal that the response is at least COMPLETE, worth one retry on."""
    stack: list[str] = []
    for ch in code:
        if ch in _BRACKET_PAIRS:
            stack.append(ch)
        elif ch in _BRACKET_CLOSERS:
            if not stack or stack[-1] != _BRACKET_CLOSERS[ch]:
                return False
            stack.pop()
    return not stack


def generate(text: str, host: str | None = None, port: int | None = None) -> str:
    """Translate `text` into a q function definition. Raises NotConfigured
    if no provider is set, or llm_provider.LLMError on a call failure."""
    if not llm_provider.configured():
        raise NotConfigured("no LLM provider configured")

    live = nl2q.live_schema(host, port) if (host and port) else None
    system = _SYSTEM_PROMPT_TEMPLATE.format(schema_block=nl2q.schema_block(live))
    user = text.strip()
    code = _clean(llm_provider.complete(system, user, max_tokens=500))
    if not _is_balanced(code):
        # llm_provider calls with temperature=0 - resending the identical
        # prompt would just reproduce the same (incomplete) output, so the
        # retry has to actually be a different request to have a chance of
        # a different result.
        retry_user = user + "\n\n(Your previous attempt cut off mid-expression. Write the COMPLETE function, with every bracket closed.)"
        code = _clean(llm_provider.complete(system, retry_user, max_tokens=500))
    return code
