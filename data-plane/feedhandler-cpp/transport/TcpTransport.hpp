// TcpTransport.hpp - a reconnecting TCP client transport, used by
// SoupBinTCP, FIX, and as the byte-stream underneath WebSocketTransport.
// Handles reconnect-with-backoff itself (see design section "TCP:
// heartbeat, reconnect, sequence recovery") so protocol decoders never
// have to deal with connection lifecycle - they just see bytes resume
// after a reconnect, same as any other gap the SequenceTracker layer
// already handles.
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <thread>
#include "../core/Transport.hpp"

namespace fh {

struct TcpTransportConfig {
    std::string host;
    uint16_t port = 0;
    uint32_t reconnectDelayMs = 2000;
    uint32_t connectTimeoutMs = 5000;
};

class TcpTransport : public ITransport {
public:
    explicit TcpTransport(TcpTransportConfig cfg) : cfg_(std::move(cfg)) {}
    ~TcpTransport() override { stop(); }

    void setHandler(RawPacketHandler handler) override { handler_ = std::move(handler); }
    // Fired on the transport's own thread right after each successful
    // connect (including reconnects), before any bytes are read - the hook
    // point for a session-layer login/handshake message (SoupBinTCP logon,
    // WebSocket's HTTP Upgrade request, a FIX Logon message, ...).
    void setOnConnected(std::function<void()> cb) { onConnected_ = std::move(cb); }
    void start() override;
    void stop() override;
    bool isRunning() const override { return running_; }
    std::string describe() const override;

    // Send bytes out on the current connection (a decoder/session layer
    // needs this for logon/heartbeat/retransmission-request messages -
    // TCP protocols aren't receive-only the way a multicast feed is).
    // Returns false if not currently connected.
    bool send(const uint8_t* data, size_t length);

    bool isConnected() const { return connected_; }
    int reconnectCount() const { return reconnectCount_; }

private:
    void run();
    bool connectOnce();

    TcpTransportConfig cfg_;
    RawPacketHandler handler_;
    std::function<void()> onConnected_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    std::atomic<int> reconnectCount_{-1}; // first successful connect isn't a "re"connect
    std::thread thread_;
    int sock_ = -1;
};

} // namespace fh
