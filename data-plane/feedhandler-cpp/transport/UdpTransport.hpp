// UdpTransport.hpp - real UDP unicast/multicast receiver. This is the
// transport a direct exchange feed (NASDAQ TotalView-ITCH, CME MDP3) uses
// in production - both are UDP multicast per their public specs. Runs its
// own receive thread; each datagram is handed to the registered handler as
// one "packet" (MoldUDP64 framing on top decides how many logical messages
// live inside it - this layer doesn't know or care).
#pragma once

#include <atomic>
#include <string>
#include <thread>
#include "../core/Transport.hpp"

namespace fh {

struct UdpTransportConfig {
    std::string bindInterface;   // local interface IP to bind (empty = INADDR_ANY)
    std::string multicastGroup;  // empty = unicast; set = join this multicast group
    uint16_t port = 0;
    int recvBufferBytes = 8 * 1024 * 1024; // exchange multicast feeds are bursty - generous SO_RCVBUF
};

class UdpTransport : public ITransport {
public:
    explicit UdpTransport(UdpTransportConfig cfg) : cfg_(std::move(cfg)) {}
    ~UdpTransport() override { stop(); }

    void setHandler(RawPacketHandler handler) override { handler_ = std::move(handler); }
    void start() override;
    void stop() override;
    bool isRunning() const override { return running_; }
    std::string describe() const override;

    // last error from start(), if it returned having failed to bind/join -
    // checked by the caller instead of throwing, so a misconfigured feed
    // (bad multicast group, interface not present) reports cleanly through
    // the engine's own status/health surface instead of an uncaught
    // exception taking the whole process down.
    const std::string& lastError() const { return lastError_; }

private:
    void run();

    UdpTransportConfig cfg_;
    RawPacketHandler handler_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    int sock_ = -1;
    std::string lastError_;
};

} // namespace fh
