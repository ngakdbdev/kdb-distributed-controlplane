#include "WsJsonDecoder.hpp"
#include "../../core/Json.hpp"

namespace fh {

namespace {
void emit(const uint8_t* data, size_t length, uint64_t ts, uint64_t seq, MessageSink& sink) {
    DecodedMessage msg;
    msg.sequence = seq;
    msg.receiveTimestampNs = ts;
    msg.msgType = 'J'; // "JSON" - venue adapter re-parses payload to determine the actual event kind
    msg.payload.assign(data, data + length);
    sink.onMessage(msg);
}
} // namespace

void WsJsonDecoder::decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) {
    json::Value root;
    std::string text(reinterpret_cast<const char*>(data), length);
    if (!json::parse(text, root)) return; // malformed JSON - drop silently, same as any other bad packet

    // A vendor-batched top-level array (some feeds send several updates
    // per WS message) is passed through as ONE DecodedMessage carrying the
    // whole array - the venue adapter re-parses and iterates
    // root.arrayValue itself, since re-slicing per-element source spans
    // here would need span tracking the parser doesn't do and buys
    // nothing a single re-parse one layer up doesn't already give for free.
    emit(data, length, receiveTimestampNs, 0, sink);
}

} // namespace fh
