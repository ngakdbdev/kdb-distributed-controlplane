// KdbPublisher.hpp - publishes normalized MarketEvents into a tickerplant
// via the SAME `.u.upd[table;data]` binary-table IPC path
// data-plane/feeds/feed_common.py's Python publisher already uses (see
// KdbIpc.hpp's header comment) - this engine and the existing Python feed
// simulators are interchangeable at the tickerplant's door. A reconnecting
// TCP client under the hood (TcpTransport's own connect/reconnect loop is
// reused directly - a publisher and a transport both just need "a live TCP
// byte pipe that reconnects itself", so there's no reason for two
// implementations of that).
//
// Only Trade-type events publish today - schema.q defines `trade` and
// `risk` tables; there's no order-book table yet for AddOrder/Execute/
// Cancel/Delete events to land in (that's real, additional scope - a book
// table + publish path - not something faked here to look more complete
// than it is).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "../core/EventSink.hpp"
#include "../transport/TcpTransport.hpp"

namespace fh {

struct KdbPublisherConfig {
    std::string host;
    uint16_t port = 0;
    std::string shard;        // stamped into every row's `shard` column - see file header
    std::string credentials;  // "user:password" - tp has no auth today, so any non-empty string satisfies the handshake
    size_t maxBatchSize = 500; // flush() is caller-driven, but onEvent() auto-flushes past this to bound memory on a bursty feed with no active flusher
};

class KdbPublisher : public EventSink {
public:
    explicit KdbPublisher(KdbPublisherConfig cfg);
    ~KdbPublisher() override;

    void start();
    void stop();

    void onEvent(const MarketEvent& event) override;
    // Sends whatever's currently batched as one IPC message. Safe to call
    // with an empty batch (no-op). Returns false if not currently connected
    // (TcpTransport's reconnect loop will bring the connection back; the
    // batch is retried on the next flush() rather than dropped silently -
    // see pending_ below).
    bool flush();

    bool isConnected() const { return tcp_.isConnected(); }
    uint64_t eventsPublished() const { return eventsPublished_; }
    uint64_t flushFailures() const { return flushFailures_; }

private:
    KdbPublisherConfig cfg_;
    TcpTransport tcp_;

    std::vector<int64_t> times_;
    std::vector<std::string> syms_;
    std::vector<double> prices_;
    std::vector<int64_t> sizes_;
    std::vector<std::string> sides_;
    std::vector<std::string> venues_;
    std::vector<std::string> shards_;

    uint64_t eventsPublished_ = 0;
    uint64_t flushFailures_ = 0;

    void clearBatch();
};

} // namespace fh
