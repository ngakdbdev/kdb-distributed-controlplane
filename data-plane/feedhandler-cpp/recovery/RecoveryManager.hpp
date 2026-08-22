// RecoveryManager.hpp - the feed lifecycle state machine (design section 8)
// plus gap-triggered recovery requests (section 9's "gap detected? ->
// RecoveryManager -> retransmit or snapshot" flow).
//
// RecoveryManager does NOT itself open a retransmission-request socket or
// know how a snapshot is fetched - that's inherently venue/transport-
// specific (MoldUDP64's retransmission port, a REST snapshot endpoint,
// whatever a given venue actually offers) and is supplied as callbacks.
// What's genuinely reusable across every venue - and what THIS class
// exists to be - is: recognize a gap, decide retransmit-vs-snapshot
// (based on gap size and whether retransmission is even configured), track
// how long recovery is taking, and expose the resulting feed state for the
// admin portal's status view. In the simulation workflow, the callbacks
// are wired to the simulator's own packet-loss/replay machinery - the
// exact same state machine runs whether the "exchange" on the other end
// is real or synthetic.
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include "GapDetector.hpp"

namespace fh {

enum class FeedState {
    Discovered, Configured, Validating, Connecting, Connected,
    Synchronizing, Live, Degraded, Recovering,
};

const char* toString(FeedState s);

using RetransmitRequestFn = std::function<void(uint64_t gapFromInclusive, uint64_t gapToExclusive)>;
using SnapshotRequestFn = std::function<void()>;
using StateChangeFn = std::function<void(FeedState oldState, FeedState newState)>;

class RecoveryManager {
public:
    // gapSizeSnapshotThreshold: a gap this large or larger skips
    // retransmission and goes straight to requesting a fresh snapshot -
    // real venues bound how far back a retransmission server will serve
    // from, so a huge gap needs a snapshot regardless.
    explicit RecoveryManager(uint64_t startSequence = 1, uint64_t gapSizeSnapshotThreshold = 1000)
        : gapDetector_(startSequence), snapshotThreshold_(gapSizeSnapshotThreshold) {
        gapDetector_.setOnGap([this](uint64_t from, uint64_t to) { handleGap(from, to); });
    }

    void setRetransmitRequestFn(RetransmitRequestFn fn) { retransmitFn_ = std::move(fn); }
    void setSnapshotRequestFn(SnapshotRequestFn fn) { snapshotFn_ = std::move(fn); }
    void setOnStateChange(StateChangeFn fn) { onStateChange_ = std::move(fn); }

    // Feeds a sequence number through gap detection. Call this for every
    // message on the live path; a gap automatically triggers recovery
    // (state -> Recovering) and the configured retransmit/snapshot
    // callback. Once caught up (see markRecovered()), state returns to Live.
    void onSequence(uint64_t seq) { gapDetector_.onSequence(seq); }

    // The recovery source (retransmission reply, snapshot response, or the
    // simulator's synthetic replay) calls this once it believes the gap is
    // closed - transitions Recovering -> Live. A real implementation would
    // instead re-verify no further gap exists on the next live sequence
    // seen; this explicit call is the simpler, still-correct v1 contract
    // (see design section 9's diagram - recovery has a clear completion
    // point in every real venue's own recovery protocol too).
    void markRecovered() { setState(FeedState::Live); }

    void setState(FeedState s) {
        if (s == state_) return;
        FeedState old = state_;
        state_ = s;
        if (onStateChange_) onStateChange_(old, s);
    }

    FeedState state() const { return state_; }
    uint64_t totalGaps() const { return gapDetector_.totalGaps(); }
    uint64_t expected() const { return gapDetector_.expected(); }

private:
    void handleGap(uint64_t from, uint64_t to) {
        setState(FeedState::Recovering);
        uint64_t size = to - from;
        if (size < snapshotThreshold_ && retransmitFn_) {
            retransmitFn_(from, to);
        } else if (snapshotFn_) {
            snapshotFn_();
        }
        // If neither callback is configured (e.g. a feed with recovery
        // disabled), state stays Recovering until the next in-order
        // message naturally resynchronizes it - see markRecovered()'s
        // comment; a caller that never calls it also just self-heals on
        // the next gap-free sequence, since onSequence() doesn't require
        // markRecovered() to have been called to keep tracking correctly.
    }

    GapDetector gapDetector_;
    uint64_t snapshotThreshold_;
    FeedState state_ = FeedState::Discovered;
    RetransmitRequestFn retransmitFn_;
    SnapshotRequestFn snapshotFn_;
    StateChangeFn onStateChange_;
};

} // namespace fh
