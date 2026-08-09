"""Tests for the static kdb+ migration/effort analyzer (pure heuristics, no
q interpreter involved - see app/migration_analyzer.py's module docstring for
why that's a hard requirement, not a shortcut)."""
from app import migration_analyzer as ma

SIMPLE = """
trade:([] time:`timestamp$(); sym:`symbol$(); price:`float$(); size:`long$())
"""

BESPOKE = """
trade:([] time:`timestamp$(); sym:`symbol$(); price:`float$())
quote:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())

.ns.upd:{[t;data] t insert data}
.ns.vwap:{[t] select sym, vwap:size wavg price by sym from t}

.z.ts:{[] .ns.rollEod[]}
.z.pg:{[msg] value msg}

h:hopen `:10.0.0.5:5010
h2:hopen `:otherhost:5011

\\l other.q

r1:select sym, avg price by sym from trade
r2:select sym, max price by sym from trade
"""


def test_analyze_script_finds_tables_and_functions():
    a = ma.analyze_script("tick.q", BESPOKE)
    assert set(a.tables) == {"trade", "quote"}
    assert ".ns.upd" in a.functions and ".ns.vwap" in a.functions

def test_analyze_script_flags_risky_z_handlers():
    a = ma.analyze_script("tick.q", BESPOKE)
    handlers = {h for h, _ in a.risky_handlers}
    assert handlers == {".z.ts", ".z.pg"}

def test_analyze_script_counts_hardcoded_hosts_and_loads():
    a = ma.analyze_script("tick.q", BESPOKE)
    assert a.hardcoded_hosts == 2
    assert a.loads == ["other.q"]

def test_analyze_script_counts_aggregations():
    a = ma.analyze_script("tick.q", BESPOKE)
    assert a.aggregation_queries == 3   # the two r1/r2 + .ns.vwap's select-by

def test_simple_script_has_no_risk_signals():
    a = ma.analyze_script("simple.q", SIMPLE)
    assert a.risky_handlers == [] and a.hardcoded_hosts == 0 and a.loads == []


# ---- effort estimate --------------------------------------------------------

def test_effort_small_script_is_size_s():
    report = ma.analyze([{"name": "simple.q", "content": SIMPLE}])
    assert report["effort"]["size"] == "S"
    assert report["effort"]["factors"] == []  # nothing scored above zero

def test_effort_bespoke_script_scores_higher_and_explains_why():
    report = ma.analyze([{"name": "tick.q", "content": BESPOKE}])
    assert report["effort"]["score"] > 0
    joined = " ".join(report["effort"]["factors"])
    assert ".z.pg" in joined and "hardcoded" in joined

def test_effort_scales_with_more_scripts():
    one = ma.estimate_effort([ma.analyze_script("a.q", BESPOKE)])
    two = ma.estimate_effort([ma.analyze_script("a.q", BESPOKE), ma.analyze_script("b.q", BESPOKE)])
    assert two.score >= one.score

def test_effort_bands_are_monotonic_and_cover_zero_to_large():
    scores_to_sizes = [(0, "S"), (20, "M"), (40, "L"), (100, "XL")]
    for score, expected in scores_to_sizes:
        analyses = []  # drive via a fake report shape isn't needed - test the bands directly
        size, lo, hi = next((s, l, h) for lo_b, hi_b, s, l, h in ma._SIZE_BANDS if lo_b <= score < hi_b)
        assert size == expected
        assert lo < hi


# ---- full report shape -----------------------------------------------------

def test_analyze_skips_empty_files():
    report = ma.analyze([{"name": "empty.q", "content": "   \n  "},
                         {"name": "real.q", "content": SIMPLE}])
    assert report["totals"]["scripts"] == 1

def test_analyze_report_totals_match_scripts():
    report = ma.analyze([{"name": "a.q", "content": SIMPLE}, {"name": "b.q", "content": BESPOKE}])
    assert report["totals"]["scripts"] == 2
    assert set(report["totals"]["tables"]) == {"trade", "quote"}
    assert report["totals"]["risky_handlers"] == 2
