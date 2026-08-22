// FixDecoder.hpp - wraps FixMessage::parseOne with the cross-call buffering
// every TCP-backed decoder in this codebase needs (a FIX message can split
// across recv() calls same as SoupBinTCP). Sequence comes from FIX's own
// MsgSeqNum (tag 34) - unlike SoupBinTCP, FIX puts its sequence number ON
// the wire, so this decoder reads it rather than counting locally.
#pragma once

#include <vector>
#include "../../core/ProtocolDecoder.hpp"

namespace fh {

class FixDecoder : public IProtocolDecoder {
public:
    void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) override;
    std::string name() const override { return "fix"; }

private:
    std::vector<uint8_t> buf_;
};

} // namespace fh
