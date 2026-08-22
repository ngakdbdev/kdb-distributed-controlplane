// SequenceTracker.hpp - per-session gap detection. One instance per feed
// session (or per A/B leg, when redundancy is enabled - see FeedArbiter).
// Deliberately dumb and synchronous: it only tracks "what's the next
// expected sequence number" and reports gaps/duplicates/reordering as they
// happen. What to DO about a gap (retransmit request vs snapshot) is
// recovery/RecoveryManager's job, not this class's.
#pragma once

#include <cstdint>

namespace fh {

enum class SeqResult { InOrder, Gap, Duplicate, Reordered };

class SequenceTracker {
public:
    explicit SequenceTracker(uint64_t startSequence = 1) : expected_(startSequence) {}

    // Call once per message/packet sequence number seen. Returns what kind
    // of event this was; `expected()` always reflects what should come next
    // regardless of the result (a gap advances expectation to seq+1, same
    // as a real feed handler would - waiting for a definitively-lost
    // packet forever isn't recoverable without external retransmission).
    SeqResult onSequence(uint64_t seq) {
        if (seq == expected_) {
            expected_ = seq + 1;
            lastGapSize_ = 0;
            return SeqResult::InOrder;
        }
        if (seq < expected_) {
            return SeqResult::Duplicate;
        }
        // seq > expected_: a gap. Track its size (for recovery/metrics),
        // then resynchronize expectation to just past this packet - the
        // missed range [expected_, seq) is what a RecoveryManager should
        // request retransmission for.
        lastGapFrom_ = expected_;
        lastGapTo_ = seq;
        lastGapSize_ = seq - expected_;
        expected_ = seq + 1;
        totalGaps_ += 1;
        return SeqResult::Gap;
    }

    uint64_t expected() const { return expected_; }
    uint64_t lastGapFrom() const { return lastGapFrom_; }
    uint64_t lastGapTo() const { return lastGapTo_; }
    uint64_t lastGapSize() const { return lastGapSize_; }
    uint64_t totalGaps() const { return totalGaps_; }

private:
    uint64_t expected_;
    uint64_t lastGapFrom_ = 0;
    uint64_t lastGapTo_ = 0;
    uint64_t lastGapSize_ = 0;
    uint64_t totalGaps_ = 0;
};

} // namespace fh
