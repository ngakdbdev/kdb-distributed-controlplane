#include "GenericFixVenueAdapter.hpp"
#include "../protocols/fix/FixMessage.hpp"
#include <cmath>

namespace fh {

namespace {
int64_t toNanoPrice(const std::string& s) {
    if (s.empty()) return 0;
    return static_cast<int64_t>(std::llround(std::stod(s) * 1e9));
}
} // namespace

void GenericFixVenueAdapter::configure(const FeedConfig& config, EventSink& sink) {
    config_ = config;
    sink_ = &sink;
}

void GenericFixVenueAdapter::onMessage(const DecodedMessage& msg) {
    if (sink_ == nullptr) return;

    fix::FixMessage fixMsg;
    if (fix::parseOne(msg.payload.data(), msg.payload.size(), fixMsg) == 0) return;

    std::string msgType = fixMsg.get(fix::Tag::MsgType);
    if (msgType != fix::MsgType::MarketDataSnapshotFullRefresh && msgType != fix::MsgType::MarketDataIncrementalRefresh) {
        return; // Logon/Heartbeat/TestRequest/etc - session-level, not market data - nothing to normalize
    }

    std::string symbol = fixMsg.get(fix::Tag::Symbol);

    // Walk the flat field list, starting a new MDEntry group each time
    // MDEntryType (269) recurs - see file header.
    bool inEntry = false;
    char entryType = '?';
    std::string entryPx, entrySize;

    auto flush = [&]() {
        if (!inEntry) return;
        MarketEvent ev;
        ev.sequence = msg.sequence;
        ev.exchangeTimestampNs = msg.receiveTimestampNs;
        ev.receiveTimestampNs = msg.receiveTimestampNs;
        ev.symbol = symbol;
        ev.priceNano = toNanoPrice(entryPx);
        ev.quantity = entrySize.empty() ? 0 : static_cast<uint64_t>(std::stod(entrySize));
        ev.venue = config_.provider;
        ev.feed = config_.feed;
        if (entryType == '2') { // FIX MDEntryType '2' = Trade
            ev.type = EventType::Trade;
        } else if (entryType == '0' || entryType == '1') { // '0'=Bid '1'=Offer
            ev.type = EventType::Quote;
            ev.side = entryType == '0' ? Side::Buy : Side::Sell;
        } else {
            return;
        }
        sink_->onEvent(ev);
    };

    for (const auto& [tag, value] : fixMsg.fields) {
        if (tag == fix::Tag::MDEntryType) {
            flush();
            inEntry = true;
            entryType = value.empty() ? '?' : value[0];
            entryPx.clear();
            entrySize.clear();
        } else if (tag == fix::Tag::MDEntryPx) {
            entryPx = value;
        } else if (tag == fix::Tag::MDEntrySize) {
            entrySize = value;
        }
    }
    flush();
}

} // namespace fh
