// WsCodec.hpp - RFC 6455 WebSocket framing, kept separate from the socket
// I/O (WebSocketTransport) specifically so it's unit-testable as pure byte
// transforms with no networking involved - the same "decoder logic is
// testable without a live connection" principle as every protocol decoder
// in protocols/. Client-side only: outgoing frames are always masked (a
// MUST per the RFC), incoming frames are assumed unmasked (servers never
// mask per the RFC - a masked server frame is treated as a protocol error).
#pragma once

#include <cstdint>
#include <optional>
#include <random>
#include <string>
#include <vector>

namespace fh::ws {

enum class Opcode : uint8_t { Continuation = 0x0, Text = 0x1, Binary = 0x2, Close = 0x8, Ping = 0x9, Pong = 0xA };

struct Frame {
    Opcode opcode = Opcode::Text;
    bool fin = true;
    std::vector<uint8_t> payload;
};

// Encodes one client->server frame, masked per RFC 6455 section 5.3.
inline std::vector<uint8_t> encodeFrame(Opcode opcode, const uint8_t* data, size_t len) {
    std::vector<uint8_t> out;
    out.push_back(0x80 | static_cast<uint8_t>(opcode)); // FIN=1, single-frame messages only (sufficient for subscribe/control messages)

    uint8_t maskBit = 0x80;
    if (len <= 125) {
        out.push_back(maskBit | static_cast<uint8_t>(len));
    } else if (len <= 0xFFFF) {
        out.push_back(maskBit | 126);
        out.push_back(static_cast<uint8_t>((len >> 8) & 0xFF));
        out.push_back(static_cast<uint8_t>(len & 0xFF));
    } else {
        out.push_back(maskBit | 127);
        for (int i = 7; i >= 0; --i) out.push_back(static_cast<uint8_t>((static_cast<uint64_t>(len) >> (i * 8)) & 0xFF));
    }

    uint8_t maskKey[4];
    std::random_device rd;
    for (auto& b : maskKey) b = static_cast<uint8_t>(rd() & 0xFF);
    out.insert(out.end(), maskKey, maskKey + 4);

    size_t base = out.size();
    out.resize(base + len);
    for (size_t i = 0; i < len; ++i) out[base + i] = data[i] ^ maskKey[i % 4];
    return out;
}

inline std::vector<uint8_t> encodeTextFrame(const std::string& text) {
    return encodeFrame(Opcode::Text, reinterpret_cast<const uint8_t*>(text.data()), text.size());
}

// Incrementally parses server->client frames out of `buf`, consuming
// complete frames from the front and leaving any trailing partial frame in
// place for the next call - the same "buffer across calls" contract every
// TCP-backed decoder in this codebase follows (a frame can span more than
// one recv()).
inline std::optional<Frame> tryParseFrame(std::vector<uint8_t>& buf) {
    if (buf.size() < 2) return std::nullopt;
    uint8_t b0 = buf[0], b1 = buf[1];
    bool fin = (b0 & 0x80) != 0;
    auto opcode = static_cast<Opcode>(b0 & 0x0F);
    bool masked = (b1 & 0x80) != 0; // servers must NOT mask; tolerate it defensively anyway
    uint64_t len = b1 & 0x7F;
    size_t pos = 2;

    if (len == 126) {
        if (buf.size() < pos + 2) return std::nullopt;
        len = (static_cast<uint64_t>(buf[pos]) << 8) | buf[pos + 1];
        pos += 2;
    } else if (len == 127) {
        if (buf.size() < pos + 8) return std::nullopt;
        len = 0;
        for (int i = 0; i < 8; ++i) len = (len << 8) | buf[pos + i];
        pos += 8;
    }

    uint8_t maskKey[4] = {0, 0, 0, 0};
    if (masked) {
        if (buf.size() < pos + 4) return std::nullopt;
        for (int i = 0; i < 4; ++i) maskKey[i] = buf[pos + i];
        pos += 4;
    }

    if (buf.size() < pos + len) return std::nullopt; // incomplete - wait for more bytes

    Frame f;
    f.fin = fin;
    f.opcode = opcode;
    f.payload.assign(buf.begin() + static_cast<long>(pos), buf.begin() + static_cast<long>(pos + len));
    if (masked) {
        for (size_t i = 0; i < f.payload.size(); ++i) f.payload[i] ^= maskKey[i % 4];
    }
    buf.erase(buf.begin(), buf.begin() + static_cast<long>(pos + len));
    return f;
}

} // namespace fh::ws
