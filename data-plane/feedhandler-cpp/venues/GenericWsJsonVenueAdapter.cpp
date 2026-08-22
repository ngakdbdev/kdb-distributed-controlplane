#include "GenericWsJsonVenueAdapter.hpp"
#include "../core/Json.hpp"
#include <cmath>

namespace fh {

namespace {
int64_t toNanoPrice(double price) { return static_cast<int64_t>(std::llround(price * 1e9)); }
} // namespace

void GenericWsJsonVenueAdapter::configure(const FeedConfig& config, EventSink& sink) {
    config_ = config;
    sink_ = &sink;
}

void GenericWsJsonVenueAdapter::onMessage(const DecodedMessage& msg) {
    if (sink_ == nullptr || msg.payload.empty()) return;

    json::Value root;
    std::string text(reinterpret_cast<const char*>(msg.payload.data()), msg.payload.size());
    if (!json::parse(text, root) || !root.isObject()) return;

    std::string filterField = config_.get("filter_field");
    std::string filterValue = config_.get("filter_value");
    if (!filterField.empty()) {
        const json::Value* fv = root.find(filterField);
        if (fv == nullptr || fv->asString() != filterValue) return;
    }

    const json::Value* recordHolder = &root;
    std::string dataPath = config_.get("data_path");
    if (!dataPath.empty()) {
        recordHolder = root.find(dataPath);
        if (recordHolder == nullptr) return;
    }

    std::string fieldSymbol = config_.get("field_symbol", "symbol");
    std::string fieldPrice = config_.get("field_price", "price");
    std::string fieldQty = config_.get("field_qty", "size");
    std::string fieldSide = config_.get("field_side", "side");
    std::string buyValue = config_.get("side_buy_value", "buy");

    auto emitOne = [&](const json::Value& rec) {
        if (!rec.isObject()) return;
        const json::Value* symV = rec.find(fieldSymbol);
        const json::Value* priceV = rec.find(fieldPrice);
        if (symV == nullptr || priceV == nullptr) return;

        MarketEvent ev;
        ev.type = EventType::Trade;
        ev.sequence = msg.sequence;
        ev.exchangeTimestampNs = msg.receiveTimestampNs; // vendor timestamp field/format varies too much to generalize here - receive time is a faithful proxy for a public retail feed's own latency characteristics
        ev.receiveTimestampNs = msg.receiveTimestampNs;
        ev.symbol = symV->asString();
        ev.priceNano = toNanoPrice(priceV->asDouble());
        // MarketEvent.quantity is a whole-unit count (matches schema.q's
        // `size` column, a q long) - crypto sizes are fractional (0.01 BTC
        // is an ordinary trade), and a naive cast truncates toward zero,
        // silently zeroing most real crypto trades. Round to nearest
        // instead - the SAME disclosed, deliberate precision loss
        // data-plane/feeds/providers/normalize.py's _crypto_size() already
        // documents for the Python feeds (no lot-size/contract-multiplier
        // concept exists in this schema to rescale by consistently); this
        // mirrors that behavior rather than inventing a different one.
        if (const json::Value* qtyV = rec.find(fieldQty)) {
            double q = qtyV->asDouble();
            ev.quantity = q > 0 ? static_cast<uint64_t>(std::llround(q)) : 0;
        }
        if (const json::Value* sideV = rec.find(fieldSide)) {
            ev.side = sideV->asString() == buyValue ? Side::Buy : Side::Sell;
        }
        ev.venue = config_.provider;
        ev.feed = config_.feed;
        sink_->onEvent(ev);
    };

    if (recordHolder->isArray()) {
        for (const auto& element : recordHolder->arrayValue) emitOne(element);
    } else {
        emitOne(*recordHolder);
    }
}

} // namespace fh
