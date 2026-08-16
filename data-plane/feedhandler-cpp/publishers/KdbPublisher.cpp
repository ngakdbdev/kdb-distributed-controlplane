#include "KdbPublisher.hpp"
#include "KdbIpc.hpp"

namespace fh {

namespace {
constexpr const char* TRADE_COLUMNS[] = {"time", "sym", "price", "size", "side", "venue", "shard"};
}

KdbPublisher::KdbPublisher(KdbPublisherConfig cfg)
    : cfg_(std::move(cfg)), tcp_(TcpTransportConfig{cfg_.host, cfg_.port}) {
    // Handshake happens once per (re)connect, matching how a real q client
    // library authenticates before sending anything else - see KdbIpc.hpp's
    // handshakeRequest() comment on why this goes through send()/setHandler()
    // rather than a blocking recv() (TcpTransport doesn't expose a raw fd).
    tcp_.setOnConnected([this] {
        std::string hs = kdb::handshakeRequest(cfg_.credentials);
        tcp_.send(reinterpret_cast<const uint8_t*>(hs.data()), hs.size());
    });
    // The only bytes ever expected back from a tickerplant on this
    // connection are the single handshake capability byte - `.u.upd` is
    // fire-and-forget async, tp never replies to it. Anything received is
    // therefore ignored past the handshake; there's nothing to parse.
    tcp_.setHandler([](const uint8_t*, size_t, uint64_t) {});
}

KdbPublisher::~KdbPublisher() { stop(); }

void KdbPublisher::start() { tcp_.start(); }
void KdbPublisher::stop() { tcp_.stop(); }

void KdbPublisher::clearBatch() {
    times_.clear(); syms_.clear(); prices_.clear(); sizes_.clear();
    sides_.clear(); venues_.clear(); shards_.clear();
}

void KdbPublisher::onEvent(const MarketEvent& event) {
    if (event.type != EventType::Trade) return; // see file header - only Trade has a table to land in today

    uint64_t ts = event.exchangeTimestampNs != 0 ? event.exchangeTimestampNs : event.receiveTimestampNs;
    times_.push_back(kdb::toKdbTimestampNs(ts));
    syms_.push_back(event.symbol);
    prices_.push_back(event.price());
    sizes_.push_back(static_cast<int64_t>(event.quantity));
    sides_.push_back(event.side == Side::Buy ? "buy" : (event.side == Side::Sell ? "sell" : ""));
    venues_.push_back(event.venue);
    shards_.push_back(cfg_.shard);

    if (times_.size() >= cfg_.maxBatchSize) flush();
}

bool KdbPublisher::flush() {
    if (times_.empty()) return true;
    if (!tcp_.isConnected()) { flushFailures_++; return false; }

    std::vector<std::vector<uint8_t>> columns = {
        kdb::Writer::timestampVector(times_),
        kdb::Writer::symbolVector(syms_),
        kdb::Writer::floatVector(prices_),
        kdb::Writer::longVector(sizes_),
        kdb::Writer::symbolVector(sides_),
        kdb::Writer::symbolVector(venues_),
        kdb::Writer::symbolVector(shards_),
    };
    std::vector<std::string> colNames(std::begin(TRADE_COLUMNS), std::end(TRADE_COLUMNS));
    auto tableBytes = kdb::Writer::table(colNames, columns);
    auto msg = kdb::Writer::asyncUpdMessage("trade", tableBytes);

    bool ok = tcp_.send(msg.data(), msg.size());
    if (ok) {
        eventsPublished_ += times_.size();
    } else {
        flushFailures_++;
    }
    clearBatch();
    return ok;
}

} // namespace fh
