// SoupBinTcpDecoder.hpp - Nasdaq's SoupBinTCP session-framing protocol (used
// by OUCH, and by some venues' ITCH-over-TCP deployments - Borsa Istanbul
// publishes ITCH/OUCH over exactly this framing per its public technical
// resources). Unlike MoldUDP64, sequence numbers aren't carried on the
// wire per-packet - the session just counts "Sequenced Data" packets
// received since login, starting from whatever sequence number was
// requested at logon. TCP means a message can arrive split across
// recv() calls, so this decoder buffers internally across decode() calls
// (the ONE decoder in this codebase that genuinely needs to, alongside
// FIX) rather than assuming one call = one complete packet.
//
// Wire format (all big-endian), per Nasdaq's public SoupBinTCP 4.0 spec:
//   Packet Length   2 bytes  uint16 - length of everything AFTER this field
//   Packet Type     1 byte   'S'=Sequenced Data 'U'=Unsequenced Data
//                            'H'=Server Heartbeat 'A'=Login Accepted
//                            'J'=Login Rejected   'O'=Logout (session end)
//   Payload         (Packet Length - 1) bytes
#pragma once

#include <cstdint>
#include <vector>
#include "../../core/ProtocolDecoder.hpp"

namespace fh {

class SoupBinTcpDecoder : public IProtocolDecoder {
public:
    explicit SoupBinTcpDecoder(uint64_t startSequence = 1) : nextSequence_(startSequence) {}

    void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) override;
    std::string name() const override { return "soupbintcp"; }

    bool loginAccepted() const { return loginAccepted_; }
    bool loginRejected() const { return loginRejected_; }
    bool sessionEnded() const { return sessionEnded_; }

private:
    std::vector<uint8_t> buf_;
    uint64_t nextSequence_;
    bool loginAccepted_ = false;
    bool loginRejected_ = false;
    bool sessionEnded_ = false;
};

} // namespace fh
