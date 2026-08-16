// SequencedSbePacketDecoder.hpp - the outer UDP packet framing an SBE-based
// venue feed puts around one or more SBE messages, providing the packet-
// level sequence number SBE itself doesn't carry (SBE only frames
// individual MESSAGES - see SbeSchema.hpp's header comment). This is
// venue-specific by nature (real MDP3 packet headers are CME's own spec),
// so the header layout here is CONFIGURABLE and documented as an
// illustrative example matching the general shape (leading sequence
// number + sending time, then one or more back-to-back SBE messages) -
// see SbeSchema.hpp's header comment on sourcing the real schema/header
// layout from the venue before using this against a live feed.
#pragma once

#include "../../core/ProtocolDecoder.hpp"
#include "SbeSchema.hpp"

namespace fh {

struct SequencedPacketHeaderLayout {
    uint16_t sequenceOffset = 0;
    uint16_t sequenceSize = 4;    // bytes, big-endian
    uint16_t sendingTimeOffset = 4;
    uint16_t sendingTimeSize = 8; // bytes, big-endian, nanoseconds
    uint16_t headerLength = 12;   // total header bytes before the first SBE message begins
};

class SequencedSbePacketDecoder : public IProtocolDecoder {
public:
    explicit SequencedSbePacketDecoder(SequencedPacketHeaderLayout layout = {})
        : layout_(layout) {}

    void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) override;
    std::string name() const override { return "sequenced_sbe_packet"; }

private:
    SequencedPacketHeaderLayout layout_;
};

} // namespace fh
