#include "test_framework.hpp"
#include "../core/SequenceTracker.hpp"
#include "../core/FeedArbiter.hpp"
#include "../recovery/RecoveryManager.hpp"

using namespace fh;

TEST(sequence_tracker_in_order_sequence) {
    SequenceTracker t(1);
    CHECK(t.onSequence(1) == SeqResult::InOrder);
    CHECK(t.onSequence(2) == SeqResult::InOrder);
    CHECK(t.onSequence(3) == SeqResult::InOrder);
    CHECK_EQ(t.expected(), 4u);
}

TEST(sequence_tracker_detects_gap_and_resyncs) {
    SequenceTracker t(1);
    t.onSequence(1);
    auto r = t.onSequence(5); // skipped 2,3,4
    CHECK(r == SeqResult::Gap);
    CHECK_EQ(t.lastGapFrom(), 2u);
    CHECK_EQ(t.lastGapTo(), 5u);
    CHECK_EQ(t.lastGapSize(), 3u);
    CHECK_EQ(t.expected(), 6u); // resynced past the gap
    CHECK_EQ(t.totalGaps(), 1u);
}

TEST(sequence_tracker_detects_duplicate) {
    SequenceTracker t(1);
    t.onSequence(1);
    t.onSequence(2);
    CHECK(t.onSequence(1) == SeqResult::Duplicate); // already-seen sequence replayed
}

TEST(recovery_manager_gap_triggers_retransmit_callback) {
    RecoveryManager rm(1, /*snapshotThreshold=*/1000);
    bool retransmitCalled = false;
    uint64_t gotFrom = 0, gotTo = 0;
    rm.setRetransmitRequestFn([&](uint64_t from, uint64_t to) { retransmitCalled = true; gotFrom = from; gotTo = to; });

    rm.onSequence(1);
    rm.onSequence(2);
    rm.onSequence(10); // gap: 3..9 missing

    CHECK(retransmitCalled);
    CHECK_EQ(gotFrom, 3u);
    CHECK_EQ(gotTo, 10u);
    CHECK(rm.state() == FeedState::Recovering);
}

TEST(recovery_manager_large_gap_prefers_snapshot_over_retransmit) {
    RecoveryManager rm(1, /*snapshotThreshold=*/5);
    bool retransmitCalled = false, snapshotCalled = false;
    rm.setRetransmitRequestFn([&](uint64_t, uint64_t) { retransmitCalled = true; });
    rm.setSnapshotRequestFn([&] { snapshotCalled = true; });

    rm.onSequence(1);
    rm.onSequence(100); // gap of 98 >> threshold of 5

    CHECK(!retransmitCalled);
    CHECK(snapshotCalled);
}

TEST(recovery_manager_mark_recovered_returns_to_live) {
    RecoveryManager rm;
    rm.setState(FeedState::Live);
    rm.onSequence(1);
    rm.onSequence(5); // gap -> Recovering
    CHECK(rm.state() == FeedState::Recovering);
    rm.markRecovered();
    CHECK(rm.state() == FeedState::Live);
}

TEST(feed_arbiter_drops_duplicate_sequence_from_redundant_leg) {
    struct CountingSink : MessageSink {
        int count = 0;
        void onMessage(const DecodedMessage&) override { ++count; }
    } downstream;
    FeedArbiter arbiter(downstream);

    DecodedMessage m1; m1.sequence = 1;
    DecodedMessage m1dup; m1dup.sequence = 1; // same sequence arriving on the "B" leg
    DecodedMessage m2; m2.sequence = 2;

    arbiter.onMessage(m1);
    arbiter.onMessage(m1dup);
    arbiter.onMessage(m2);

    CHECK_EQ(downstream.count, 2);
    CHECK_EQ(arbiter.duplicatesDropped(), 1ULL);
}
