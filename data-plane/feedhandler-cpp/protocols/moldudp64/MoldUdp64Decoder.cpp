#include "MoldUdp64Decoder.hpp"

namespace fh {

namespace {
uint16_t readU16(const uint8_t* p) {
    return static_cast<uint16_t>((static_cast<uint16_t>(p[0]) << 8) | p[1]);
}
uint64_t readU64(const uint8_t* p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v = (v << 8) | p[i];
    return v;
}
} // namespace

void MoldUdp64Decoder::decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) {
    lastWasHeartbeat_ = false;
    lastWasEndOfSession_ = false;

    constexpr size_t HEADER_LEN = 20; // 10 session + 8 sequence + 2 message-count
    if (length < HEADER_LEN) return; // malformed/truncated packet - nothing decodable

    uint64_t baseSequence = readU64(data + 10);
    uint16_t messageCount = readU16(data + 18);

    if (messageCount == 0xFFFF) { lastWasHeartbeat_ = true; return; }
    if (messageCount == 0x0000) { lastWasEndOfSession_ = true; return; }

    size_t pos = HEADER_LEN;
    for (uint16_t i = 0; i < messageCount; ++i) {
        if (pos + 2 > length) break; // truncated - stop at whatever we could parse
        uint16_t msgLen = readU16(data + pos);
        pos += 2;
        if (pos + msgLen > length) break;

        DecodedMessage msg;
        msg.sequence = baseSequence + i;
        msg.receiveTimestampNs = receiveTimestampNs;
        msg.msgType = msgLen > 0 ? static_cast<char>(data[pos]) : 0; // inner protocol's own type byte, if it has one (ITCH does)
        msg.payload.assign(data + pos, data + pos + msgLen);
        sink.onMessage(msg);

        pos += msgLen;
    }
}

} // namespace fh
