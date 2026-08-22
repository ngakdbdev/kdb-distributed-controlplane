// WsJsonDecoder.hpp - the generic decoder behind every WebSocket+JSON
// vendor feed (Coinbase, Binance, Kraken, and any future JSON-over-WS
// provider) - see design section 1 "Modern streaming" and section 15's
// point that adding a venue should mean "write a config," not "write a
// decoder." One WsJsonDecoder + one GenericWsJsonVenueAdapter (see
// venues/) handles all of them; what differs per provider is only the
// FeedConfig field-name mapping (which JSON key is price, which is
// symbol, ...), not code.
//
// A WebSocketTransport message is already one complete JSON document (see
// WebSocketTransport's frame reassembly) - unlike the TCP-framed
// protocols in this codebase, there's no cross-call buffering to do here.
// This decoder's only real job is producing one DecodedMessage per JSON
// object, splitting a top-level JSON ARRAY (some vendors batch multiple
// updates per WS message) into one DecodedMessage per element.
#pragma once

#include "../../core/ProtocolDecoder.hpp"

namespace fh {

class WsJsonDecoder : public IProtocolDecoder {
public:
    void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) override;
    std::string name() const override { return "wsjson"; }
};

} // namespace fh
