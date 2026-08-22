// Transport.hpp - the transport layer interface. A transport's only job is
// handing raw bytes to a callback as they arrive - it has no idea what
// protocol is encoded inside them (that's the decoder's job, one layer up).
// This is what lets the SAME MoldUDP64 decoder run whether the packets come
// from a real multicast socket or a simulated in-process generator feeding
// synthetic packets for testing (see sim/) - the decoder never links against
// a socket.
#pragma once

#include <cstdint>
#include <functional>
#include <string>

namespace fh {

// (data, length, receiveTimestampNs) - the transport stamps receive time as
// close to the wire as it reasonably can, since that's what latency
// measurement (decode/book/publish latency) is measured FROM.
using RawPacketHandler = std::function<void(const uint8_t* data, size_t length, uint64_t receiveTimestampNs)>;

class ITransport {
public:
    virtual ~ITransport() = default;
    virtual void setHandler(RawPacketHandler handler) = 0;
    virtual void start() = 0;
    virtual void stop() = 0;
    virtual bool isRunning() const = 0;
    virtual std::string describe() const = 0;
};

uint64_t nowNs();

} // namespace fh
