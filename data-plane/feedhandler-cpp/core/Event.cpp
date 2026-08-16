#include "Event.hpp"

namespace fh {

const char* toString(EventType t) {
    switch (t) {
        case EventType::AddOrder: return "AddOrder";
        case EventType::ModifyOrder: return "ModifyOrder";
        case EventType::DeleteOrder: return "DeleteOrder";
        case EventType::Execute: return "Execute";
        case EventType::Trade: return "Trade";
        case EventType::Quote: return "Quote";
        case EventType::Snapshot: return "Snapshot";
        case EventType::TradingStatus: return "TradingStatus";
        case EventType::Instrument: return "Instrument";
        case EventType::Auction: return "Auction";
        case EventType::Heartbeat: return "Heartbeat";
        default: return "Unknown";
    }
}

const char* toString(Side s) {
    switch (s) {
        case Side::Buy: return "Buy";
        case Side::Sell: return "Sell";
        default: return "Unknown";
    }
}

} // namespace fh
