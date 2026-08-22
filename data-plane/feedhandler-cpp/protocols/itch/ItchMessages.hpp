// ItchMessages.hpp - NASDAQ TotalView-ITCH 5.0 message parsing (the core
// order-book + trade message set: System Event, Stock Directory, Add
// Order, Order Executed, Order Cancel, Order Delete, Trade). Field offsets
// below are taken directly from Nasdaq's public TotalView-ITCH 5.0
// specification.
//
// Deliberately pure parsing functions, not an IProtocolDecoder: an ITCH
// message has no framing/sequence of its own - MoldUdp64Decoder (UDP) or
// SoupBinTcpDecoder (TCP) has already carved it out of the byte stream and
// assigned it a sequence number by the time these functions ever see it.
// What's ITCH-specific is INTERPRETING the payload, which is exactly what
// a VenueAdapter does (see venues/NasdaqItchVenueAdapter) - these
// functions are the shared vocabulary it's built on, kept separate so
// they're independently unit-testable against known spec byte sequences
// without needing a venue adapter, transport, or kdb+ involved at all.
//
// Every ITCH message's own Timestamp field is 6 bytes (48-bit), nanoseconds
// since midnight (venue-local trading day) - NOT since Unix epoch. Venue
// adapters combine this with the trading day's date to get an absolute
// timestamp; these parse functions just expose the raw 48-bit value.
#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string>

namespace fh::itch {

inline uint16_t u16(const uint8_t* p) { return static_cast<uint16_t>((p[0] << 8) | p[1]); }
inline uint32_t u32(const uint8_t* p) {
    return (static_cast<uint32_t>(p[0]) << 24) | (static_cast<uint32_t>(p[1]) << 16) |
           (static_cast<uint32_t>(p[2]) << 8) | p[3];
}
inline uint64_t u48(const uint8_t* p) { // 6-byte big-endian timestamp field
    uint64_t v = 0;
    for (int i = 0; i < 6; ++i) v = (v << 8) | p[i];
    return v;
}
inline uint64_t u64(const uint8_t* p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v = (v << 8) | p[i];
    return v;
}

struct SystemEvent {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    char eventCode; // 'O'=Start of Messages 'S'=Start of System hours 'Q'=Start of Market hours
                    // 'M'=End of Market hours 'E'=End of System hours 'C'=End of Messages
};
inline std::optional<SystemEvent> parseSystemEvent(const uint8_t* p, size_t len) {
    // Type(1) + StockLocate(2) + TrackingNumber(2) + Timestamp(6) + EventCode(1) = 12 bytes.
    // Confirmed live: this guard previously said 14 (two bytes too many),
    // silently rejecting every real, correctly-sized System Event message.
    if (len < 12 || p[0] != 'S') return std::nullopt;
    return SystemEvent{u16(p + 1), u16(p + 3), u48(p + 5), static_cast<char>(p[11])};
}

struct StockDirectory {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    std::array<char, 8> stock; // space-padded ASCII ticker
    char marketCategory;
    char financialStatusIndicator;
    uint32_t roundLotSize;
};
inline std::optional<StockDirectory> parseStockDirectory(const uint8_t* p, size_t len) {
    if (len < 39 || p[0] != 'R') return std::nullopt;
    StockDirectory d{};
    d.stockLocate = u16(p + 1);
    d.trackingNumber = u16(p + 3);
    d.timestampNs = u48(p + 5);
    std::memcpy(d.stock.data(), p + 11, 8);
    d.marketCategory = static_cast<char>(p[19]);
    d.financialStatusIndicator = static_cast<char>(p[20]);
    d.roundLotSize = u32(p + 21);
    return d;
}

struct AddOrder {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    uint64_t orderReferenceNumber;
    char buySellIndicator; // 'B' or 'S'
    uint32_t shares;
    std::array<char, 8> stock;
    uint32_t priceTicks; // 4 decimal places implied, per spec (price = priceTicks / 10000.0)
};
inline std::optional<AddOrder> parseAddOrder(const uint8_t* p, size_t len) {
    if (len < 36 || p[0] != 'A') return std::nullopt;
    AddOrder a{};
    a.stockLocate = u16(p + 1);
    a.trackingNumber = u16(p + 3);
    a.timestampNs = u48(p + 5);
    a.orderReferenceNumber = u64(p + 11);
    a.buySellIndicator = static_cast<char>(p[19]);
    a.shares = u32(p + 20);
    std::memcpy(a.stock.data(), p + 24, 8);
    a.priceTicks = u32(p + 32);
    return a;
}

struct OrderExecuted {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    uint64_t orderReferenceNumber;
    uint32_t executedShares;
    uint64_t matchNumber;
};
inline std::optional<OrderExecuted> parseOrderExecuted(const uint8_t* p, size_t len) {
    if (len < 31 || p[0] != 'E') return std::nullopt;
    return OrderExecuted{u16(p + 1), u16(p + 3), u48(p + 5), u64(p + 11), u32(p + 19), u64(p + 23)};
}

struct OrderCancel {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    uint64_t orderReferenceNumber;
    uint32_t cancelledShares;
};
inline std::optional<OrderCancel> parseOrderCancel(const uint8_t* p, size_t len) {
    if (len < 23 || p[0] != 'X') return std::nullopt;
    return OrderCancel{u16(p + 1), u16(p + 3), u48(p + 5), u64(p + 11), u32(p + 19)};
}

struct OrderDelete {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    uint64_t orderReferenceNumber;
};
inline std::optional<OrderDelete> parseOrderDelete(const uint8_t* p, size_t len) {
    if (len < 19 || p[0] != 'D') return std::nullopt;
    return OrderDelete{u16(p + 1), u16(p + 3), u48(p + 5), u64(p + 11)};
}

struct Trade {
    uint16_t stockLocate, trackingNumber;
    uint64_t timestampNs;
    uint64_t orderReferenceNumber;
    char buySellIndicator;
    uint32_t shares;
    std::array<char, 8> stock;
    uint32_t priceTicks;
    uint64_t matchNumber;
};
inline std::optional<Trade> parseTrade(const uint8_t* p, size_t len) {
    if (len < 44 || p[0] != 'P') return std::nullopt;
    Trade t{};
    t.stockLocate = u16(p + 1);
    t.trackingNumber = u16(p + 3);
    t.timestampNs = u48(p + 5);
    t.orderReferenceNumber = u64(p + 11);
    t.buySellIndicator = static_cast<char>(p[19]);
    t.shares = u32(p + 20);
    std::memcpy(t.stock.data(), p + 24, 8);
    t.priceTicks = u32(p + 32);
    t.matchNumber = u64(p + 36);
    return t;
}

inline std::string trimStock(const std::array<char, 8>& stock) {
    size_t n = 8;
    while (n > 0 && stock[n - 1] == ' ') --n;
    return std::string(stock.data(), n);
}

} // namespace fh::itch
