// MoldUdp64Decoder.hpp - Nasdaq's MoldUDP64 session-framing protocol: the
// UDP packet wrapper carrying sequence numbers and message boundaries
// around an inner payload (ITCH, in Nasdaq's case, but the framing itself
// is payload-agnostic - other venues reuse MoldUDP64 to carry their own
// binary messages). Wire format (all big-endian), per Nasdaq's public
// MoldUDP64 spec:
//
//   Packet header (20 bytes):
//     Session          10 bytes  ASCII, space-padded
//     Sequence Number   8 bytes  uint64 - sequence of the FIRST message in this packet
//     Message Count     2 bytes  uint16 - 0xFFFF = heartbeat (no messages),
//                                          0x0000 = end-of-session marker,
//                                          else N inner messages follow
//   Then, repeated Message Count times:
//     Message Length     2 bytes  uint16
//     Message Data        N bytes  the inner protocol's own message bytes
//
// A packet can carry multiple messages; the header's sequence number is
// the FIRST message's sequence - message k within the packet (0-indexed)
// is sequence + k, per spec. This decoder emits one DecodedMessage per
// inner message with that per-message sequence already resolved, so
// SequenceTracker upstream never needs to know packets can batch several
// messages together.
#pragma once

#include "../../core/ProtocolDecoder.hpp"

namespace fh {

class MoldUdp64Decoder : public IProtocolDecoder {
public:
    void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) override;
    std::string name() const override { return "moldudp64"; }

    // Diagnostics, useful for tests and the status surface: true if the
    // most recent packet was a heartbeat (Message Count == 0xFFFF) or
    // end-of-session (== 0x0000) rather than carrying real messages.
    bool lastWasHeartbeat() const { return lastWasHeartbeat_; }
    bool lastWasEndOfSession() const { return lastWasEndOfSession_; }

private:
    bool lastWasHeartbeat_ = false;
    bool lastWasEndOfSession_ = false;
};

} // namespace fh
