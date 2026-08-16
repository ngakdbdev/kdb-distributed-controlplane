#include "SequencedSbePacketDecoder.hpp"

namespace fh {

namespace {
uint64_t readBE(const uint8_t* p, uint16_t size) {
    uint64_t v = 0;
    for (uint16_t i = 0; i < size; ++i) v = (v << 8) | p[i];
    return v;
}
uint16_t readU16LE(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
} // namespace

void SequencedSbePacketDecoder::decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) {
    if (length < layout_.headerLength) return;
    if (layout_.sequenceOffset + layout_.sequenceSize > layout_.headerLength) return;

    uint64_t baseSequence = readBE(data + layout_.sequenceOffset, layout_.sequenceSize);

    size_t pos = layout_.headerLength;
    uint32_t msgIndex = 0;
    while (pos + sbe::MESSAGE_HEADER_LEN <= length) {
        uint16_t blockLength = readU16LE(data + pos); // SBE message header's own blockLength field
        size_t msgTotal = sbe::MESSAGE_HEADER_LEN + blockLength;
        if (pos + msgTotal > length) break; // truncated - stop at whatever fully fits

        DecodedMessage msg;
        msg.sequence = baseSequence + msgIndex;
        msg.receiveTimestampNs = receiveTimestampNs;
        msg.msgType = 0; // meaningless at this layer - the venue adapter reads templateId via SbeDecoder
        msg.payload.assign(data + pos, data + pos + msgTotal);
        sink.onMessage(msg);

        pos += msgTotal;
        ++msgIndex;
    }
}

} // namespace fh
