#include "RecoveryManager.hpp"

namespace fh {

const char* toString(FeedState s) {
    switch (s) {
        case FeedState::Discovered: return "DISCOVERED";
        case FeedState::Configured: return "CONFIGURED";
        case FeedState::Validating: return "VALIDATING";
        case FeedState::Connecting: return "CONNECTING";
        case FeedState::Connected: return "CONNECTED";
        case FeedState::Synchronizing: return "SYNCHRONIZING";
        case FeedState::Live: return "LIVE";
        case FeedState::Degraded: return "DEGRADED";
        case FeedState::Recovering: return "RECOVERING";
        default: return "UNKNOWN";
    }
}

} // namespace fh
