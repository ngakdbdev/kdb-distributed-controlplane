// test_venue_integration.cpp - end-to-end (minus the network/kdb+) tests:
// synthetic bytes -> transport -> protocol decoder -> venue adapter ->
// MarketEvent, the exact pipeline main.cpp wires for real. This is what
// "test everything with local simulated source for data" looks like at
// the unit-test level - sim/ does the same thing at the running-process
// level (see the live verification in the final report).
#include "test_framework.hpp"
#include "../core/EventSink.hpp"
#include "../protocols/moldudp64/MoldUdp64Decoder.hpp"
#include "../protocols/fix/FixMessage.hpp"
#include "../protocols/fix/FixDecoder.hpp"
#include "../venues/NasdaqItchVenueAdapter.hpp"
#include "../venues/GenericFixVenueAdapter.hpp"
#include "../transport/InProcessTransport.hpp"

using namespace fh;

namespace {
struct RecordingSink : EventSink {
    std::vector<MarketEvent> events;
    void onEvent(const MarketEvent& e) override { events.push_back(e); }
};

void putU16(std::vector<uint8_t>& b, uint16_t v) { b.push_back(static_cast<uint8_t>(v >> 8)); b.push_back(static_cast<uint8_t>(v)); }
void putU32(std::vector<uint8_t>& b, uint32_t v) { for (int i = 3; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putU48(std::vector<uint8_t>& b, uint64_t v) { for (int i = 5; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putU64(std::vector<uint8_t>& b, uint64_t v) { for (int i = 7; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putStock(std::vector<uint8_t>& b, const std::string& s) { for (size_t i = 0; i < 8; ++i) b.push_back(i < s.size() ? static_cast<uint8_t>(s[i]) : ' '); }

std::vector<uint8_t> moldPacket(uint64_t seq, const std::vector<std::vector<uint8_t>>& msgs) {
    std::vector<uint8_t> pkt(10, ' ');
    for (int i = 7; i >= 0; --i) pkt.push_back(static_cast<uint8_t>((seq >> (i*8)) & 0xFF));
    putU16(pkt, static_cast<uint16_t>(msgs.size()));
    for (const auto& m : msgs) { putU16(pkt, static_cast<uint16_t>(m.size())); pkt.insert(pkt.end(), m.begin(), m.end()); }
    return pkt;
}
} // namespace

TEST(itch_pipeline_stock_directory_then_add_order_resolves_symbol) {
    MoldUdp64Decoder decoder;
    NasdaqItchVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg; cfg.provider = "NASDAQ"; cfg.feed = "TOTALVIEW_ITCH";
    adapter.configure(cfg, sink);

    InProcessTransport transport;
    transport.setHandler([&](const uint8_t* d, size_t n, uint64_t ts) { decoder.decode(d, n, ts, adapter); });
    transport.start();

    std::vector<uint8_t> directory;
    directory.push_back('R'); putU16(directory, 10); putU16(directory, 0); putU48(directory, 1000);
    putStock(directory, "TSLA");
    directory.push_back('Q'); directory.push_back('N'); putU32(directory, 100);
    directory.resize(39, 0); // real ITCH 5.0 Stock Directory is 39 bytes - see ItchMessages.hpp's length guard
    transport.feed(moldPacket(1, {directory}));

    std::vector<uint8_t> addOrder;
    addOrder.push_back('A'); putU16(addOrder, 10); putU16(addOrder, 0); putU48(addOrder, 2000);
    putU64(addOrder, 42); addOrder.push_back('B'); putU32(addOrder, 300);
    putStock(addOrder, "TSLA"); putU32(addOrder, 2500000);
    transport.feed(moldPacket(2, {addOrder}));

    CHECK_EQ(sink.events.size(), 2u);
    if (sink.events.size() != 2) return; // avoid indexing OOB below if the count assertion above already failed

    CHECK(sink.events[0].type == EventType::Instrument);
    CHECK(sink.events[1].type == EventType::AddOrder);
    CHECK_EQ(sink.events[1].symbol, std::string("TSLA")); // resolved via locate code -> Stock Directory map
    CHECK_EQ(sink.events[1].side, Side::Buy);
    CHECK_EQ(adapter.knownSymbolCount(), 1u);
}

TEST(itch_pipeline_trade_falls_back_to_inline_symbol_when_locate_unknown) {
    MoldUdp64Decoder decoder;
    NasdaqItchVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg;
    adapter.configure(cfg, sink);

    InProcessTransport transport;
    transport.setHandler([&](const uint8_t* d, size_t n, uint64_t ts) { decoder.decode(d, n, ts, adapter); });
    transport.start();

    std::vector<uint8_t> trade;
    trade.push_back('P'); putU16(trade, 99); putU16(trade, 0); putU48(trade, 3000); // locate 99 never announced
    putU64(trade, 7); trade.push_back('S'); putU32(trade, 50);
    putStock(trade, "NFLX"); putU32(trade, 5000000); putU64(trade, 1);
    transport.feed(moldPacket(1, {trade}));

    CHECK_EQ(sink.events.size(), 1u);
    CHECK(sink.events[0].type == EventType::Trade);
    CHECK_EQ(sink.events[0].symbol, std::string("NFLX")); // fallback to the Trade message's own inline stock field
}

TEST(fix_pipeline_market_data_snapshot_produces_quote_and_trade_events) {
    FixDecoder decoder;
    GenericFixVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg; cfg.provider = "GENERIC_FIX";
    adapter.configure(cfg, sink);

    std::string msg = fix::build("FIX.4.4", {
        {fix::Tag::MsgType, fix::MsgType::MarketDataSnapshotFullRefresh},
        {fix::Tag::Symbol, "EURUSD"},
        {fix::Tag::MDEntryType, "0"}, {fix::Tag::MDEntryPx, "1.0850"}, {fix::Tag::MDEntrySize, "1000000"},
        {fix::Tag::MDEntryType, "1"}, {fix::Tag::MDEntryPx, "1.0852"}, {fix::Tag::MDEntrySize, "500000"},
        {fix::Tag::MDEntryType, "2"}, {fix::Tag::MDEntryPx, "1.0851"}, {fix::Tag::MDEntrySize, "250000"},
    });

    struct FixToVenue : MessageSink {
        GenericFixVenueAdapter& a;
        explicit FixToVenue(GenericFixVenueAdapter& adapter) : a(adapter) {}
        void onMessage(const DecodedMessage& m) override { a.onMessage(m); }
    } bridge(adapter);

    decoder.decode(reinterpret_cast<const uint8_t*>(msg.data()), msg.size(), 0, bridge);

    CHECK_EQ(sink.events.size(), 3u);
    CHECK(sink.events[0].type == EventType::Quote);
    CHECK(sink.events[0].side == Side::Buy);
    CHECK(sink.events[1].type == EventType::Quote);
    CHECK(sink.events[1].side == Side::Sell);
    CHECK(sink.events[2].type == EventType::Trade);
    CHECK_EQ(sink.events[2].symbol, std::string("EURUSD"));
}
