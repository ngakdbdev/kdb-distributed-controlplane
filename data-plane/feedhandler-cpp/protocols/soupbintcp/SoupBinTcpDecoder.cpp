#include "SoupBinTcpDecoder.hpp"

namespace fh {

namespace {
uint16_t readU16(const uint8_t* p) {
    return static_cast<uint16_t>((static_cast<uint16_t>(p[0]) << 8) | p[1]);
}
} // namespace

void SoupBinTcpDecoder::decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) {
    buf_.insert(buf_.end(), data, data + length);

    size_t pos = 0;
    while (pos + 2 <= buf_.size()) {
        uint16_t packetLen = readU16(buf_.data() + pos); // length of type byte + payload, NOT including this field
        if (pos + 2 + packetLen > buf_.size()) break;     // incomplete packet - wait for more bytes

        char packetType = static_cast<char>(buf_[pos + 2]);
        const uint8_t* payload = buf_.data() + pos + 3;
        size_t payloadLen = packetLen >= 1 ? packetLen - 1 : 0;

        switch (packetType) {
            case 'A': loginAccepted_ = true; break;
            case 'J': loginRejected_ = true; break;
            case 'O': sessionEnded_ = true; break;
            case 'H': {
                DecodedMessage msg;
                msg.msgType = 'H';
                msg.sequence = 0;
                msg.receiveTimestampNs = receiveTimestampNs;
                sink.onMessage(msg);
                break;
            }
            case 'S': {
                DecodedMessage msg;
                msg.sequence = nextSequence_++;
                msg.receiveTimestampNs = receiveTimestampNs;
                msg.msgType = payloadLen > 0 ? static_cast<char>(payload[0]) : 0;
                msg.payload.assign(payload, payload + payloadLen);
                sink.onMessage(msg);
                break;
            }
            case 'U': {
                DecodedMessage msg;
                msg.sequence = 0; // unsequenced data doesn't consume/advance the session sequence
                msg.receiveTimestampNs = receiveTimestampNs;
                msg.msgType = payloadLen > 0 ? static_cast<char>(payload[0]) : 0;
                msg.payload.assign(payload, payload + payloadLen);
                sink.onMessage(msg);
                break;
            }
            default:
                break; // unrecognized packet type - skip, framing still lets us find the next packet
        }

        pos += 2 + packetLen;
    }
    buf_.erase(buf_.begin(), buf_.begin() + static_cast<long>(pos));
}

} // namespace fh
