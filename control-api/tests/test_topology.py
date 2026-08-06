"""Tests for the canonical shard-topology module."""
import string

import pytest

from app import topology as T


def test_n2_reproduces_legacy_am_nz_split():
    """The default (2 shards) must behave exactly like the shipped A-M / N-Z
    MVP, so switching to config-driven topology changes nothing by default."""
    assert T.letter_ranges(2) == [("A", "M"), ("N", "Z")]
    ss = T.shards(2)
    assert [s.id for s in ss] == ["s0", "s1"]
    assert ss[0].label == "A-M"
    assert ss[1].label == "N-Z"


@pytest.mark.parametrize("n", range(1, T.MAX_SHARDS + 1))
def test_ranges_are_contiguous_and_cover_the_alphabet(n):
    ranges = T.letter_ranges(n)
    assert len(ranges) == n
    # concatenating each [lo..hi] slice must rebuild A-Z exactly, in order
    rebuilt = ""
    for lo, hi in ranges:
        rebuilt += string.ascii_uppercase[
            string.ascii_uppercase.index(lo): string.ascii_uppercase.index(hi) + 1
        ]
    assert rebuilt == string.ascii_uppercase


@pytest.mark.parametrize("n", range(1, T.MAX_SHARDS + 1))
def test_ranges_are_as_even_as_possible(n):
    sizes = [
        string.ascii_uppercase.index(hi) - string.ascii_uppercase.index(lo) + 1
        for lo, hi in T.letter_ranges(n)
    ]
    assert sum(sizes) == 26
    assert max(sizes) - min(sizes) <= 1  # differ by at most one letter


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 26])
def test_every_letter_routes_into_exactly_one_shard(n):
    valid_ids = {s.id for s in T.shards(n)}
    for ch in string.ascii_uppercase:
        assert T.shard_of(ch + "XYZ", n) in valid_ids


def test_shard_of_known_symbols_at_n2():
    assert T.shard_of("AAPL", 2) == "s0"
    assert T.shard_of("MSFT", 2) == "s0"   # M is the boundary, still s0
    assert T.shard_of("NFLX", 2) == "s1"
    assert T.shard_of("TSLA", 2) == "s1"


def test_shard_of_non_alpha_falls_to_s0():
    # only symbols with NO A-Z letter at all fall to s0
    assert T.shard_of("123", 4) == "s0"
    assert T.shard_of("", 4) == "s0"
    assert T.shard_of("__.5", 4) == "s0"


def test_shard_of_skips_leading_non_alpha():
    # "_TMP" has a real letter (T); it routes on that, not on the underscore.
    # n=4 ranges: A-G / H-N / O-T / U-Z  ->  T is in O-T -> s2
    assert T.shard_of("_TMP", 4) == "s2"


def test_shard_of_uses_first_alpha_letter():
    # leading digit skipped, routes on the first real letter
    assert T.shard_of("7NVDA", 2) == "s1"


def test_uniform_ports_per_tier():
    js = T.shards_json(4)
    rdb_ports = {h["rdb"].split(":")[1] for h in js["shards"]}
    idb_ports = {h["idb"].split(":")[1] for h in js["shards"]}
    assert rdb_ports == {"5020"}   # every rdb on the same port, different host
    assert idb_ports == {"5030"}
    hosts = [h["rdb"].split(":")[0] for h in js["shards"]]
    assert hosts == ["rdb-s0", "rdb-s1", "rdb-s2", "rdb-s3"]


def test_healed_services_scales_with_shard_count():
    assert T.healed_services(1) == ["tp-s0", "wdb-s0", "rdb-s0", "idb-s0", "hdb-s0", "gateway"]
    h4 = T.healed_services(4)
    assert len(h4) == 5 * 4 + 1        # 5 tiers * 4 shards + gateway
    assert "gateway" in h4
    assert "bpipe-sim" not in h4       # feeds are never healed as failures


def test_managed_services_includes_feeds_and_gateway():
    m = T.managed_services(2)
    assert m["gateway"] == "gateway"
    assert m["bpipe-sim"] == "bpipe-sim"
    assert m["crims-sim"] == "crims-sim"
    assert m["tp-s0"] == "tp-s0" and m["idb-s1"] == "idb-s1"


def test_validation_rejects_bad_counts():
    for bad in (0, -1, 27, 100):
        with pytest.raises(ValueError):
            T.letter_ranges(bad)


def test_service_names_and_volumes():
    s0 = T.shards(2)[0]
    assert s0.service("tickerplant") == "tp-s0"
    assert s0.service("wdb") == "wdb-s0"
    assert s0.db_volume == "db-s0"
    assert s0.tp_log_volume == "tp-log-s0"
