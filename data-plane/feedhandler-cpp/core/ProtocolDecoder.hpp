// ProtocolDecoder.hpp - decodes raw bytes from a transport into
// protocol-level messages, pushed to a MessageSink. Deliberately does NOT
// produce MarketEvents directly - a decoder knows "this is an ITCH Add
// Order message with these fields," not "this is instrument AAPL on
// NASDAQ" (that mapping is the VenueAdapter's job, one layer up). This
// split is what lets MoldUDP64+ITCH be reused unchanged across every venue
// that happens to publish ITCH-shaped messages, not just one exchange.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace fh {

// A decoded protocol message, still venue-agnostic: a raw type tag +
// opaque payload bytes (the decoder's own wire format minus session/
// framing overhead) plus whatever the protocol itself carries as a
// sequence number. The VenueAdapter interprets `payload` according to
// `msgType` - a decoder doesn't need to know what any given type MEANS,
// only how to carve the byte stream into individual messages.
struct DecodedMessage {
    char msgType = 0;
    uint64_t sequence = 0;
    std::vector<uint8_t> payload;
    uint64_t receiveTimestampNs = 0;
};

class MessageSink {
public:
    virtual ~MessageSink() = default;
    virtual void onMessage(const DecodedMessage& msg) = 0;
};

class IProtocolDecoder {
public:
    virtual ~IProtocolDecoder() = default;
    // Feed raw transport bytes in; the decoder pushes zero or more
    // DecodedMessages to `sink` as it finds complete messages (a decoder
    // may buffer partial messages across calls - TCP-based protocols like
    // SoupBinTCP/FIX need this since a message can split across packets).
    virtual void decode(const uint8_t* data, size_t length, uint64_t receiveTimestampNs, MessageSink& sink) = 0;
    virtual std::string name() const = 0;
};

} // namespace fh
