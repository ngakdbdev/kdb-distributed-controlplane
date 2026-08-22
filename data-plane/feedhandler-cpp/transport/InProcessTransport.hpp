// InProcessTransport.hpp - no socket at all: bytes are handed directly to
// the registered handler via feed(). This is what makes "simulation
// workflow for dummy data" possible without touching a single real socket -
// sim/ generators call feed() with synthetically-built protocol packets,
// which flow through the EXACT SAME decoder/adapter/publisher code a real
// UdpTransport or TcpTransport would drive. Swapping this for a real
// transport later is a one-line config change (transport.type), not a
// rewrite - that's the whole point of the transport being an interface.
#pragma once

#include <atomic>
#include "../core/Transport.hpp"

namespace fh {

class InProcessTransport : public ITransport {
public:
    explicit InProcessTransport(std::string label = "in-process") : label_(std::move(label)) {}

    void setHandler(RawPacketHandler handler) override { handler_ = std::move(handler); }
    void start() override { running_ = true; }
    void stop() override { running_ = false; }
    bool isRunning() const override { return running_; }
    std::string describe() const override { return "in-process(" + label_ + ")"; }

    // The simulator's entry point: push one "packet" worth of bytes as if
    // it had just arrived off the wire. No-op if not started or no handler
    // is registered yet, matching how a real transport drops bytes that
    // arrive before setHandler()/start() - not a special case.
    void feed(const uint8_t* data, size_t length) {
        if (!running_ || !handler_) return;
        handler_(data, length, nowNs());
    }
    void feed(const std::vector<uint8_t>& bytes) { feed(bytes.data(), bytes.size()); }

private:
    std::string label_;
    RawPacketHandler handler_;
    std::atomic<bool> running_{false};
};

} // namespace fh
