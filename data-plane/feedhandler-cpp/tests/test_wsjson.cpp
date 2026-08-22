#include "test_framework.hpp"
#include "../core/EventSink.hpp"
#include "../core/Json.hpp"
#include "../protocols/wsjson/WsJsonDecoder.hpp"
#include "../venues/GenericWsJsonVenueAdapter.hpp"

using namespace fh;

namespace {
struct RecordingSink : EventSink {
    std::vector<MarketEvent> events;
    void onEvent(const MarketEvent& e) override { events.push_back(e); }
};
} // namespace

TEST(json_parser_handles_nested_objects_and_arrays) {
    json::Value v;
    bool ok = json::parse(R"({"a":1,"b":"x","c":[1,2,3],"d":{"e":true,"f":null}})", v);
    CHECK(ok);
    CHECK(v.isObject());
    CHECK_EQ(v.find("a")->asDouble(), 1.0);
    CHECK_EQ(v.find("b")->asString(), std::string("x"));
    CHECK(v.find("c")->isArray());
    CHECK_EQ(v.find("c")->arrayValue.size(), 3u);
}

TEST(json_parser_handles_string_escapes) {
    json::Value v;
    bool ok = json::parse(R"({"s":"line1\nline2\ttab"})", v);
    CHECK(ok);
    CHECK_EQ(v.find("s")->asString(), std::string("line1\nline2\ttab"));
}

TEST(json_parser_rejects_malformed_input) {
    json::Value v;
    CHECK(!json::parse("{not valid json", v));
}

TEST(coinbase_shaped_trade_normalizes_to_market_event) {
    GenericWsJsonVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg;
    cfg.provider = "COINBASE";
    cfg.params = {{"filter_field", "type"}, {"filter_value", "match"},
                 {"field_symbol", "product_id"}, {"field_price", "price"},
                 {"field_qty", "size"}, {"field_side", "side"}, {"side_buy_value", "buy"}};
    adapter.configure(cfg, sink);

    std::string json = R"({"type":"match","product_id":"BTC-USD","price":"50123.45","size":"0.25","side":"buy"})";
    DecodedMessage msg;
    msg.payload.assign(json.begin(), json.end());
    msg.receiveTimestampNs = 999;

    adapter.onMessage(msg);

    CHECK_EQ(sink.events.size(), 1u);
    CHECK_EQ(sink.events[0].symbol, std::string("BTC-USD"));
    CHECK_EQ(sink.events[0].side, Side::Buy);
    CHECK(sink.events[0].price() > 50123.4 && sink.events[0].price() < 50123.5);
}

TEST(coinbase_fractional_crypto_size_rounds_instead_of_truncating_to_zero) {
    // Regression: a naive (uint64_t) cast on a fractional trade size like
    // 0.25 BTC truncates to 0, silently zeroing most real crypto trades -
    // confirmed live against the real tickerplant before this fix (see
    // GenericWsJsonVenueAdapter.cpp's comment). 0.6 rounds to 1, not 0.
    GenericWsJsonVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg;
    cfg.params = {{"filter_field", "type"}, {"filter_value", "match"},
                 {"field_symbol", "product_id"}, {"field_price", "price"}, {"field_qty", "size"}};
    adapter.configure(cfg, sink);

    std::string json = R"({"type":"match","product_id":"BTC-USD","price":"50000","size":"0.6"})";
    DecodedMessage msg;
    msg.payload.assign(json.begin(), json.end());
    adapter.onMessage(msg);

    CHECK_EQ(sink.events.size(), 1u);
    if (sink.events.empty()) return;
    CHECK_EQ(sink.events[0].quantity, 1ULL);
}

TEST(wsjson_adapter_ignores_non_matching_message_types) {
    GenericWsJsonVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg;
    cfg.params = {{"filter_field", "type"}, {"filter_value", "match"}};
    adapter.configure(cfg, sink);

    std::string json = R"({"type":"heartbeat"})";
    DecodedMessage msg;
    msg.payload.assign(json.begin(), json.end());
    adapter.onMessage(msg);

    CHECK_EQ(sink.events.size(), 0u);
}

TEST(wsjson_adapter_handles_array_shaped_records_via_data_path) {
    GenericWsJsonVenueAdapter adapter;
    RecordingSink sink;
    FeedConfig cfg;
    cfg.params = {{"filter_field", "channel"}, {"filter_value", "trade"}, {"data_path", "data"},
                 {"field_symbol", "symbol"}, {"field_price", "price"}, {"field_qty", "qty"}};
    adapter.configure(cfg, sink);

    std::string json = R"({"channel":"trade","data":[{"symbol":"ETH-USD","price":"3000.5","qty":"2"},{"symbol":"ETH-USD","price":"3001.0","qty":"1"}]})";
    DecodedMessage msg;
    msg.payload.assign(json.begin(), json.end());
    adapter.onMessage(msg);

    CHECK_EQ(sink.events.size(), 2u);
}
