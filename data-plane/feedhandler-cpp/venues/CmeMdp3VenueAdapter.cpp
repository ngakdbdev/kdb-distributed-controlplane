#include "CmeMdp3VenueAdapter.hpp"

namespace fh {

sbe::Schema buildExampleMdp3Schema() {
    sbe::Schema schema;

    sbe::MessageTemplate trade;
    trade.templateId = CmeMdp3TemplateId::Trade;
    trade.name = "Trade";
    trade.blockLength = 17;
    trade.fields = {
        {"instrumentId", sbe::FieldType::UInt32, 0, 4},
        {"price", sbe::FieldType::Int64, 4, 8},      // fixed-point, 9 decimal places (matches MarketEvent.priceNano)
        {"quantity", sbe::FieldType::UInt32, 12, 4},
        {"side", sbe::FieldType::Char, 16, 1},
    };
    schema.addTemplate(trade);

    sbe::MessageTemplate addOrder;
    addOrder.templateId = CmeMdp3TemplateId::AddOrder;
    addOrder.name = "AddOrder";
    addOrder.blockLength = 25;
    addOrder.fields = {
        {"orderId", sbe::FieldType::UInt64, 0, 8},
        {"instrumentId", sbe::FieldType::UInt32, 8, 4},
        {"price", sbe::FieldType::Int64, 12, 8},
        {"quantity", sbe::FieldType::UInt32, 20, 4},
        {"side", sbe::FieldType::Char, 24, 1},
    };
    schema.addTemplate(addOrder);

    return schema;
}

void CmeMdp3VenueAdapter::configure(const FeedConfig& config, EventSink& sink) {
    config_ = config;
    sink_ = &sink;
}

void CmeMdp3VenueAdapter::onMessage(const DecodedMessage& msg) {
    if (sink_ == nullptr) return;
    sbe::SbeMessage decoded;
    size_t consumed = decoder_.decodeOne(msg.payload.data(), msg.payload.size(), decoded);
    if (consumed == 0) return; // unrecognized templateId, or truncated - drop, matching every other decoder's error handling

    uint64_t instrumentId = decoded.getUInt("instrumentId");
    std::string symbol = config_.get("instrument_" + std::to_string(instrumentId));
    if (symbol.empty()) symbol = config_.provider + "#" + std::to_string(instrumentId);

    char sideChar = decoded.getString("side").empty() ? '?' : decoded.getString("side")[0];
    int64_t priceNano = decoded.getInt("price"); // schema is already fixed-point 1e-9, matching MarketEvent directly

    MarketEvent ev;
    ev.sequence = msg.sequence;
    ev.exchangeTimestampNs = msg.receiveTimestampNs; // no venue-timestamp field in the example schema - see file header
    ev.receiveTimestampNs = msg.receiveTimestampNs;
    ev.symbol = symbol;
    ev.priceNano = priceNano;
    ev.quantity = decoded.getUInt("quantity");
    ev.side = sideChar == 'B' ? Side::Buy : (sideChar == 'S' ? Side::Sell : Side::Unknown);
    ev.venue = config_.provider;
    ev.feed = config_.feed;

    if (decoded.templateId == CmeMdp3TemplateId::Trade) {
        ev.type = EventType::Trade;
    } else if (decoded.templateId == CmeMdp3TemplateId::AddOrder) {
        ev.type = EventType::AddOrder;
        ev.orderId = decoded.getUInt("orderId");
    } else {
        return;
    }
    sink_->onEvent(ev);
}

} // namespace fh
