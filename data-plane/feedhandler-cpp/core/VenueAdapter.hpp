// VenueAdapter.hpp - the layer that knows what a decoded protocol message
// MEANS for one specific venue: which ITCH message types map to AddOrder
// vs Trade, which SBE schema field is price vs quantity, how to resolve a
// venue-native instrument id to a symbol string. A venue adapter consumes
// DecodedMessage (protocol-level, venue-agnostic) and produces MarketEvent
// (fully normalized) - it is the ONLY layer allowed to know both "ITCH
// message type 'A'" and "this means AddOrder" in the same place.
#pragma once

#include "EventSink.hpp"
#include "FeedConfig.hpp"
#include "ProtocolDecoder.hpp"

namespace fh {

class IVenueAdapter : public MessageSink {
public:
    ~IVenueAdapter() override = default;
    virtual void configure(const FeedConfig& config, EventSink& sink) = 0;
    virtual std::string name() const = 0;
};

} // namespace fh
