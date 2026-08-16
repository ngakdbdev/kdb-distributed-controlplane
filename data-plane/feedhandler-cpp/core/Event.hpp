// Event.hpp - the normalized market-data event every protocol decoder and
// venue adapter converges on. Nothing downstream of a VenueAdapter (recovery,
// book building, the kdb+ publisher) ever sees a raw ITCH/SBE/FIX message -
// only this. That's the single most important structural decision in this
// codebase: it's what lets NASDAQ ITCH, CME SBE, and a crypto exchange's
// WebSocket/JSON feed all end up on the same publish path with zero
// venue-specific code below the adapter layer.
#pragma once

#include <cstdint>
#include <string>

namespace fh {

enum class EventType : uint8_t {
    AddOrder,
    ModifyOrder,
    DeleteOrder,
    Execute,
    Trade,
    Quote,
    Snapshot,
    TradingStatus,
    Instrument,
    Auction,
    Heartbeat,
    Unknown,
};

enum class Side : uint8_t { Buy, Sell, Unknown };

const char* toString(EventType t);
const char* toString(Side s);

// Fixed-point price: integer units of 1e-9 (nanocurrency), matching the
// scale most binary venue protocols (ITCH, SBE) already use internally for
// prices, so decoders convert once at the source instead of every
// downstream consumer repeatedly doing float math on venue-specific
// decimal places (ITCH is 4dp, SBE schemas vary per instrument, etc).
struct MarketEvent {
    EventType type = EventType::Unknown;

    uint64_t sequence = 0;          // venue/session sequence number, for gap detection
    uint64_t exchangeTimestampNs = 0;  // venue-reported event time, nanoseconds since epoch
    uint64_t receiveTimestampNs = 0;   // this process's own receive time - for latency measurement

    std::string symbol;             // normalized symbol (venue-native symbol, mapped upstream if needed)
    uint64_t orderId = 0;           // 0 if not order-scoped (e.g. a Trade print with no book reference)

    int64_t priceNano = 0;          // fixed-point, 1e-9 units - see struct comment
    uint64_t quantity = 0;

    Side side = Side::Unknown;

    std::string venue;              // e.g. "NASDAQ", "CME", "COINBASE"
    std::string feed;               // e.g. "TOTALVIEW_ITCH", "MDP3", "TRADES_WS"

    double price() const { return static_cast<double>(priceNano) / 1e9; }
};

} // namespace fh
