// NasdaqItchVenueAdapter.hpp - interprets ITCH messages (already extracted
// from MoldUDP64/SoupBinTCP framing by the protocol decoder one layer
// down) into normalized MarketEvents. Owns the ONE piece of real state an
// ITCH consumer needs: the Stock Locate -> ticker symbol map, built from
// Stock Directory ('R') messages - every other ITCH message references
// its instrument by locate code, not by symbol string, so nothing else
// can resolve a symbol without this adapter having seen the directory
// message for it first (same as a real ITCH consumer: symbols not yet
// announced can't be resolved, by design of the protocol itself).
#pragma once

#include <unordered_map>
#include "../core/VenueAdapter.hpp"

namespace fh {

class NasdaqItchVenueAdapter : public IVenueAdapter {
public:
    void configure(const FeedConfig& config, EventSink& sink) override;
    void onMessage(const DecodedMessage& msg) override;
    std::string name() const override { return "nasdaq_itch"; }

    // exposed for tests: how many locate codes have been resolved via
    // Stock Directory messages seen so far.
    size_t knownSymbolCount() const { return locateToSymbol_.size(); }

private:
    EventSink* sink_ = nullptr;
    FeedConfig config_;
    std::unordered_map<uint16_t, std::string> locateToSymbol_;

    std::string symbolFor(uint16_t locate) const;
};

} // namespace fh
