// WsJsonSimulator.hpp - generates synthetic Coinbase-"matches"-channel-
// shaped JSON trade messages (see venues/GenericWsJsonVenueAdapter.hpp and
// data-plane/feeds/providers/normalize.py's coinbase_match() for the real
// shape this mirrors). This is the highest-value simulator for proving the
// end-to-end pipeline today: unlike the ITCH/SBE simulators (which stand
// in for feeds this environment has no way to ever really connect to),
// Coinbase's real feed is public and free - the SAME GenericWsJsonVenueAdapter
// config that consumes these synthetic messages works unchanged against
// the real wss://ws-feed.exchange.coinbase.com feed later, via
// WebSocketTransport instead of InProcessTransport. Nothing about the
// decode/normalize/publish path needs to change to go from simulated to
// real for this provider.
#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include "../transport/InProcessTransport.hpp"

namespace fh::sim {

struct WsJsonSimConfig {
    std::vector<std::string> symbols = {"BTC-USD", "ETH-USD"};
    uint32_t tradesPerSymbol = 20;
    double basePrice = 50000.0;
};

class WsJsonSimulator {
public:
    explicit WsJsonSimulator(WsJsonSimConfig cfg) : cfg_(std::move(cfg)) {}
    size_t run(InProcessTransport& transport);

private:
    WsJsonSimConfig cfg_;
};

} // namespace fh::sim
