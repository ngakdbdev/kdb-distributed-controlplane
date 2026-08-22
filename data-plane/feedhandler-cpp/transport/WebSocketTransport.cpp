#include "WebSocketTransport.hpp"
#include "WsCodec.hpp"
#include "../core/Sha1Base64.hpp"

#include <algorithm>
#include <cstring>
#include <random>
#include <sstream>

namespace fh {

namespace {
const char* WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"; // RFC 6455 fixed magic string

std::string randomWebSocketKey() {
    uint8_t raw[16];
    std::random_device rd;
    for (auto& b : raw) b = static_cast<uint8_t>(rd() & 0xFF);
    return crypto::base64Encode(raw, sizeof(raw));
}

std::string expectedAcceptFor(const std::string& key) {
    std::string combined = key + WS_GUID;
    uint8_t digest[20];
    crypto::sha1(reinterpret_cast<const uint8_t*>(combined.data()), combined.size(), digest);
    return crypto::base64Encode(digest, 20);
}
} // namespace

WebSocketTransport::WebSocketTransport(WebSocketTransportConfig cfg)
    : cfg_(std::move(cfg)), tcp_(TcpTransportConfig{cfg_.host, cfg_.port}) {
    tcp_.setHandler([this](const uint8_t* d, size_t n, uint64_t ts) { onTcpBytes(d, n, ts); });
    tcp_.setOnConnected([this] {
        handshakeDone_ = false;
        httpBuf_.clear();
        recvBuf_.clear();
        performHandshake();
    });
}

WebSocketTransport::~WebSocketTransport() { stop(); }

bool WebSocketTransport::performHandshake() {
    std::string key = randomWebSocketKey();
    expectedAccept_ = expectedAcceptFor(key);

    std::ostringstream req;
    req << "GET " << cfg_.path << " HTTP/1.1\r\n"
        << "Host: " << cfg_.host << "\r\n"
        << "Upgrade: websocket\r\n"
        << "Connection: Upgrade\r\n"
        << "Sec-WebSocket-Key: " << key << "\r\n"
        << "Sec-WebSocket-Version: 13\r\n"
        << "\r\n";
    std::string r = req.str();
    return tcp_.send(reinterpret_cast<const uint8_t*>(r.data()), r.size());
}

void WebSocketTransport::onTcpBytes(const uint8_t* data, size_t length, uint64_t ts) {
    if (!handshakeDone_) {
        httpBuf_.insert(httpBuf_.end(), data, data + length);
        // Look for the blank line ending the HTTP response header block.
        static const std::string terminator = "\r\n\r\n";
        auto it = std::search(httpBuf_.begin(), httpBuf_.end(), terminator.begin(), terminator.end());
        if (it == httpBuf_.end()) return; // headers not fully received yet

        std::string headers(httpBuf_.begin(), it);
        bool statusOk = headers.find("101") != std::string::npos; // "HTTP/1.1 101 Switching Protocols"
        bool acceptOk = headers.find(expectedAccept_) != std::string::npos;
        size_t headerEnd = static_cast<size_t>(it - httpBuf_.begin()) + terminator.size();
        std::vector<uint8_t> leftover(httpBuf_.begin() + static_cast<long>(headerEnd), httpBuf_.end());
        httpBuf_.clear();

        if (!statusOk || !acceptOk) {
            // Handshake rejected/malformed - drop the connection; TcpTransport's
            // own reconnect loop will retry with a fresh key on the next attempt.
            return;
        }
        handshakeDone_ = true;
        if (cfg_.sendOnConnect && !cfg_.onConnectMessage.empty()) sendText(cfg_.onConnectMessage);
        if (!leftover.empty()) onTcpBytes(leftover.data(), leftover.size(), ts); // process any pipelined frame bytes
        return;
    }

    recvBuf_.insert(recvBuf_.end(), data, data + length);
    while (true) {
        auto frame = ws::tryParseFrame(recvBuf_);
        if (!frame) break;
        switch (frame->opcode) {
            case ws::Opcode::Text:
            case ws::Opcode::Binary:
                if (handler_ && !frame->payload.empty()) handler_(frame->payload.data(), frame->payload.size(), ts);
                break;
            case ws::Opcode::Ping: {
                auto pong = ws::encodeFrame(ws::Opcode::Pong, frame->payload.data(), frame->payload.size());
                tcp_.send(pong.data(), pong.size());
                break;
            }
            default:
                break; // Close/Pong/Continuation: no special handling in this v1 client
        }
    }
}

bool WebSocketTransport::sendText(const std::string& text) {
    if (!handshakeDone_) return false;
    auto frame = ws::encodeTextFrame(text);
    return tcp_.send(frame.data(), frame.size());
}

void WebSocketTransport::start() { tcp_.start(); }
void WebSocketTransport::stop() { tcp_.stop(); }
bool WebSocketTransport::isRunning() const { return tcp_.isRunning(); }
std::string WebSocketTransport::describe() const { return "websocket(" + cfg_.host + cfg_.path + ")"; }

} // namespace fh
