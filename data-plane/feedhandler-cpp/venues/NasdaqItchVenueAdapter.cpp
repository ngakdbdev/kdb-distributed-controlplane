#include "NasdaqItchVenueAdapter.hpp"
#include "../protocols/itch/ItchMessages.hpp"
#include <cstdlib>

namespace fh {

void NasdaqItchVenueAdapter::configure(const FeedConfig& config, EventSink& sink) {
    config_ = config;
    sink_ = &sink;
    locateToSymbol_.clear();
}

std::string NasdaqItchVenueAdapter::symbolFor(uint16_t locate) const {
    auto it = locateToSymbol_.find(locate);
    return it != locateToSymbol_.end() ? it->second : "";
}

void NasdaqItchVenueAdapter::onMessage(const DecodedMessage& msg) {
    if (sink_ == nullptr || msg.payload.empty()) return;
    const uint8_t* p = msg.payload.data();
    size_t len = msg.payload.size();

    // ITCH timestamps are nanoseconds since venue-local midnight, not an
    // absolute epoch - "trading_day_epoch_ns" (the trading day's midnight,
    // as epoch nanoseconds) lets this adapter produce absolute timestamps
    // without a date library; 0 (unset) means the raw venue-relative value
    // passes through unchanged, useful for tests comparing against spec
    // sample timestamps directly.
    uint64_t dayEpoch = std::strtoull(config_.get("trading_day_epoch_ns", "0").c_str(), nullptr, 10);

    switch (msg.msgType) {
        case 'S': {
            auto e = itch::parseSystemEvent(p, len);
            if (!e) return;
            MarketEvent ev;
            ev.type = EventType::TradingStatus;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + e->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'R': {
            auto d = itch::parseStockDirectory(p, len);
            if (!d) return;
            std::string symbol = itch::trimStock(d->stock);
            locateToSymbol_[d->stockLocate] = symbol;
            MarketEvent ev;
            ev.type = EventType::Instrument;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + d->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.symbol = symbol;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'A': {
            auto a = itch::parseAddOrder(p, len);
            if (!a) return;
            MarketEvent ev;
            ev.type = EventType::AddOrder;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + a->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.symbol = symbolFor(a->stockLocate);
            ev.orderId = a->orderReferenceNumber;
            ev.priceNano = static_cast<int64_t>(a->priceTicks) * 100000; // ITCH price: 4dp -> our 9dp fixed point
            ev.quantity = a->shares;
            ev.side = a->buySellIndicator == 'B' ? Side::Buy : Side::Sell;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'E': {
            auto e = itch::parseOrderExecuted(p, len);
            if (!e) return;
            MarketEvent ev;
            ev.type = EventType::Execute;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + e->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.orderId = e->orderReferenceNumber;
            ev.quantity = e->executedShares;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'X': {
            auto x = itch::parseOrderCancel(p, len);
            if (!x) return;
            MarketEvent ev;
            ev.type = EventType::ModifyOrder;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + x->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.orderId = x->orderReferenceNumber;
            ev.quantity = x->cancelledShares;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'D': {
            auto d = itch::parseOrderDelete(p, len);
            if (!d) return;
            MarketEvent ev;
            ev.type = EventType::DeleteOrder;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + d->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.orderId = d->orderReferenceNumber;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        case 'P': {
            auto t = itch::parseTrade(p, len);
            if (!t) return;
            MarketEvent ev;
            ev.type = EventType::Trade;
            ev.sequence = msg.sequence;
            ev.exchangeTimestampNs = dayEpoch + t->timestampNs;
            ev.receiveTimestampNs = msg.receiveTimestampNs;
            ev.symbol = symbolFor(t->stockLocate).empty() ? itch::trimStock(t->stock) : symbolFor(t->stockLocate);
            ev.orderId = t->orderReferenceNumber;
            ev.priceNano = static_cast<int64_t>(t->priceTicks) * 100000;
            ev.quantity = t->shares;
            ev.side = t->buySellIndicator == 'B' ? Side::Buy : Side::Sell;
            ev.venue = config_.provider;
            ev.feed = config_.feed;
            sink_->onEvent(ev);
            return;
        }
        default:
            return; // message type not in this codebase's implemented subset - see file header
    }
}

} // namespace fh
