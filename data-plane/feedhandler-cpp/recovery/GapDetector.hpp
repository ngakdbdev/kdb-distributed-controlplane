// GapDetector.hpp - the recovery-facing wrapper around core::SequenceTracker
// (see design section 9). Kept as its own small class, separate from the
// bare SequenceTracker, because recovery needs a bit more than "was this
// in order": a callback fired exactly once per gap (not once per message
// after it, and not re-fired if the same tracker later sees a duplicate),
// which is what actually triggers a RecoveryManager request.
#pragma once

#include <functional>
#include "../core/SequenceTracker.hpp"

namespace fh {

// (gapFromInclusive, gapToExclusive) - the range of sequence numbers that
// were skipped, i.e. never seen in order. A RecoveryManager uses this to
// build a retransmission request (MoldUDP64) or decide a snapshot is
// needed instead (gap too large / retransmission unavailable).
using GapCallback = std::function<void(uint64_t gapFromInclusive, uint64_t gapToExclusive)>;

class GapDetector {
public:
    explicit GapDetector(uint64_t startSequence = 1) : tracker_(startSequence) {}

    void setOnGap(GapCallback cb) { onGap_ = std::move(cb); }

    // Returns true if `seq` was in order (safe to process normally);
    // false for a gap OR a duplicate (either way, the caller should NOT
    // treat this message as a fresh in-order arrival - a duplicate is
    // typically just dropped, matching FeedArbiter's own dedup behavior
    // when redundancy is off and a retransmission resends something
    // already processed).
    bool onSequence(uint64_t seq) {
        SeqResult r = tracker_.onSequence(seq);
        if (r == SeqResult::Gap && onGap_) onGap_(tracker_.lastGapFrom(), tracker_.lastGapTo());
        return r == SeqResult::InOrder;
    }

    uint64_t expected() const { return tracker_.expected(); }
    uint64_t totalGaps() const { return tracker_.totalGaps(); }

private:
    SequenceTracker tracker_;
    GapCallback onGap_;
};

} // namespace fh
