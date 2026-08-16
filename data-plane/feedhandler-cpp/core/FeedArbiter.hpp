// FeedArbiter.hpp - A/B feed redundancy as a reusable core component, not
// per-venue code (see design section 10). Two independent legs (A and B)
// of the SAME logical feed each decode into DecodedMessages; the arbiter
// passes through the FIRST copy of each sequence number it sees and
// silently drops the duplicate from whichever leg arrives second. A gap on
// one leg is invisible downstream as long as the other leg has that
// sequence - that's the entire point of paying for two multicast lines.
#pragma once

#include <cstdint>
#include <deque>
#include <unordered_set>

#include "ProtocolDecoder.hpp"

namespace fh {

class FeedArbiter : public MessageSink {
public:
    // `downstream` receives the arbitrated (de-duplicated) message stream.
    // `dedupWindow` bounds memory: only the last N sequence numbers are
    // remembered for duplicate detection, since a legitimate A/B pair
    // arrives within milliseconds of each other, not messages apart.
    explicit FeedArbiter(MessageSink& downstream, size_t dedupWindow = 4096)
        : downstream_(downstream), window_(dedupWindow) {}

    void onMessage(const DecodedMessage& msg) override {
        if (seen_.count(msg.sequence)) {
            duplicatesDropped_++;
            return;
        }
        seen_.insert(msg.sequence);
        order_.push_back(msg.sequence);
        if (order_.size() > window_) {
            seen_.erase(order_.front());
            order_.pop_front();
        }
        downstream_.onMessage(msg);
    }

    uint64_t duplicatesDropped() const { return duplicatesDropped_; }

private:
    MessageSink& downstream_;
    size_t window_;
    std::unordered_set<uint64_t> seen_;
    std::deque<uint64_t> order_;
    uint64_t duplicatesDropped_ = 0;
};

} // namespace fh
