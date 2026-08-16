// main.cpp - the feed-handler-cpp engine entry point.
//
// Two modes, chosen by FH_MODE:
//   sim  (default) - runs the synthetic simulators (sim/) on a repeating
//        interval, publishing into a real tickerplant via the exact same
//        decode -> normalize -> publish pipeline production would use.
//        This is the "test everything with local simulated source for
//        data" mode - no real exchange/vendor connectivity involved at all.
//   live - loads a FeedConfig (FH_CONFIG_JSON path) and connects a REAL
//        transport (currently wired for wsjson/websocket - see the
//        registry note in docs/feedhandler-admin.md for what's needed to
//        wire a real UDP/TCP venue's exact connection details once there
//        are real entitlements to test against).
//
// Either way, this process exposes /status on FH_STATUS_PORT (default
// 9200) for control-api's platform health rollup to poll - see
// core/StatusServer.hpp.
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>

#include "core/ConfigLoader.hpp"
#include "core/FeedRegistry.hpp"
#include "core/StatusServer.hpp"
#include "publishers/KdbPublisher.hpp"
#include "recovery/RecoveryManager.hpp"
#include "sim/MoldUdp64Simulator.hpp"
#include "sim/WsJsonSimulator.hpp"
#include "protocols/moldudp64/MoldUdp64Decoder.hpp"
#include "protocols/wsjson/WsJsonDecoder.hpp"
#include "transport/InProcessTransport.hpp"
#include "transport/WebSocketTransport.hpp"

namespace {

std::atomic<bool> g_stop{false};
void onSignal(int) { g_stop = true; }

std::string envOr(const char* name, const std::string& fallback) {
    const char* v = std::getenv(name);
    return v != nullptr ? std::string(v) : fallback;
}

// A MessageSink that decodes protocol messages via a venue adapter AND
// feeds each message's sequence number into a RecoveryManager for gap
// tracking - the standard "decoder -> [sequence tracking + venue
// interpretation]" wiring every feed in this engine uses, sim or real.
class TrackedVenueSink : public fh::MessageSink {
public:
    TrackedVenueSink(fh::IVenueAdapter& adapter, fh::RecoveryManager& recovery)
        : adapter_(adapter), recovery_(recovery) {}
    void onMessage(const fh::DecodedMessage& msg) override {
        if (msg.sequence != 0) recovery_.onSequence(msg.sequence);
        adapter_.onMessage(msg);
    }

private:
    fh::IVenueAdapter& adapter_;
    fh::RecoveryManager& recovery_;
};

std::string buildStatusJson(const fh::RecoveryManager& recovery, const fh::KdbPublisher& publisher,
                            const std::string& mode) {
    std::ostringstream os;
    os << "{"
       << "\"mode\":\"" << mode << "\","
       << "\"feed_state\":\"" << fh::toString(recovery.state()) << "\","
       << "\"expected_sequence\":" << recovery.expected() << ","
       << "\"total_gaps\":" << recovery.totalGaps() << ","
       << "\"kdb_connected\":" << (publisher.isConnected() ? "true" : "false") << ","
       << "\"events_published\":" << publisher.eventsPublished() << ","
       << "\"flush_failures\":" << publisher.flushFailures()
       << "}";
    return os.str();
}

int runSimMode() {
    fh::registerBuiltinVenueAdapters();

    fh::KdbPublisherConfig kdbCfg;
    kdbCfg.host = envOr("FH_KDB_HOST", "tp-s0");
    kdbCfg.port = static_cast<uint16_t>(std::atoi(envOr("FH_KDB_PORT", "5010").c_str()));
    kdbCfg.shard = envOr("FH_SHARD", "s0");
    kdbCfg.credentials = envOr("FH_KDB_CREDENTIALS", "feedhandler");
    fh::KdbPublisher publisher(kdbCfg);
    publisher.start();

    fh::RecoveryManager recovery;
    recovery.setState(fh::FeedState::Live);

    // NASDAQ-style simulated pipeline: MoldUDP64 -> ITCH -> normalize -> publish.
    // ITCH's own Timestamp field is nanoseconds since VENUE-LOCAL MIDNIGHT,
    // not an absolute epoch (see NasdaqItchVenueAdapter's header comment) -
    // trading_day_epoch_ns anchors it to today's midnight so the resulting
    // exchangeTimestampNs is a real, current timestamp. Confirmed live:
    // without this, the raw ITCH offset (e.g. ~09:30:00 -> ~3.4e13 ns) is
    // used AS an absolute epoch value on its own, landing around 1970 -
    // rdb's retention sweep purges rows that old almost immediately, so
    // published trades vanished before they could ever be queried.
    uint64_t todayMidnightNs = (fh::nowNs() / 86400000000000ULL) * 86400000000000ULL;
    auto itchAdapter = fh::FeedRegistry::instance().createVenueAdapter("nasdaq_itch");
    fh::FeedConfig itchCfg; itchCfg.provider = "NASDAQ"; itchCfg.feed = "TOTALVIEW_ITCH_SIM";
    itchCfg.params["trading_day_epoch_ns"] = std::to_string(todayMidnightNs);
    itchAdapter->configure(itchCfg, publisher);
    TrackedVenueSink itchSink(*itchAdapter, recovery);
    fh::MoldUdp64Decoder moldDecoder;
    fh::InProcessTransport moldTransport("nasdaq-sim");
    moldTransport.setHandler([&](const uint8_t* d, size_t n, uint64_t ts) { moldDecoder.decode(d, n, ts, itchSink); });
    moldTransport.start();

    // Coinbase-style simulated pipeline: WS/JSON -> normalize -> publish.
    // This one is real-connectable later (see file header) - the config
    // shape is exactly what GenericWsJsonVenueAdapter needs for the real
    // Coinbase feed, just pointed at a simulator instead of a socket here.
    auto wsAdapter = fh::FeedRegistry::instance().createVenueAdapter("generic_wsjson");
    fh::FeedConfig wsCfg;
    wsCfg.provider = "COINBASE"; wsCfg.feed = "MATCHES_SIM";
    wsCfg.params = {{"filter_field", "type"}, {"filter_value", "match"},
                    {"field_symbol", "product_id"}, {"field_price", "price"},
                    {"field_qty", "size"}, {"field_side", "side"}, {"side_buy_value", "buy"}};
    wsAdapter->configure(wsCfg, publisher);
    fh::RecoveryManager wsRecovery; wsRecovery.setState(fh::FeedState::Live);
    TrackedVenueSink wsSink(*wsAdapter, wsRecovery);
    fh::WsJsonDecoder wsDecoder;
    fh::InProcessTransport wsTransport("coinbase-sim");
    wsTransport.setHandler([&](const uint8_t* d, size_t n, uint64_t ts) { wsDecoder.decode(d, n, ts, wsSink); });
    wsTransport.start();

    uint16_t statusPort = static_cast<uint16_t>(std::atoi(envOr("FH_STATUS_PORT", "9200").c_str()));
    fh::StatusServer status(statusPort, [&] { return buildStatusJson(recovery, publisher, "sim"); });
    status.start();

    int intervalSec = std::atoi(envOr("FH_SIM_INTERVAL_SEC", "5").c_str());
    int dropEveryNth = std::atoi(envOr("FH_SIM_DROP_EVERY_NTH", "0").c_str());
    std::cerr << "feedhandler: sim mode up - publishing synthetic NASDAQ ITCH + Coinbase-shaped trades to "
              << kdbCfg.host << ":" << kdbCfg.port << " every " << intervalSec << "s, status on :" << statusPort
              << std::endl;

    while (!g_stop) {
        fh::sim::MoldUdp64SimConfig moldCfg;
        moldCfg.symbols = {"AAPL", "MSFT"};
        moldCfg.tradesPerSymbol = 3;
        moldCfg.dropEveryNth = static_cast<uint32_t>(dropEveryNth);
        fh::sim::MoldUdp64Simulator moldSim(moldCfg);
        moldSim.run(moldTransport);

        fh::sim::WsJsonSimConfig wsSimCfg;
        wsSimCfg.symbols = {"BTC-USD", "ETH-USD"};
        wsSimCfg.tradesPerSymbol = 3;
        fh::sim::WsJsonSimulator wsSim(wsSimCfg);
        wsSim.run(wsTransport);

        publisher.flush();
        std::this_thread::sleep_for(std::chrono::seconds(intervalSec));
    }

    status.stop();
    publisher.stop();
    return 0;
}

int runLiveMode() {
    fh::registerBuiltinVenueAdapters();

    std::string configPath = envOr("FH_CONFIG_JSON", "");
    if (configPath.empty()) {
        std::cerr << "feedhandler: live mode requires FH_CONFIG_JSON (path to a feed config JSON file)" << std::endl;
        return 1;
    }
    std::ifstream f(configPath);
    if (!f) {
        std::cerr << "feedhandler: could not open FH_CONFIG_JSON at " << configPath << std::endl;
        return 1;
    }
    std::ostringstream ss;
    ss << f.rdbuf();
    std::string secretsJson = envOr("FH_SECRETS_JSON_INLINE", "");

    fh::FeedConfig config;
    if (!fh::loadFeedConfig(ss.str(), secretsJson, config)) {
        std::cerr << "feedhandler: failed to parse FH_CONFIG_JSON" << std::endl;
        return 1;
    }

    auto adapter = fh::FeedRegistry::instance().createVenueAdapter(config.venueAdapter);
    if (!adapter) {
        std::cerr << "feedhandler: unknown venue_adapter '" << config.venueAdapter << "'" << std::endl;
        return 1;
    }

    fh::KdbPublisherConfig kdbCfg;
    kdbCfg.host = envOr("FH_KDB_HOST", "tp-s0");
    kdbCfg.port = static_cast<uint16_t>(std::atoi(envOr("FH_KDB_PORT", "5010").c_str()));
    kdbCfg.shard = envOr("FH_SHARD", "s0");
    fh::KdbPublisher publisher(kdbCfg);
    publisher.start();

    adapter->configure(config, publisher);
    fh::RecoveryManager recovery;
    TrackedVenueSink sink(*adapter, recovery);

    // Only websocket is wired to a real transport in this pass (see file
    // header) - a real UDP/TCP venue needs its exact connection details
    // (multicast group, session credentials) sourced from that venue's
    // own onboarding before it can be pointed at a live feed.
    if (config.transportType != "websocket") {
        std::cerr << "feedhandler: live mode currently wires 'websocket' transport only (got '"
                  << config.transportType << "')" << std::endl;
        return 1;
    }

    fh::WsJsonDecoder decoder;
    fh::WebSocketTransportConfig wsCfg;
    wsCfg.host = config.get("host");
    wsCfg.port = static_cast<uint16_t>(std::atoi(config.get("port", "443").c_str()));
    wsCfg.path = config.get("path", "/");
    wsCfg.sendOnConnect = !config.get("subscribe_message").empty();
    wsCfg.onConnectMessage = config.get("subscribe_message");
    fh::WebSocketTransport transport(wsCfg);
    transport.setHandler([&](const uint8_t* d, size_t n, uint64_t ts) { decoder.decode(d, n, ts, sink); });

    uint16_t statusPort = static_cast<uint16_t>(std::atoi(envOr("FH_STATUS_PORT", "9200").c_str()));
    fh::StatusServer status(statusPort, [&] { return buildStatusJson(recovery, publisher, "live"); });
    status.start();
    transport.start();
    recovery.setState(fh::FeedState::Connecting);

    std::cerr << "feedhandler: live mode up - " << config.provider << "/" << config.feed
              << " via " << transport.describe() << ", status on :" << statusPort << std::endl;

    while (!g_stop) {
        publisher.flush();
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    status.stop();
    transport.stop();
    publisher.stop();
    return 0;
}

} // namespace

int main() {
    std::signal(SIGINT, onSignal);
    std::signal(SIGTERM, onSignal);

    std::string mode = envOr("FH_MODE", "sim");
    if (mode == "live") return runLiveMode();
    return runSimMode();
}
