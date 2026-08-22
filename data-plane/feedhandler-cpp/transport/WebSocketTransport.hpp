// WebSocketTransport.hpp - a WebSocket client transport (RFC 6455) built on
// top of TcpTransport + WsCodec. Does the HTTP Upgrade handshake itself,
// then hands each fully-reassembled text/binary message's payload to the
// registered handler as one "packet" - exactly like UdpTransport does per
// datagram - so a WSJSON protocol decoder never has to know it's running
// over WebSocket rather than raw UDP. Handles ping/pong transparently
// (replies pong automatically); a close frame stops the transport, and the
// underlying TcpTransport's reconnect loop brings it back with a fresh
// handshake.
#pragma once

#include <atomic>
#include <string>
#include <vector>

#include "TcpTransport.hpp"
#include "../core/Transport.hpp"

namespace fh {

struct WebSocketTransportConfig {
    std::string host;
    uint16_t port = 443;
    std::string path = "/";
    bool sendOnConnect = false;
    std::string onConnectMessage; // e.g. a subscribe JSON payload, sent right after the handshake completes
};

class WebSocketTransport : public ITransport {
public:
    explicit WebSocketTransport(WebSocketTransportConfig cfg);
    ~WebSocketTransport() override;

    void setHandler(RawPacketHandler handler) override { handler_ = std::move(handler); }
    void start() override;
    void stop() override;
    bool isRunning() const override;
    std::string describe() const override;

    bool sendText(const std::string& text);
    bool isHandshakeComplete() const { return handshakeDone_; }

private:
    void onTcpBytes(const uint8_t* data, size_t length, uint64_t ts);
    bool performHandshake();

    WebSocketTransportConfig cfg_;
    RawPacketHandler handler_;
    TcpTransport tcp_;
    std::vector<uint8_t> recvBuf_;
    std::vector<uint8_t> httpBuf_;
    std::atomic<bool> handshakeDone_{false};
    std::string expectedAccept_;
};

} // namespace fh
