// GenericFixVenueAdapter.hpp - interprets FIX MarketDataSnapshotFullRefresh
// ('W') and MarketDataIncrementalRefresh ('X') messages into normalized
// MarketEvents. "Generic" because FIX's tag=value vocabulary for market
// data (Symbol/MDEntryType/MDEntryPx/MDEntrySize) is standardized across
// venues that speak FIX for market data at all - unlike ITCH/SBE, there's
// usually no venue-specific adapter needed, just per-venue session
// parameters (SenderCompID etc., in FeedConfig) for the logon handshake.
//
// FIX's repeating MDEntry group isn't parsed via a formal group-count tag
// (this codebase's FixMessage is a flat ordered tag list, not a group-
// aware parser - see FixMessage.hpp's header) - instead, since group
// entries repeat the SAME tags in sequence, this adapter walks the field
// list and starts a new entry each time it sees MDEntryType (269) again.
// Correct for the common non-nested case a market-data group actually is.
#pragma once

#include "../core/VenueAdapter.hpp"

namespace fh {

class GenericFixVenueAdapter : public IVenueAdapter {
public:
    void configure(const FeedConfig& config, EventSink& sink) override;
    void onMessage(const DecodedMessage& msg) override;
    std::string name() const override { return "generic_fix"; }

private:
    EventSink* sink_ = nullptr;
    FeedConfig config_;
};

} // namespace fh
