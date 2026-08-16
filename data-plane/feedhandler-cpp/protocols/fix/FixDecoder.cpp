#include "FixDecoder.hpp"
#include "FixMessage.hpp"
#include <cstdlib>

namespace fh {

void FixDecoder::decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) {
    buf_.insert(buf_.end(), data, data + length);

    size_t pos = 0;
    while (pos < buf_.size()) {
        fix::FixMessage msg;
        size_t consumed = fix::parseOne(buf_.data() + pos, buf_.size() - pos, msg);
        if (consumed == 0) break; // incomplete message - wait for more bytes

        DecodedMessage out;
        out.receiveTimestampNs = receiveTimestampNs;
        std::string msgType = msg.get(fix::Tag::MsgType);
        out.msgType = msgType.empty() ? 0 : msgType[0];
        std::string seqStr = msg.get(fix::Tag::MsgSeqNum);
        out.sequence = seqStr.empty() ? 0 : static_cast<uint64_t>(std::strtoull(seqStr.c_str(), nullptr, 10));
        out.payload.assign(buf_.begin() + static_cast<long>(pos), buf_.begin() + static_cast<long>(pos + consumed));
        sink.onMessage(out);

        pos += consumed;
    }
    buf_.erase(buf_.begin(), buf_.begin() + static_cast<long>(pos));
}

} // namespace fh
