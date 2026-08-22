#include "test_framework.hpp"
#include "../protocols/itch/ItchMessages.hpp"

using namespace fh::itch;

namespace {
void putU16(std::vector<uint8_t>& b, uint16_t v) { b.push_back(static_cast<uint8_t>(v >> 8)); b.push_back(static_cast<uint8_t>(v)); }
void putU32(std::vector<uint8_t>& b, uint32_t v) { for (int i = 3; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putU48(std::vector<uint8_t>& b, uint64_t v) { for (int i = 5; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putU64(std::vector<uint8_t>& b, uint64_t v) { for (int i = 7; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putStock(std::vector<uint8_t>& b, const std::string& s) { for (size_t i = 0; i < 8; ++i) b.push_back(i < s.size() ? static_cast<uint8_t>(s[i]) : ' '); }
} // namespace

TEST(itch_system_event_accepts_correctly_sized_12_byte_message) {
    // Regression: the length guard previously required 14 bytes for a
    // message that's actually 12 bytes wide (Type+Locate+Tracking+Timestamp
    // +EventCode = 1+2+2+6+1), silently rejecting every real System Event
    // message - confirmed live, this correctly-built 12-byte message was
    // exactly what a real feed sends and used to fail to parse.
    std::vector<uint8_t> m;
    m.push_back('S');
    putU16(m, 1); putU16(m, 0); putU48(m, 34200000000000ULL);
    m.push_back('O');
    CHECK_EQ(m.size(), static_cast<size_t>(12));

    auto e = parseSystemEvent(m.data(), m.size());
    CHECK(e.has_value());
    if (e) {
        CHECK_EQ(e->eventCode, 'O');
        CHECK_EQ(e->timestampNs, 34200000000000ULL);
    }
}

TEST(itch_add_order_round_trips_all_fields) {
    std::vector<uint8_t> m;
    m.push_back('A');
    putU16(m, 42);              // stock locate
    putU16(m, 7);                // tracking number
    putU48(m, 34200123456789ULL); // timestamp
    putU64(m, 999888777);        // order ref
    m.push_back('B');            // buy
    putU32(m, 500);              // shares
    putStock(m, "AAPL");
    putU32(m, 1825000);          // price ticks (4dp) -> 182.50

    auto a = parseAddOrder(m.data(), m.size());
    CHECK(a.has_value());
    CHECK_EQ(a->stockLocate, static_cast<uint16_t>(42));
    CHECK_EQ(a->timestampNs, 34200123456789ULL);
    CHECK_EQ(a->orderReferenceNumber, 999888777ULL);
    CHECK_EQ(a->buySellIndicator, 'B');
    CHECK_EQ(a->shares, static_cast<uint32_t>(500));
    CHECK_EQ(trimStock(a->stock), std::string("AAPL"));
    CHECK_EQ(a->priceTicks, static_cast<uint32_t>(1825000));
}

TEST(itch_add_order_rejects_wrong_message_type) {
    std::vector<uint8_t> m(36, 0);
    m[0] = 'X'; // not 'A'
    CHECK(!parseAddOrder(m.data(), m.size()).has_value());
}

TEST(itch_add_order_rejects_truncated_payload) {
    std::vector<uint8_t> m = {'A', 0, 1};
    CHECK(!parseAddOrder(m.data(), m.size()).has_value());
}

TEST(itch_stock_directory_extracts_symbol) {
    std::vector<uint8_t> m;
    m.push_back('R');
    putU16(m, 5); putU16(m, 0); putU48(m, 1000);
    putStock(m, "MSFT");
    m.push_back('Q'); m.push_back('N');
    putU32(m, 100);
    m.resize(39, 0); // real ITCH 5.0 Stock Directory is 39 bytes - trailing fields (issue classification etc.) unused by this parser but still occupy the wire

    auto d = parseStockDirectory(m.data(), m.size());
    CHECK(d.has_value());
    CHECK_EQ(trimStock(d->stock), std::string("MSFT"));
    CHECK_EQ(d->stockLocate, static_cast<uint16_t>(5));
}

TEST(itch_trade_message_round_trips) {
    std::vector<uint8_t> m;
    m.push_back('P');
    putU16(m, 1); putU16(m, 0); putU48(m, 2000);
    putU64(m, 55);
    m.push_back('S');
    putU32(m, 200);
    putStock(m, "GOOG");
    putU32(m, 1400000);
    putU64(m, 77777);

    auto t = parseTrade(m.data(), m.size());
    CHECK(t.has_value());
    CHECK_EQ(t->shares, static_cast<uint32_t>(200));
    CHECK_EQ(trimStock(t->stock), std::string("GOOG"));
    CHECK_EQ(t->matchNumber, 77777ULL);
}

TEST(itch_order_delete_minimal_fields) {
    std::vector<uint8_t> m;
    m.push_back('D');
    putU16(m, 3); putU16(m, 0); putU48(m, 500);
    putU64(m, 12345);

    auto d = parseOrderDelete(m.data(), m.size());
    CHECK(d.has_value());
    CHECK_EQ(d->orderReferenceNumber, 12345ULL);
}

TEST(itch_trim_stock_strips_trailing_spaces) {
    std::array<char, 8> stock{'A', 'B', 'C', ' ', ' ', ' ', ' ', ' '};
    CHECK_EQ(trimStock(stock), std::string("ABC"));
}
